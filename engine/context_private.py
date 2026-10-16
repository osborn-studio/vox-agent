"""Opt-in owner-only Context journal; never touches the delivery outbox.

Append-only revisions and a current revision gate invalidate dependent local drafts.
No sharing, export, summary generation or automatic synchronization is implied.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import time
import threading
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .auth import Principal, require_principal
from .org_schema import ORG_DATABASE_NAME
from .context_inputs import decode_cursor, encode_cursor, parse_window

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
ProjectIdentifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Tag = Literal["decision", "progress", "blocker", "question", "verification"]


class PrivateInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    eventId: Identifier
    text: str = Field(min_length=1, max_length=8192)
    tags: list[Tag] = Field(default_factory=list, max_length=5)
    projectId: ProjectIdentifier | None = None
    expectedRepositoryId: str | None = Field(default=None, min_length=3, max_length=200)
    visibility: Literal["private"] = "private"

    @field_validator("expectedRepositoryId")
    @classmethod
    def repository_snapshot(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
            raise ValueError("invalid repository snapshot")
        return value

    @model_validator(mode="after")
    def paired_snapshot(self):
        if self.expectedRepositoryId is not None and self.projectId is None:
            raise ValueError("repository snapshot requires project")
        return self

    @field_validator("text")
    @classmethod
    def valid_unicode(cls, value):
        try:
            value.encode("utf-8")
            if "\x00" in value:
                raise ValueError("invalid Unicode")
        except UnicodeEncodeError:
            raise ValueError("invalid Unicode") from None
        return value

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate tags")
        return value


def valid_request_id(value):
    if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid request id")
    return value


class PrivateEdit(BaseModel):
    requestId: Identifier | None = None
    _request = field_validator("requestId")(valid_request_id)
    model_config = ConfigDict(strict=True, extra="forbid")
    expectedRevision: int = Field(ge=1, le=1_000_000)
    text: str = Field(min_length=1, max_length=8192)
    tags: list[Tag] = Field(default_factory=list, max_length=5)

    _unique = field_validator("tags")(PrivateInput.unique_tags.__func__)
    _unicode = field_validator("text")(PrivateInput.valid_unicode.__func__)


class PrivateSearch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    start: str = Field(min_length=1, max_length=80)
    end: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=256)
    q: str | None = Field(default=None, min_length=1, max_length=128)
    tag: Tag | None = None
    projectId: ProjectIdentifier | None = None

    @field_validator("q")
    @classmethod
    def safe_query(cls, value):
        return PrivateInput.valid_unicode(value) if value is not None else value


class Withdrawal(BaseModel):
    requestId: Identifier | None = None
    _request = field_validator("requestId")(valid_request_id)
    model_config = ConfigDict(strict=True, extra="forbid")
    expectedRevision: int = Field(ge=1, le=1_000_000)


class SourceReference(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: Identifier
    revision: int = Field(ge=1, le=1_000_000)


class ReferenceCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    sources: list[SourceReference] = Field(min_length=1, max_length=100)

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, value):
        if len({source.id for source in value}) != len(value):
            raise ValueError("duplicate references")
        return value


class PrivateContextJournal:
    """Owned by BackupAppContext; same private DB/file backup, separate tables."""
    def __init__(self, path):
        self.path = path.resolve()
        self.auth_lock = threading.RLock()
        with self.connection(write=True) as db:
            had_actor_budget = db.execute("SELECT 1 FROM sqlite_master WHERE name='private_context_actor_budget_v1'").fetchone() is not None
            schema = """
                CREATE TABLE IF NOT EXISTS private_context_v1 (
                    id TEXT PRIMARY KEY, org TEXT NOT NULL, actor TEXT NOT NULL,
                    event TEXT NOT NULL, fingerprint TEXT NOT NULL, created REAL NOT NULL,
                    revision INTEGER NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0,
                    project TEXT, UNIQUE(org,actor,event));
                CREATE TABLE IF NOT EXISTS private_context_revisions_v1 (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, text TEXT NOT NULL,
                    tags TEXT NOT NULL, repository TEXT, changed REAL NOT NULL,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS private_context_budget_v1 (
                    id INTEGER PRIMARY KEY CHECK(id=1), bytes INTEGER NOT NULL, records INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS private_context_actor_budget_v1 (
                    org TEXT NOT NULL, actor TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(org,actor));
                CREATE TABLE IF NOT EXISTS private_context_mutations_v1 (
                    org TEXT NOT NULL, actor TEXT NOT NULL, request TEXT NOT NULL,
                    id TEXT NOT NULL, revision INTEGER NOT NULL, withdrawn INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, PRIMARY KEY(org,actor,request));
                CREATE INDEX IF NOT EXISTS private_context_owner_time_v1
                    ON private_context_v1(org,actor,created DESC,id DESC);
            """
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
            for action in ("UPDATE", "DELETE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS private_revision_no_{action.lower()}_v1 "
                           f"BEFORE {action} ON private_context_revisions_v1 BEGIN "
                           "SELECT RAISE(ABORT,'PRIVATE_REVISION_IMMUTABLE'); END")
                db.execute(f"CREATE TRIGGER IF NOT EXISTS private_mutation_no_{action.lower()}_v1 "
                           f"BEFORE {action} ON private_context_mutations_v1 BEGIN "
                           "SELECT RAISE(ABORT,'PRIVATE_MUTATION_IMMUTABLE'); END")
            if not had_actor_budget:
                db.execute("INSERT OR IGNORE INTO private_context_actor_budget_v1 "
                           "SELECT r.org,r.actor,sum(length(CAST(v.text AS BLOB))+length(CAST(v.tags AS BLOB))) "
                           "FROM private_context_v1 r JOIN private_context_revisions_v1 v ON r.id=v.id GROUP BY r.org,r.actor")
            if db.execute("SELECT 1 FROM private_context_budget_v1 WHERE id=1").fetchone() is None:
                used = db.execute("SELECT coalesce(sum(length(CAST(text AS BLOB))+length(CAST(tags AS BLOB))),0) FROM private_context_revisions_v1").fetchone()[0]
                count = db.execute("SELECT count(*) FROM private_context_v1").fetchone()[0]
                db.execute("INSERT INTO private_context_budget_v1 VALUES(1,?,?)", (used, count))

    @contextmanager
    def connection(self, *, write=False):
        # BackupStore created this file privately. mode=rw never creates a new file.
        try:
            db = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0.2)
        except sqlite3.DatabaseError:
            raise HTTPException(503, "PRIVATE_CONTEXT_UNAVAILABLE") from None
        db.row_factory = sqlite3.Row
        deadline = time.monotonic() + 0.25
        steps = 0

        def budget():
            nonlocal steps
            steps += 1000
            return steps >= 200000 or time.monotonic() >= deadline

        db.set_progress_handler(budget, 1000)
        try:
            if not write:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except sqlite3.DatabaseError:
            db.rollback()
            raise HTTPException(503, "PRIVATE_CONTEXT_UNAVAILABLE") from None
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def authorize(self, context, principal):
        # A late worker check uses its own read-only identity connection; do not
        # concurrently reuse OrgStore's writer connection across worker threads.
        identity = context.auth_service.org_store.data_dir / ORG_DATABASE_NAME
        try:
            with closing(sqlite3.connect(identity.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                user = db.execute("SELECT org_id,status FROM user WHERE id=?", (principal.user_id,)).fetchone()
        except sqlite3.DatabaseError:
            raise HTTPException(503, "PRIVATE_CONTEXT_UNAVAILABLE") from None
        if not user or user["status"] != "active" or user["org_id"] != principal.org_id:
            raise HTTPException(403, "ACCOUNT_DISABLED")
        if not context.private_context_enabled or context.private_context_store is not self:
            raise HTTPException(503, "PRIVATE_CONTEXT_DISABLED")

    @staticmethod
    def reserve(db, principal, text, tags, *, new_record=False):
        size = len(text.encode()) + len(tags.encode())
        changed = db.execute("UPDATE private_context_budget_v1 SET bytes=bytes+?,records=records+? "
                             "WHERE id=1 AND bytes+?<=268435456 AND records+?<=10000",
                             (size, int(new_record), size, int(new_record)))
        if changed.rowcount != 1:
            raise HTTPException(413, "PRIVATE_CONTEXT_CAPACITY")
        db.execute("INSERT OR IGNORE INTO private_context_actor_budget_v1 VALUES(?,?,0)", (principal.org_id, principal.user_id))
        changed = db.execute("UPDATE private_context_actor_budget_v1 SET bytes=bytes+? WHERE org=? AND actor=? AND bytes+?<=33554432",
                             (size, principal.org_id, principal.user_id, size))
        if changed.rowcount != 1:
            raise HTTPException(413, "PRIVATE_CONTEXT_CAPACITY")

    @staticmethod
    def own(db, principal, identifier):
        row = db.execute("SELECT r.*,substr(v.text,1,8193) AS text,length(CAST(v.text AS BLOB)) AS text_size,substr(v.tags,1,1025) AS tags,v.repository,v.changed FROM private_context_v1 r "
                         "JOIN private_context_revisions_v1 v ON r.id=v.id AND r.revision=v.revision "
                         "WHERE r.id=? AND r.org=? AND r.actor=?",
                         (identifier, principal.org_id, principal.user_id)).fetchone()
        if row is None:
            raise HTTPException(404, "PRIVATE_CONTEXT_NOT_FOUND")
        return row

    @staticmethod
    def visible(context, principal, row):
        return row["project"] is None or any(p.id == row["project"] and p.org_id == principal.org_id
                                            and principal.user_id in p.allowed_users for p in context.projects)

    @staticmethod
    def dto(row):
        result = {"version": 1, "id": row["id"], "eventId": row["event"], "revision": row["revision"],
                  "visibility": "private", "evidenceKind": "user-statement", "withdrawn": bool(row["withdrawn"]),
                  "createdAt": datetime.fromtimestamp(row["created"], timezone.utc).isoformat(),
                  "revisionChangedAt": datetime.fromtimestamp(row["changed"], timezone.utc).isoformat()}
        if not row["withdrawn"]:
            try:
                if len(row["text"].encode()) != row["text_size"]:
                    raise ValueError("corrupt text")
                validated = PrivateEdit(expectedRevision=row["revision"], text=row["text"], tags=json.loads(row["tags"]))
            except (ValidationError, ValueError, TypeError, RecursionError):
                raise HTTPException(503, "PRIVATE_CONTEXT_UNAVAILABLE") from None
            result.update(text=validated.text, tags=validated.tags, projectId=row["project"], repositoryId=row["repository"])
        return result

    def create(self, context, principal, value):
        self.authorize(context, principal)
        payload = value.model_dump()
        # Keep legacy omitted/null-snapshot fingerprints byte-for-byte unchanged.
        if value.expectedRepositoryId is None:
            payload.pop("expectedRepositoryId")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connection(write=True) as db:
            old = db.execute("SELECT id,fingerprint FROM private_context_v1 WHERE org=? AND actor=? AND event=?",
                             (principal.org_id, principal.user_id, value.eventId)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise HTTPException(409, "PRIVATE_CONTEXT_CONFLICT")
                row = self.own(db, principal, old["id"])
                if not self.visible(context, principal, row):
                    raise HTTPException(404, "PRIVATE_CONTEXT_NOT_FOUND")
                return self.dto(row)
            project = next((p for p in context.projects if p.org_id == principal.org_id
                            and p.id == value.projectId and principal.user_id in p.allowed_users), None)
            if value.projectId is not None and project is None:
                raise HTTPException(403, "PRIVATE_CONTEXT_PROJECT_DENIED")
            if value.expectedRepositoryId is not None and project.repository_id != value.expectedRepositoryId:
                raise HTTPException(409, "PRIVATE_CONTEXT_PROJECT_CHANGED")
            if db.execute("SELECT count(*) FROM private_context_v1 WHERE org=? AND actor=?", (principal.org_id, principal.user_id)).fetchone()[0] >= 500:
                raise HTTPException(413, "PRIVATE_CONTEXT_CAPACITY")
            self.reserve(db, principal, value.text, json.dumps(value.tags), new_record=True)
            identifier, now = str(uuid4()), time.time()
            db.execute("INSERT INTO private_context_v1 VALUES(?,?,?,?,?,?,1,0,?)",
                       (identifier, principal.org_id, principal.user_id, value.eventId, fingerprint, now, value.projectId))
            db.execute("INSERT INTO private_context_revisions_v1 VALUES(?,1,?,?,?,?)",
                       (identifier, value.text, json.dumps(value.tags), project.repository_id if project else None, now))
            return self.dto(self.own(db, principal, identifier))

    def mutation_source(self, db, principal, mutation):
        # Reconstruct the accepted historical revision, not today's possibly newer state.
        current = dict(self.own(db, principal, mutation["id"]))
        if current["withdrawn"] and not mutation["withdrawn"]:
            return current  # Terminal state never re-exposes a historical body through recovery.
        revision = db.execute("SELECT substr(text,1,8193) AS text,length(CAST(text AS BLOB)) AS text_size,"
                              "substr(tags,1,1025) AS tags,repository,changed FROM private_context_revisions_v1 WHERE id=? AND revision=?",
                              (mutation["id"], mutation["revision"])).fetchone()
        if revision is None:
            raise HTTPException(503, "PRIVATE_CONTEXT_UNAVAILABLE")
        current.update(dict(revision))
        current.update(revision=mutation["revision"], withdrawn=mutation["withdrawn"])
        return current

    def change(self, context, principal, identifier, value, *, withdraw=False):
        self.authorize(context, principal)
        with self.connection(write=True) as db:
            row = self.own(db, principal, identifier)
            if not withdraw and not self.visible(context, principal, row):
                raise HTTPException(404, "PRIVATE_CONTEXT_NOT_FOUND")
            request_id = value.requestId
            fingerprint = hashlib.sha256(json.dumps({"id": identifier, "withdraw": withdraw, **value.model_dump()},
                                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if request_id is not None:
                previous = db.execute("SELECT * FROM private_context_mutations_v1 WHERE org=? AND actor=? AND request=?",
                                      (principal.org_id, principal.user_id, request_id)).fetchone()
                if previous is not None:
                    if previous["id"] != identifier or previous["fingerprint"] != fingerprint:
                        raise HTTPException(409, "PRIVATE_CONTEXT_REQUEST_CONFLICT")
                    return self.dto(self.mutation_source(db, principal, previous))
                if not withdraw and db.execute("SELECT count(*) FROM private_context_mutations_v1 WHERE org=? AND actor=?",
                                               (principal.org_id, principal.user_id)).fetchone()[0] >= 50000:
                    raise HTTPException(413, "PRIVATE_CONTEXT_CAPACITY")
            if request_id is None and withdraw and row["withdrawn"] and row["revision"] == value.expectedRevision + 1:
                return self.dto(row)  # Same logical withdrawal retry; never restore.
            if row["withdrawn"] or row["revision"] != value.expectedRevision:
                raise HTTPException(409, "PRIVATE_CONTEXT_REVISION_CONFLICT")
            if row["revision"] >= 1_000_000 or (row["revision"] >= 100 and not withdraw):
                raise HTTPException(409, "PRIVATE_CONTEXT_REVISION_LIMIT")
            if not withdraw:
                self.reserve(db, principal, value.text, json.dumps(value.tags) + (request_id or "") + (fingerprint if request_id else ""))
            revision = row["revision"] + 1
            db.execute("INSERT INTO private_context_revisions_v1 VALUES(?,?,?,?,?,?)",
                       (identifier, revision, "" if withdraw else value.text,
                        "[]" if withdraw else json.dumps(value.tags), row["repository"], time.time()))
            db.execute("UPDATE private_context_v1 SET revision=?,withdrawn=? WHERE id=?", (revision, int(withdraw), identifier))
            if request_id is not None:
                db.execute("INSERT INTO private_context_mutations_v1 VALUES(?,?,?,?,?,?,?)",
                           (principal.org_id, principal.user_id, request_id, identifier, revision, int(withdraw), fingerprint))
            return self.dto(self.own(db, principal, identifier))

    def check_references(self, context, principal, references):
        self.authorize(context, principal)
        # One consistent read snapshot. A stale derivative must not be displayed or sent.
        with self.connection() as db:
            for source in references:
                try:
                    row = self.own(db, principal, source.id)
                except HTTPException as error:
                    if error.status_code == 404:
                        return False
                    raise
                try:
                    self.dto(row)
                except HTTPException as error:
                    if error.status_code == 503:
                        return False
                    raise
                if row["withdrawn"] or row["revision"] != source.revision or not self.visible(context, principal, row):
                    return False
            return True

    def listing(self, context, principal, *, start, end, limit, cursor, search, tag, project):
        self.authorize(context, principal)
        clauses = ["r.org=?", "r.actor=?", "r.created>=?", "r.created<?", "r.withdrawn=0"]
        params = [principal.org_id, principal.user_id, start, end]
        if cursor:
            clauses.append("(r.created<? OR (r.created=? AND r.id<?))")
            params.extend([cursor[0], cursor[0], cursor[1]])
        if search:
            clauses.append("instr(lower(v.text),lower(?))>0")
            params.append(search)
        if tag:
            clauses.append("EXISTS(SELECT 1 FROM json_each(v.tags) WHERE value=?)")
            params.append(tag)
        if project:
            clauses.append("r.project=?")
            params.append(project)
        with self.connection() as db:
            rows = db.execute("SELECT r.*,substr(v.text,1,8193) AS text,length(CAST(v.text AS BLOB)) AS text_size,substr(v.tags,1,1025) AS tags,v.repository,v.changed FROM private_context_v1 r "
                              "JOIN private_context_revisions_v1 v ON r.id=v.id AND r.revision=v.revision "
                              "WHERE " + " AND ".join(clauses) + " ORDER BY r.created DESC,r.id DESC LIMIT ?",
                              [*params, limit + 1]).fetchall()
        page, more = rows[:limit], len(rows) > limit
        visible = [row for row in page if self.visible(context, principal, row)]
        return {"version": 1, "scope": "own-private-context", "items": [self.dto(row) for row in visible],
                "hasMore": more, "nextCursor": encode_cursor(page[-1]) if more else None,
                "omittedRecords": len(visible) != len(page), "evidenceKind": "user-statement"}


def private_principal(request: Request):
    # Native-only opt-in journal, including personal edition without bearer auth.
    if "origin" in request.headers:
        raise HTTPException(403, "NATIVE_CLIENT_REQUIRED")
    context = request.app.state.context
    if context is None or not context.private_context_enabled or context.private_context_store is None:
        raise HTTPException(503, "PRIVATE_CONTEXT_DISABLED")
    journal = context.private_context_store
    # Reuse runtime's identity lock for SSO/ordinary auth; isolated non-runtime
    # contexts still serialize their private authentication/touch transactions.
    with getattr(context.auth_service, "identity_lock", journal.auth_lock):
        principal = require_principal(request, authorization=request.headers.get("authorization"))
        journal.authorize(context, principal)
        return principal


def _context(request, principal):
    context = request.app.state.context
    verified = private_principal(request)
    if verified.user_id != principal.user_id or verified.org_id != principal.org_id:
        raise HTTPException(403, "ACCOUNT_CHANGED")
    if request.app.state.context is not context:
        raise HTTPException(503, "PRIVATE_CONTEXT_UNAVAILABLE")
    return context


async def _body(request, model):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 131072:
            raise HTTPException(413, "PRIVATE_CONTEXT_TOO_LARGE")
        data.extend(chunk)
    try:
        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value

        return model.model_validate(json.loads(bytes(data), object_pairs_hook=unique_object))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise HTTPException(422, "INVALID_PRIVATE_CONTEXT_INPUT") from None


def register_private_context_routes(app):
    @app.get("/v1/context-private-authorization")
    def input_authorization(request: Request, principal: Principal = Depends(private_principal)):
        if request.query_params:
            raise HTTPException(422, "INVALID_PRIVATE_CONTEXT_QUERY")
        context = _context(request, principal)
        journal = context.private_context_store
        with getattr(context.auth_service, "identity_lock", journal.auth_lock), journal.auth_lock:
            context = _context(request, principal)
            journal.authorize(context, principal)
            # Point-in-time permission only; no private source/body and no write lease.
            return {"version": 1, "allowed": True, "meaning": "current-private-intake-authorization"}

    @app.post("/v1/context-private")
    async def create(request: Request, principal: Principal = Depends(private_principal)):
        context = await run_in_threadpool(_context, request, principal)
        value = await _body(request, PrivateInput)
        context = await run_in_threadpool(_context, request, principal)
        return await run_in_threadpool(context.private_context_store.create, context, principal, value)

    @app.get("/v1/context-private")
    def listing(request: Request, principal: Principal = Depends(private_principal)):
        context = _context(request, principal)
        query = request.query_params
        if set(query) - {"start", "end", "limit", "cursor", "tag", "projectId"} or any(
            len(query.getlist(key)) != 1 for key in query
        ):
            raise HTTPException(422, "INVALID_PRIVATE_CONTEXT_QUERY")
        start, end = parse_window(query.get("start"), query.get("end"))
        limit = query.get("limit", "20")
        if not re.fullmatch(r"[0-9]{1,2}", limit) or not 1 <= int(limit) <= 50:
            raise HTTPException(422, "INVALID_PRIVATE_CONTEXT_QUERY")
        search, tag, project = None, query.get("tag"), query.get("projectId")
        if (search is not None and not 1 <= len(search) <= 128) or (tag is not None and tag not in {"decision", "progress", "blocker", "question", "verification"}):
            raise HTTPException(422, "INVALID_PRIVATE_CONTEXT_QUERY")
        if project is not None and not any(p.id == project and p.org_id == principal.org_id
                                          and principal.user_id in p.allowed_users for p in context.projects):
            raise HTTPException(403, "PRIVATE_CONTEXT_PROJECT_DENIED")
        return context.private_context_store.listing(context, principal, start=start, end=end, limit=int(limit),
                                                     cursor=decode_cursor(query.get("cursor")), search=search, tag=tag, project=project)

    @app.post("/v1/context-private-search")
    async def search(request: Request, principal: Principal = Depends(private_principal)):
        context = await run_in_threadpool(_context, request, principal)
        value = await _body(request, PrivateSearch)
        context = await run_in_threadpool(_context, request, principal)
        start, end = parse_window(value.start, value.end)
        if value.projectId is not None and not any(p.id == value.projectId and p.org_id == principal.org_id
                                                  and principal.user_id in p.allowed_users for p in context.projects):
            raise HTTPException(403, "PRIVATE_CONTEXT_PROJECT_DENIED")
        return await run_in_threadpool(context.private_context_store.listing, context, principal,
                                       start=start, end=end, limit=value.limit, cursor=decode_cursor(value.cursor),
                                       search=value.q, tag=value.tag, project=value.projectId)

    @app.get("/v1/context-private-events/{event_id}/requests/{request_id}")
    @app.get("/v1/context-private-events/{event_id}")
    def event_status(request: Request, event_id: str, request_id: str | None = None, principal: Principal = Depends(private_principal)):
        context = _context(request, principal)
        if request.query_params or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id) or (request_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)):
            raise HTTPException(422, "INVALID_PRIVATE_CONTEXT_QUERY")
        journal = context.private_context_store
        with getattr(context.auth_service, "identity_lock", journal.auth_lock), journal.auth_lock, journal.connection() as db:
            record = db.execute("SELECT id FROM private_context_v1 WHERE org=? AND actor=? AND event=?",
                                (principal.org_id, principal.user_id, event_id)).fetchone()
            row = journal.own(db, principal, record["id"]) if record else None
            mutation = None
            if row is not None and request_id is None and not row["withdrawn"]:
                row = journal.mutation_source(db, principal, {"id": row["id"], "revision": 1, "withdrawn": 0})
            if request_id is not None:
                mutation = db.execute("SELECT * FROM private_context_mutations_v1 WHERE org=? AND actor=? AND request=? AND id=?",
                                      (principal.org_id, principal.user_id, request_id, record["id"] if record else "")).fetchone()
                row = journal.mutation_source(db, principal, mutation) if mutation is not None else None
            _context(request, principal)  # Fresh identity and feature check before exposing any result.
            if row is not None and not (mutation is not None and mutation["withdrawn"]) and not journal.visible(context, principal, row):
                raise HTTPException(404, "PRIVATE_CONTEXT_NOT_FOUND")
            return {"version": 1, "meaning": "point-in-time-only-not-absence-proof",
                    "status": "observed" if row is not None else "not-observed",
                    "source": journal.dto(row) if row is not None else None,
                    "mutationId": request_id if mutation is not None else None}

    @app.get("/v1/context-private/{identifier}")
    def get(request: Request, identifier: str, principal: Principal = Depends(private_principal)):
        context = _context(request, principal)
        with context.private_context_store.connection() as db:
            row = context.private_context_store.own(db, principal, identifier)
            if not context.private_context_store.visible(context, principal, row):
                raise HTTPException(404, "PRIVATE_CONTEXT_NOT_FOUND")
            return context.private_context_store.dto(row)

    @app.put("/v1/context-private/{identifier}")
    async def edit(request: Request, identifier: str, principal: Principal = Depends(private_principal)):
        context = await run_in_threadpool(_context, request, principal)
        value = await _body(request, PrivateEdit)
        context = await run_in_threadpool(_context, request, principal)
        return await run_in_threadpool(context.private_context_store.change, context, principal, identifier, value)

    @app.post("/v1/context-private/{identifier}/withdraw")
    async def withdraw(request: Request, identifier: str, principal: Principal = Depends(private_principal)):
        context = await run_in_threadpool(_context, request, principal)
        value = await _body(request, Withdrawal)
        context = await run_in_threadpool(_context, request, principal)
        return await run_in_threadpool(context.private_context_store.change, context, principal, identifier, value, withdraw=True)

    @app.post("/v1/context-private-reference-check")
    async def check(request: Request, principal: Principal = Depends(private_principal)):
        context = await run_in_threadpool(_context, request, principal)
        value = await _body(request, ReferenceCheck)
        context = await run_in_threadpool(_context, request, principal)
        valid = await run_in_threadpool(context.private_context_store.check_references, context, principal, value.sources)
        return {"version": 1, "valid": valid, "requiresUserReview": True,
                "meaning": "current-authorized-private-revisions-only", "evidenceKind": "user-statement"}
