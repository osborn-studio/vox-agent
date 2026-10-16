"""SQLite storage for VoxLog v2 — voice events + search.

Uses WAL mode for non-blocking writes.
Supports the new voice_event schema with session, tags, modes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog

from runtime.sqlite_policy import WINDOWS_SAFE_SQLITE_PRAGMAS
from runtime.models.events import (
    ArchiveStatus,
    ExportStatus,
    OutputMode,
    RecordingMode,
    TargetRiskLevel,
    VoiceEvent,
)

logger = structlog.get_logger()

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS voice_events (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    utterance_id TEXT DEFAULT '',
    output_id TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    session_type TEXT DEFAULT 'general',
    source TEXT DEFAULT 'desktop',
    env TEXT DEFAULT 'home',
    agent TEXT DEFAULT '',
    target_app TEXT DEFAULT '',
    target_risk_level TEXT DEFAULT 'low',
    raw_text TEXT NOT NULL DEFAULT '',
    display_text TEXT NOT NULL DEFAULT '',
    polished_text TEXT DEFAULT '',
    audio_duration_ms INTEGER DEFAULT 0,
    audio_file TEXT DEFAULT '',
    stt_provider TEXT DEFAULT '',
    stt_model TEXT DEFAULT '',
    llm_provider TEXT DEFAULT '',
    latency_stt_ms INTEGER DEFAULT 0,
    latency_total_fast_ms INTEGER DEFAULT 0,
    latency_total_slow_ms INTEGER DEFAULT 0,
    language_mix TEXT DEFAULT 'mixed',
    tags TEXT DEFAULT '[]',
    recording_mode TEXT DEFAULT 'normal',
    output_mode TEXT DEFAULT 'paste',
    confidence REAL DEFAULT 0.0,
    archive_status TEXT DEFAULT 'queued',
    export_status TEXT DEFAULT 'pending',
    role TEXT DEFAULT 'me'
);

CREATE VIRTUAL TABLE IF NOT EXISTS voice_events_fts USING fts5(
    event_id UNINDEXED,
    raw_text,
    display_text,
    polished_text,
    target_app,
    agent,
    tags
);

CREATE TABLE IF NOT EXISTS memory_digests (
    id TEXT PRIMARY KEY,
    digest_type TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    digest_date TEXT DEFAULT '',
    project_key TEXT DEFAULT '',
    source_event_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT DEFAULT '',
    intent TEXT DEFAULT '',
    suggested_tags TEXT DEFAULT '[]',
    mentioned_entities TEXT DEFAULT '[]',
    enhanced INTEGER DEFAULT 0,
    enhancer_provider TEXT DEFAULT ''
);
"""

INDEXES_V2 = """
CREATE INDEX IF NOT EXISTS idx_ve_created ON voice_events(created_at);
CREATE INDEX IF NOT EXISTS idx_ve_agent ON voice_events(agent);
CREATE INDEX IF NOT EXISTS idx_ve_session ON voice_events(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_session_type ON memory_digests(session_id)
  WHERE digest_type='session_digest' AND session_id<>'';
CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_date_type ON memory_digests(digest_date)
  WHERE digest_type='daily_digest' AND digest_date<>'';
CREATE UNIQUE INDEX IF NOT EXISTS idx_digest_project_type ON memory_digests(project_key)
  WHERE digest_type='project_digest' AND project_key<>'';
"""

ALTERS_V2 = [
    "ALTER TABLE voice_events ADD COLUMN utterance_id TEXT DEFAULT ''",
    "ALTER TABLE voice_events ADD COLUMN output_id TEXT DEFAULT ''",
    "ALTER TABLE voice_events ADD COLUMN target_risk_level TEXT DEFAULT 'low'",
    "ALTER TABLE voice_events ADD COLUMN audio_file TEXT DEFAULT ''",
    "ALTER TABLE voice_events ADD COLUMN stt_model TEXT DEFAULT ''",
    "ALTER TABLE voice_events ADD COLUMN output_mode TEXT DEFAULT 'paste'",
    "ALTER TABLE voice_events ADD COLUMN confidence REAL DEFAULT 0.0",
    "ALTER TABLE memory_digests ADD COLUMN digest_date TEXT DEFAULT ''",
    "ALTER TABLE memory_digests ADD COLUMN project_key TEXT DEFAULT ''",
    "ALTER TABLE memory_digests ADD COLUMN enhanced INTEGER DEFAULT 0",
    "ALTER TABLE memory_digests ADD COLUMN enhancer_provider TEXT DEFAULT ''",
]

# Migration: copy old voice_log data to new voice_events table
MIGRATE_V1 = """
INSERT OR IGNORE INTO voice_events (id, created_at, source, env, agent, target_app,
    raw_text, display_text, polished_text, stt_provider, llm_provider,
    latency_total_fast_ms, role, archive_status, recording_mode)
SELECT id, created_at, 'desktop', env, COALESCE(agent, ''), target_app,
    raw_text, polished_text, polished_text, asr_provider, llm_provider,
    latency_ms, CASE WHEN target_app LIKE '%paste%' THEN 'other' ELSE 'me' END,
    'polished', 'normal'
FROM voice_log
WHERE id NOT IN (SELECT id FROM voice_events)
"""


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path), timeout=5)
        for statement in WINDOWS_SAFE_SQLITE_PRAGMAS:
            await self._db.execute(statement)
        await self._db.executescript(SCHEMA_V2)
        await self._apply_alters()
        await self._db.commit()
        await self._ensure_digest_schema()
        await self._apply_indexes()
        await self._rebuild_fts()

        # Migrate v1 data if old table exists
        try:
            cursor = await self._db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voice_log'")
            if await cursor.fetchone():
                await self._db.executescript(MIGRATE_V1)
                logger.info("store.migrated_v1")
        except Exception as e:
            logger.warning("store.migrate_fail", error=str(e)[:100])

        await self._db.commit()
        count = await self.count()
        logger.info("store.init", db=str(self.db_path), records=count)

    async def _apply_alters(self) -> None:
        assert self._db
        for stmt in ALTERS_V2:
            try:
                await self._db.execute(stmt)
            except Exception:
                # Existing databases may already have these columns.
                pass

    async def _table_columns(self, table_name: str) -> set[str]:
        assert self._db
        cursor = await self._db.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        return {str(row[1]) for row in rows}

    async def _ensure_digest_schema(self) -> None:
        assert self._db
        digest_columns = await self._table_columns("memory_digests")
        missing_columns = {
            "digest_date": "TEXT DEFAULT ''",
            "project_key": "TEXT DEFAULT ''",
            "enhanced": "INTEGER DEFAULT 0",
            "enhancer_provider": "TEXT DEFAULT ''",
        }
        for column_name, column_spec in missing_columns.items():
            if column_name in digest_columns:
                continue
            await self._db.execute(
                f"ALTER TABLE memory_digests ADD COLUMN {column_name} {column_spec}"
            )
        await self._db.commit()

    async def _apply_indexes(self) -> None:
        assert self._db
        voice_indexes = [
            ("idx_ve_created", "CREATE INDEX IF NOT EXISTS idx_ve_created ON voice_events(created_at)"),
            ("idx_ve_agent", "CREATE INDEX IF NOT EXISTS idx_ve_agent ON voice_events(agent)"),
            ("idx_ve_session", "CREATE INDEX IF NOT EXISTS idx_ve_session ON voice_events(session_id)"),
        ]
        for _, statement in voice_indexes:
            await self._db.execute(statement)

        digest_columns = await self._table_columns("memory_digests")
        digest_indexes = [
            (
                "idx_digest_session_type",
                {"session_id", "digest_type"},
                """CREATE UNIQUE INDEX idx_digest_session_type
                   ON memory_digests(session_id)
                   WHERE digest_type='session_digest' AND session_id<>''""",
            ),
            (
                "idx_digest_date_type",
                {"digest_date", "digest_type"},
                """CREATE UNIQUE INDEX idx_digest_date_type
                   ON memory_digests(digest_date)
                   WHERE digest_type='daily_digest' AND digest_date<>''""",
            ),
            (
                "idx_digest_project_type",
                {"project_key", "digest_type"},
                """CREATE UNIQUE INDEX idx_digest_project_type
                   ON memory_digests(project_key)
                   WHERE digest_type='project_digest' AND project_key<>''""",
            ),
        ]
        for index_name, required_columns, statement in digest_indexes:
            if not required_columns.issubset(digest_columns):
                continue
            # Older schemas indexed every digest type on fields that were empty
            # for unrelated types. A second session/project could therefore
            # collide on the empty date/project/session field. Recreate these
            # as type-specific partial indexes on every startup; this is
            # idempotent and upgrades existing databases in place.
            await self._db.execute(f"DROP INDEX IF EXISTS {index_name}")
            await self._db.execute(statement)
        await self._db.commit()

    async def _rebuild_fts(self) -> None:
        assert self._db
        await self._db.execute("DROP TABLE IF EXISTS voice_events_fts")
        await self._db.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS voice_events_fts USING fts5(
                event_id UNINDEXED,
                raw_text,
                display_text,
                polished_text,
                target_app,
                agent,
                tags
            )"""
        )
        await self._db.execute(
            """INSERT INTO voice_events_fts (rowid, event_id, raw_text, display_text, polished_text, target_app, agent, tags)
               SELECT rowid, id, raw_text, display_text, polished_text, target_app, agent, tags
               FROM voice_events"""
        )

    async def _sync_fts_for_event(self, event_id: str) -> None:
        assert self._db
        await self._db.execute("DELETE FROM voice_events_fts WHERE event_id=?", (event_id,))
        await self._db.execute(
            """INSERT INTO voice_events_fts (rowid, event_id, raw_text, display_text, polished_text, target_app, agent, tags)
               SELECT rowid, id, raw_text, display_text, polished_text, target_app, agent, tags
               FROM voice_events WHERE id=?""",
            (event_id,),
        )

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def save_event(self, event: VoiceEvent) -> None:
        assert self._db
        import json
        await self._db.execute(
            """INSERT OR REPLACE INTO voice_events
               (id, created_at, utterance_id, output_id, session_id, session_type, source, env,
                agent, target_app, target_risk_level, raw_text, display_text, polished_text,
                audio_duration_ms, audio_file, stt_provider, stt_model, llm_provider,
                latency_stt_ms, latency_total_fast_ms, latency_total_slow_ms, language_mix, tags,
                recording_mode, output_mode, confidence, archive_status, export_status, role)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event.id, event.created_at.isoformat(), event.utterance_id, event.output_id, event.session_id,
             event.session_type.value, event.source, event.env, event.agent,
             event.target_app, event.target_risk_level.value, event.raw_text, event.display_text,
             event.polished_text, event.audio_duration_ms, event.audio_file, event.stt_provider,
             event.stt_model, event.llm_provider,
             event.latency_stt_ms, event.latency_total_fast_ms, event.latency_total_slow_ms,
             event.language_mix, json.dumps(event.tags), event.recording_mode.value,
             event.output_mode.value, event.confidence, event.archive_status.value,
             event.export_status.value, event.role),
        )
        await self._sync_fts_for_event(event.id)
        await self._db.commit()

    async def update_polish(self, event_id: str, polished_text: str) -> None:
        assert self._db
        await self._db.execute(
            "UPDATE voice_events SET polished_text=?, archive_status='polished' WHERE id=?",
            (polished_text, event_id),
        )
        await self._sync_fts_for_event(event_id)
        await self._db.commit()

    async def update_mode(self, event_id: str, mode: RecordingMode) -> None:
        assert self._db
        export_status = ExportStatus.PENDING if mode == RecordingMode.NORMAL else ExportStatus.SKIP
        await self._db.execute(
            "UPDATE voice_events SET recording_mode=?, export_status=? WHERE id=?",
            (mode.value, export_status.value, event_id),
        )
        await self._db.commit()

    async def delete_event(self, event_id: str) -> bool:
        assert self._db
        cursor = await self._db.execute(
            "SELECT created_at FROM voice_events WHERE id=?", (event_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        created = datetime.fromisoformat(row[0])
        if (datetime.now(timezone.utc) - created).total_seconds() > 120:
            return False  # 2 minute recall window
        await self._db.execute("DELETE FROM voice_events_fts WHERE event_id=?", (event_id,))
        await self._db.execute("DELETE FROM voice_events WHERE id=?", (event_id,))
        await self._db.commit()
        return True

    async def list_by_agent(self, agent: str, limit: int = 200) -> list[dict]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, created_at, utterance_id, output_id, session_id, raw_text, display_text, polished_text,
                      stt_provider, stt_model, llm_provider, latency_total_fast_ms,
                      target_app, target_risk_level, role, recording_mode, output_mode, confidence, archive_status, agent
               FROM voice_events WHERE agent=? AND recording_mode != 'ephemeral'
               ORDER BY created_at DESC LIMIT ?""",
            (agent, limit),
        )
        return [self._row_to_dict(r) for r in await cursor.fetchall()]

    async def list_portable_history(self, *, offset: int = 0, limit: int = 500) -> list[dict]:
        """Return a stable, bounded page for a secret-free migration bundle."""
        assert self._db
        if isinstance(offset, bool) or not 0 <= offset <= 50_000:
            raise ValueError("portable history offset must be between 0 and 50000")
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("portable history limit must be between 1 and 500")
        cursor = await self._db.execute(
            """SELECT id, created_at, utterance_id, output_id, session_id, raw_text, display_text, polished_text,
                      stt_provider, stt_model, llm_provider, latency_total_fast_ms,
                      target_app, target_risk_level, role, recording_mode, output_mode, confidence, archive_status, agent
               FROM voice_events
               WHERE recording_mode != 'ephemeral'
               ORDER BY created_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        return [self._row_to_dict(row) for row in await cursor.fetchall()]

    async def import_portable_history(self, records: list[dict]) -> dict[str, int]:
        """Insert portable events once, preserving any local record with the same ID."""
        assert self._db
        if len(records) > 200:
            raise ValueError("portable history import exceeds the 200 record batch limit")

        events = [self._portable_record_to_event(record) for record in records]
        imported = 0
        for event in events:
            import json

            cursor = await self._db.execute(
                """INSERT OR IGNORE INTO voice_events
                   (id, created_at, utterance_id, output_id, session_id, session_type, source, env,
                    agent, target_app, target_risk_level, raw_text, display_text, polished_text,
                    audio_duration_ms, audio_file, stt_provider, stt_model, llm_provider,
                    latency_stt_ms, latency_total_fast_ms, latency_total_slow_ms, language_mix, tags,
                    recording_mode, output_mode, confidence, archive_status, export_status, role)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.id,
                    event.created_at.isoformat(),
                    event.utterance_id,
                    event.output_id,
                    event.session_id,
                    event.session_type.value,
                    event.source,
                    event.env,
                    event.agent,
                    event.target_app,
                    event.target_risk_level.value,
                    event.raw_text,
                    event.display_text,
                    event.polished_text,
                    event.audio_duration_ms,
                    event.audio_file,
                    event.stt_provider,
                    event.stt_model,
                    event.llm_provider,
                    event.latency_stt_ms,
                    event.latency_total_fast_ms,
                    event.latency_total_slow_ms,
                    event.language_mix,
                    json.dumps(event.tags),
                    event.recording_mode.value,
                    event.output_mode.value,
                    event.confidence,
                    event.archive_status.value,
                    event.export_status.value,
                    event.role,
                ),
            )
            if cursor.rowcount == 1:
                imported += 1
                await self._sync_fts_for_event(event.id)
        await self._db.commit()
        return {"imported": imported, "skipped": len(events) - imported}

    async def list_agents(self) -> list[dict]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT agent, COUNT(*) as cnt, MAX(created_at) as last_ts
               FROM voice_events WHERE agent != '' AND recording_mode != 'ephemeral'
               GROUP BY agent ORDER BY last_ts DESC"""
        )
        return [{"agent": r[0], "count": r[1], "last_active": r[2]} for r in await cursor.fetchall()]

    async def search(self, query: str, limit: int = 50) -> list[dict]:
        assert self._db
        query = query.strip()
        if not query:
            cursor = await self._db.execute(
                """SELECT id, created_at, utterance_id, output_id, session_id, raw_text, display_text, polished_text,
                          stt_provider, stt_model, llm_provider, latency_total_fast_ms,
                          target_app, target_risk_level, role, recording_mode, output_mode, confidence, archive_status, agent
                   FROM voice_events
                   WHERE recording_mode != 'ephemeral'
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            return [self._row_to_dict(r) for r in await cursor.fetchall()]

        tokens = [token for token in query.replace('"', " ").split() if token]
        fts_query = " ".join(f"{token}*" for token in tokens) if tokens else query
        try:
            cursor = await self._db.execute(
                """SELECT ve.id, ve.created_at, ve.utterance_id, ve.output_id, ve.session_id, ve.raw_text, ve.display_text, ve.polished_text,
                          ve.stt_provider, ve.stt_model, ve.llm_provider, ve.latency_total_fast_ms,
                          ve.target_app, ve.target_risk_level, ve.role, ve.recording_mode, ve.output_mode, ve.confidence, ve.archive_status, ve.agent
                   FROM voice_events_fts fts
                   JOIN voice_events ve ON ve.id = fts.event_id
                   WHERE voice_events_fts MATCH ?
                     AND ve.recording_mode != 'ephemeral'
                   ORDER BY bm25(voice_events_fts), ve.created_at DESC
                   LIMIT ?""",
                (fts_query, limit),
            )
            rows = await cursor.fetchall()
            if rows:
                return [self._row_to_dict(r) for r in rows]
        except Exception:
            logger.warning("store.fts_search_fallback", query=query[:100])

        cursor = await self._db.execute(
            """SELECT id, created_at, utterance_id, output_id, session_id, raw_text, display_text, polished_text,
                      stt_provider, stt_model, llm_provider, latency_total_fast_ms,
                      target_app, target_risk_level, role, recording_mode, output_mode, confidence, archive_status, agent
               FROM voice_events
               WHERE (display_text LIKE ? OR raw_text LIKE ? OR polished_text LIKE ?)
                 AND recording_mode != 'ephemeral'
               ORDER BY created_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        )
        return [self._row_to_dict(r) for r in await cursor.fetchall()]

    async def count(self) -> int:
        assert self._db
        cursor = await self._db.execute("SELECT COUNT(*) FROM voice_events")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def export_markdown(self, date_str: str) -> str:
        assert self._db
        cursor = await self._db.execute(
            """SELECT created_at, display_text, polished_text, target_app, role, agent
               FROM voice_events WHERE created_at LIKE ? AND recording_mode='normal'
               ORDER BY created_at ASC""",
            (f"{date_str}%",),
        )
        rows = await cursor.fetchall()
        if not rows:
            return f"# Voice Log: {date_str}\n\nNo records.\n"

        lines = [f"# Voice Log: {date_str}\n", f"> {len(rows)} recordings\n"]
        for ts, display, polished, app, role, agent in rows:
            time_part = ts[11:19] if len(ts) > 19 else ts
            text = polished or display
            tag = f" [{app}]" if app else ""
            role_tag = "🎤" if role == "me" else "📋"
            lines.append(f"## {time_part}{tag} {role_tag}\n\n{text}\n")
        return "\n".join(lines)

    async def upsert_session_digest(
        self,
        session_id: str,
        source_event_id: str,
        summary: str,
        intent: str,
        suggested_tags: list[str],
        mentioned_entities: list[str],
        enhanced: bool = False,
        enhancer_provider: str = "heuristic",
    ) -> None:
        assert self._db
        import json
        digest_id = f"session:{session_id or source_event_id}"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO memory_digests
               (id, digest_type, session_id, digest_date, project_key, source_event_id, created_at, updated_at, summary, intent, suggested_tags, mentioned_entities, enhanced, enhancer_provider)
               VALUES (?, 'session_digest', ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 session_id=excluded.session_id,
                 source_event_id=excluded.source_event_id,
                 updated_at=excluded.updated_at,
                 summary=excluded.summary,
                 intent=excluded.intent,
                 suggested_tags=excluded.suggested_tags,
                 mentioned_entities=excluded.mentioned_entities,
                 enhanced=excluded.enhanced,
                 enhancer_provider=excluded.enhancer_provider""",
            (
                digest_id,
                session_id,
                source_event_id,
                now,
                now,
                summary,
                intent,
                json.dumps(suggested_tags),
                json.dumps(mentioned_entities),
                1 if enhanced else 0,
                enhancer_provider,
            ),
        )
        await self._db.commit()

    async def upsert_daily_digest(
        self,
        digest_date: str,
        source_event_id: str,
        summary: str,
        intent: str,
        suggested_tags: list[str],
        mentioned_entities: list[str],
        enhanced: bool = False,
        enhancer_provider: str = "heuristic",
    ) -> None:
        assert self._db
        import json
        digest_id = f"daily:{digest_date}"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO memory_digests
               (id, digest_type, session_id, digest_date, project_key, source_event_id, created_at, updated_at, summary, intent, suggested_tags, mentioned_entities, enhanced, enhancer_provider)
               VALUES (?, 'daily_digest', '', ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 digest_date=excluded.digest_date,
                 source_event_id=excluded.source_event_id,
                 updated_at=excluded.updated_at,
                 summary=excluded.summary,
                 intent=excluded.intent,
                 suggested_tags=excluded.suggested_tags,
                 mentioned_entities=excluded.mentioned_entities,
                 enhanced=excluded.enhanced,
                 enhancer_provider=excluded.enhancer_provider""",
            (
                digest_id,
                digest_date,
                source_event_id,
                now,
                now,
                summary,
                intent,
                json.dumps(suggested_tags),
                json.dumps(mentioned_entities),
                1 if enhanced else 0,
                enhancer_provider,
            ),
        )
        await self._db.commit()

    async def upsert_project_digest(
        self,
        project_key: str,
        source_event_id: str,
        summary: str,
        intent: str,
        suggested_tags: list[str],
        mentioned_entities: list[str],
        enhanced: bool = False,
        enhancer_provider: str = "heuristic",
    ) -> None:
        assert self._db
        import json
        digest_id = f"project:{project_key}"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO memory_digests
               (id, digest_type, session_id, digest_date, project_key, source_event_id, created_at, updated_at, summary, intent, suggested_tags, mentioned_entities, enhanced, enhancer_provider)
               VALUES (?, 'project_digest', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_key=excluded.project_key,
                 source_event_id=excluded.source_event_id,
                 updated_at=excluded.updated_at,
                 summary=excluded.summary,
                 intent=excluded.intent,
                 suggested_tags=excluded.suggested_tags,
                 mentioned_entities=excluded.mentioned_entities,
                 enhanced=excluded.enhanced,
                 enhancer_provider=excluded.enhancer_provider""",
            (
                digest_id,
                project_key,
                source_event_id,
                now,
                now,
                summary,
                intent,
                json.dumps(suggested_tags),
                json.dumps(mentioned_entities),
                1 if enhanced else 0,
                enhancer_provider,
            ),
        )
        await self._db.commit()

    async def get_session_digest(self, session_id: str) -> dict | None:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, digest_type, session_id, digest_date, project_key, source_event_id, created_at, updated_at, summary, intent, suggested_tags, mentioned_entities, enhanced, enhancer_provider
               FROM memory_digests WHERE session_id=? AND digest_type='session_digest'""",
            (session_id,),
        )
        row = await cursor.fetchone()
        return self._digest_row_to_dict(row)

    async def get_daily_digest(self, digest_date: str) -> dict | None:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, digest_type, session_id, digest_date, project_key, source_event_id, created_at, updated_at, summary, intent, suggested_tags, mentioned_entities, enhanced, enhancer_provider
               FROM memory_digests WHERE digest_date=? AND digest_type='daily_digest'""",
            (digest_date,),
        )
        row = await cursor.fetchone()
        return self._digest_row_to_dict(row)

    async def get_project_digest(self, project_key: str) -> dict | None:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, digest_type, session_id, digest_date, project_key, source_event_id, created_at, updated_at, summary, intent, suggested_tags, mentioned_entities, enhanced, enhancer_provider
               FROM memory_digests WHERE project_key=? AND digest_type='project_digest'""",
            (project_key,),
        )
        row = await cursor.fetchone()
        return self._digest_row_to_dict(row)

    async def list_events_for_date(self, digest_date: str, limit: int = 500) -> list[VoiceEvent]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, created_at, utterance_id, output_id, session_id, session_type, source, env,
                      agent, target_app, target_risk_level, raw_text, display_text, polished_text,
                      audio_duration_ms, audio_file, stt_provider, stt_model, llm_provider,
                      latency_stt_ms, latency_total_fast_ms, latency_total_slow_ms, language_mix, tags,
                      recording_mode, output_mode, confidence, archive_status, export_status, role
               FROM voice_events
               WHERE substr(created_at, 1, 10)=?
                 AND recording_mode != 'ephemeral'
               ORDER BY created_at ASC
               LIMIT ?""",
            (digest_date, limit),
        )
        rows = await cursor.fetchall()
        return [self._event_row_to_model(row) for row in rows]

    async def list_events_for_project(self, project_key: str, limit: int = 500) -> list[VoiceEvent]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, created_at, utterance_id, output_id, session_id, session_type, source, env,
                      agent, target_app, target_risk_level, raw_text, display_text, polished_text,
                      audio_duration_ms, audio_file, stt_provider, stt_model, llm_provider,
                      latency_stt_ms, latency_total_fast_ms, latency_total_slow_ms, language_mix, tags,
                      recording_mode, output_mode, confidence, archive_status, export_status, role
               FROM voice_events
               WHERE lower(target_app)=?
                 AND recording_mode != 'ephemeral'
               ORDER BY created_at ASC
               LIMIT ?""",
            (project_key.strip().lower(), limit),
        )
        rows = await cursor.fetchall()
        return [self._event_row_to_model(row) for row in rows]

    async def list_events_for_session(self, session_id: str, limit: int = 500) -> list[VoiceEvent]:
        assert self._db
        cursor = await self._db.execute(
            """SELECT id, created_at, utterance_id, output_id, session_id, session_type, source, env,
                      agent, target_app, target_risk_level, raw_text, display_text, polished_text,
                      audio_duration_ms, audio_file, stt_provider, stt_model, llm_provider,
                      latency_stt_ms, latency_total_fast_ms, latency_total_slow_ms, language_mix, tags,
                      recording_mode, output_mode, confidence, archive_status, export_status, role
               FROM voice_events
               WHERE session_id=?
                 AND recording_mode != 'ephemeral'
               ORDER BY created_at ASC
               LIMIT ?""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [self._event_row_to_model(row) for row in rows]

    @staticmethod
    def _digest_row_to_dict(row) -> dict | None:
        if not row:
            return None
        import json
        return {
            "id": row[0],
            "digest_type": row[1],
            "session_id": row[2],
            "digest_date": row[3],
            "project_key": row[4],
            "source_event_id": row[5],
            "created_at": row[6],
            "updated_at": row[7],
            "summary": row[8],
            "intent": row[9],
            "suggested_tags": json.loads(row[10]),
            "mentioned_entities": json.loads(row[11]),
            "enhanced": bool(row[12]),
            "enhancer_provider": row[13],
        }

    @staticmethod
    def _event_row_to_model(row) -> VoiceEvent:
        import json
        return VoiceEvent(
            id=row[0],
            created_at=datetime.fromisoformat(row[1]),
            utterance_id=row[2],
            output_id=row[3],
            session_id=row[4],
            session_type=row[5],
            source=row[6],
            env=row[7],
            agent=row[8],
            target_app=row[9],
            target_risk_level=row[10],
            raw_text=row[11],
            display_text=row[12],
            polished_text=row[13],
            audio_duration_ms=row[14],
            audio_file=row[15],
            stt_provider=row[16],
            stt_model=row[17],
            llm_provider=row[18],
            latency_stt_ms=row[19],
            latency_total_fast_ms=row[20],
            latency_total_slow_ms=row[21],
            language_mix=row[22],
            tags=json.loads(row[23]),
            recording_mode=row[24],
            output_mode=row[25],
            confidence=row[26],
            archive_status=row[27],
            export_status=row[28],
            role=row[29],
        )

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0], "created_at": row[1], "utterance_id": row[2], "output_id": row[3],
            "session_id": row[4], "raw_text": row[5], "display_text": row[6], "polished_text": row[7],
            "stt_provider": row[8], "stt_model": row[9], "llm_provider": row[10],
            "latency_ms": row[11], "target_app": row[12], "target_risk_level": row[13],
            "role": row[14], "recording_mode": row[15], "output_mode": row[16],
            "confidence": row[17], "archive_status": row[18], "agent": row[19],
        }

    @staticmethod
    def _portable_record_to_event(record: dict) -> VoiceEvent:
        if not isinstance(record, dict):
            raise ValueError("portable history record must be an object")
        allowed = {
            "id", "created_at", "utterance_id", "output_id", "session_id", "raw_text",
            "display_text", "polished_text", "stt_provider", "stt_model", "llm_provider",
            "latency_ms", "target_app", "target_risk_level", "role", "recording_mode",
            "output_mode", "confidence", "archive_status", "agent",
        }
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(f"portable history record has unknown fields: {sorted(unknown)}")

        def bounded_text(name: str, maximum: int, *, required: bool = False) -> str:
            value = record.get(name, "")
            if not isinstance(value, str):
                raise ValueError(f"portable history {name} must be text")
            if required and not value.strip():
                raise ValueError(f"portable history {name} is required")
            if len(value) > maximum:
                raise ValueError(f"portable history {name} exceeds {maximum} characters")
            return value

        event_id = bounded_text("id", 200, required=True)
        created_at = bounded_text("created_at", 80, required=True)
        raw_text = bounded_text("raw_text", 100_000)
        display_text = bounded_text("display_text", 100_000)
        polished_text = bounded_text("polished_text", 100_000)
        recording_mode = bounded_text("recording_mode", 20) or "normal"
        if recording_mode == RecordingMode.EPHEMERAL.value:
            raise ValueError("ephemeral history cannot be imported")
        if recording_mode not in {RecordingMode.NORMAL.value, RecordingMode.PRIVATE.value}:
            raise ValueError("portable history recording_mode is invalid")

        latency_ms = record.get("latency_ms", 0)
        confidence = record.get("confidence", 0.0)
        if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or not 0 <= latency_ms <= 3_600_000:
            raise ValueError("portable history latency_ms is invalid")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("portable history confidence is invalid")

        try:
            parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed_created_at.tzinfo is None:
                raise ValueError
            target_risk = TargetRiskLevel(bounded_text("target_risk_level", 20) or "low")
            output_mode = OutputMode(bounded_text("output_mode", 30) or "paste")
            archive_status = ArchiveStatus(bounded_text("archive_status", 30) or "raw_only")
        except ValueError as error:
            raise ValueError("portable history contains invalid enum or timestamp values") from error

        return VoiceEvent(
            id=event_id,
            created_at=parsed_created_at,
            utterance_id=bounded_text("utterance_id", 200),
            output_id=bounded_text("output_id", 200),
            session_id=bounded_text("session_id", 200),
            source="migration",
            env="imported",
            agent=bounded_text("agent", 200),
            target_app=bounded_text("target_app", 500),
            target_risk_level=target_risk,
            raw_text=raw_text,
            display_text=display_text,
            polished_text=polished_text,
            stt_provider=bounded_text("stt_provider", 200),
            stt_model=bounded_text("stt_model", 200),
            llm_provider=bounded_text("llm_provider", 200),
            latency_total_fast_ms=latency_ms,
            recording_mode=RecordingMode(recording_mode),
            output_mode=output_mode,
            confidence=float(confidence),
            archive_status=archive_status,
            export_status=(
                ExportStatus.PENDING
                if recording_mode == RecordingMode.NORMAL.value
                else ExportStatus.SKIP
            ),
            role=bounded_text("role", 40) or "me",
        )
