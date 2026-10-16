"""In-memory persistence for VOXAGENT mobile companion capture sessions,
transcript segments, memory nodes, and a lightweight token ledger.

Pure in-memory (no DB, no lifespan dependency) so it can be exercised in
tests via a bare TestClient(app) without ever running FastAPI's lifespan.
All public methods return plain dicts with camelCase keys that match the
Zod schemas in packages/contracts/src/*.ts — the TS gateway parses these
responses directly, so key names and shapes must stay in lockstep.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

# Word-ish tokens: runs of unicode letters/digits (underscore included). CJK
# runs have no interior spaces/punctuation, so a Chinese/Japanese phrase stays
# a single token and still matches as a substring — no segmenter, no regression.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Capture sessions / transcript segments / memory nodes
# ---------------------------------------------------------------------------

_SESSION_PATCHABLE_FIELDS = (
    "title",
    "status",
    "endedAt",
    "detectedLanguages",
    "privacyLevel",
    "consentState",
    "languageHints",
    "localOnlyUntilSync",
)


def build_session(payload: dict) -> dict:
    session_id = f"cap_{uuid4().hex[:12]}"
    timestamp = now_iso()
    return {
        "id": session_id,
        "workspaceId": payload.get("workspaceId"),
        "ownerUserId": payload.get("ownerUserId"),
        "edition": payload.get("edition", "PERSONAL"),
        "source": payload.get("source"),
        "mode": payload.get("mode"),
        "title": payload.get("title"),
        "startedAt": timestamp,
        "endedAt": None,
        "languageHints": payload.get("languageHints", []),
        "detectedLanguages": payload.get("detectedLanguages", []),
        "privacyLevel": payload.get("privacyLevel"),
        "consentState": payload.get("consentState"),
        "deviceIds": payload.get("deviceIds", []),
        "localOnlyUntilSync": payload.get("localOnlyUntilSync", False),
        "status": "RECORDING",
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def build_segment(session_id: str, payload: dict) -> dict:
    return {
        "id": f"seg_{uuid4().hex[:12]}",
        "sessionId": session_id,
        "speakerId": payload.get("speakerId"),
        "speakerLabel": payload.get("speakerLabel"),
        "startMs": payload.get("startMs"),
        "endMs": payload.get("endMs"),
        "language": payload.get("language"),
        "textRaw": payload.get("textRaw"),
        "textNormalized": payload.get("textNormalized"),
        "textTranslated": payload.get("textTranslated"),
        "translationTargetLanguage": payload.get("translationTargetLanguage"),
        "confidence": payload.get("confidence"),
        "model": payload.get("model"),
        "isUserMarked": payload.get("isUserMarked", False),
        "embeddingId": payload.get("embeddingId"),
    }


def build_node(payload: dict) -> dict:
    timestamp = now_iso()
    return {
        "id": f"mem_{uuid4().hex[:12]}",
        "workspaceId": payload.get("workspaceId"),
        "ownerUserId": payload.get("ownerUserId"),
        "sourceSessionId": payload.get("sourceSessionId"),
        "type": payload.get("type"),
        "title": payload.get("title"),
        "body": payload.get("body"),
        "language": payload.get("language"),
        "importance": payload.get("importance", "MEDIUM"),
        "visibility": payload.get("visibility", "PRIVATE"),
        "tags": payload.get("tags", []),
        "metadata": payload.get("metadata", {}),
        "confidence": payload.get("confidence"),
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def _haystack(node: dict) -> str:
    return " ".join(
        [node.get("title", ""), node.get("body", ""), *node.get("tags", [])]
    ).lower()


def node_in_scope(
    node: dict,
    *,
    workspace_id: str | None,
    owner_user_id: str | None,
) -> bool:
    """Match an optional product-principal scope.

    A missing scope keeps repository behavior backward compatible for the
    single-user Personal edition and internal maintenance callers. Enterprise
    product routes always provide both values.
    """

    return (
        workspace_id is None or node.get("workspaceId") == workspace_id
    ) and (
        owner_user_id is None or node.get("ownerUserId") == owner_user_id
    )


def query_tokens(query: str) -> list[str]:
    """Word-order-independent query tokens (lowercased, punctuation stripped)."""
    return _TOKEN_RE.findall(query.lower())


def score_node(node: dict, query: str) -> float:
    """Relevance in [0, 1]: fraction of query tokens found in the node.

    Token overlap (not whole-string substring) so "japan launch" matches a
    node titled "Launch in Japan"; a single CJK token still substring-matches.
    Returns 0.0 when nothing matches (caller filters those out).
    """
    tokens = query_tokens(query)
    if not tokens:
        return 0.0
    haystack = _haystack(node)
    matched = sum(1 for token in tokens if token in haystack)
    return matched / len(tokens)


def node_matches(node: dict, query: str) -> bool:
    """Back-compat boolean form of :func:`score_node`."""
    return score_node(node, query) > 0


def _idf(n_docs: int, doc_freq: int) -> float:
    """BM25-style inverse document frequency (always > 0, ~0 for ubiquitous
    terms). A term present in most nodes contributes almost nothing, so a
    lone stopword like "in" no longer scores as high as a rare, specific term.
    """
    return math.log((n_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1)


def rank_nodes(nodes, query: str, limit: int = 20) -> list[dict]:
    """Rank nodes for a query by summed IDF of the matched query tokens.

    Substring matching (so CJK phrases with no interior spaces still match and
    partial terms hit), weighted by IDF over the given corpus so common terms
    are down-weighted. Returns [{"node", "score", "highlights"}] sorted by
    score descending, capped to `limit`. `score` is a non-negative float
    (unbounded — larger means more/rarer query terms matched).
    """
    tokens = query_tokens(query)
    if not tokens:
        return []
    docs = [(node, _haystack(node)) for node in nodes]
    n_docs = len(docs)
    unique = set(tokens)
    idf = {t: _idf(n_docs, sum(1 for _, hay in docs if t in hay)) for t in unique}
    results = []
    for node, hay in docs:
        score = sum(idf[t] for t in unique if t in hay)
        if score > 0:
            results.append({"node": node, "score": score, "highlights": [node["body"]]})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


@dataclass
class VoiceMemoryRepository:
    sessions: dict = field(default_factory=dict)
    segments: dict = field(default_factory=dict)  # session_id -> list[dict]
    memory_nodes: dict = field(default_factory=dict)
    audio_paths: dict = field(default_factory=dict)  # session_id -> str

    # -- Capture sessions -----------------------------------------------

    def create_session(self, payload: dict) -> dict:
        session = build_session(payload)
        self.sessions[session["id"]] = session
        self.segments[session["id"]] = []
        return session

    def get_session(self, session_id: str) -> dict | None:
        return self.sessions.get(session_id)

    def list_sessions(
        self,
        limit: int = 50,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        sessions = sorted(
            (
                session
                for session in self.sessions.values()
                if (
                    workspace_id is None
                    or session.get("workspaceId") == workspace_id
                )
                and (
                    owner_user_id is None
                    or session.get("ownerUserId") == owner_user_id
                )
            ),
            key=lambda s: s["createdAt"],
            reverse=True,
        )
        return sessions[:limit]

    def update_session(self, session_id: str, payload: dict) -> dict:
        session = self.sessions[session_id]
        for key in _SESSION_PATCHABLE_FIELDS:
            if key in payload:
                session[key] = payload[key]
        session["updatedAt"] = now_iso()
        return session

    # -- Transcript segments ----------------------------------------------

    def add_segment(self, session_id: str, payload: dict) -> dict:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        segment = build_segment(session_id, payload)
        self.segments[session_id].append(segment)
        return segment

    def list_segments(self, session_id: str) -> list:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.segments.get(session_id, [])

    # -- Audio path store ---------------------------------------------------

    def set_audio_path(self, session_id: str, path: str) -> None:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        self.audio_paths[session_id] = path

    def get_audio_path(self, session_id: str) -> str | None:
        return self.audio_paths.get(session_id)

    # -- Memory nodes -------------------------------------------------------

    def add_memory_node(self, payload: dict) -> dict:
        node = build_node(payload)
        self.memory_nodes[node["id"]] = node
        return node

    def get_node(
        self,
        node_id: str,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict | None:
        node = self.memory_nodes.get(node_id)
        if node is None or not node_in_scope(
            node,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
        ):
            return None
        return node

    def list_recent_nodes(
        self,
        days: int = 7,
        limit: int = 20,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        nodes = [
            n
            for n in self.memory_nodes.values()
            if datetime.fromisoformat(n["createdAt"]) >= cutoff
            and node_in_scope(
                n,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        nodes.sort(key=lambda n: n["createdAt"], reverse=True)
        return nodes[:limit]

    def list_action_items(
        self,
        status: str | None = None,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        items = [
            n
            for n in self.memory_nodes.values()
            if n["type"] == "ACTION_ITEM"
            and node_in_scope(
                n,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        if status is not None:
            items = [n for n in items if n.get("metadata", {}).get("status") == status]
        return items

    def list_nodes_for_session(
        self,
        session_id: str,
        node_type: str | None = None,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list:
        nodes = [
            n
            for n in self.memory_nodes.values()
            if n.get("sourceSessionId") == session_id
            and node_in_scope(
                n,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        if node_type is not None:
            nodes = [n for n in nodes if n["type"] == node_type]
        nodes.sort(key=lambda n: n["createdAt"])
        return nodes

    def search_memory(
        self,
        query: str,
        limit: int = 20,
        *,
        workspace_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict:
        nodes = [
            node
            for node in self.memory_nodes.values()
            if node_in_scope(
                node,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            )
        ]
        return {
            "query": query,
            "results": rank_nodes(nodes, query, limit),
        }

    # -- Retention ---------------------------------------------------------

    def retention_candidates(
        self,
        *,
        audio_before: str,
        transcript_before: str,
        memory_before: str,
    ) -> dict:
        audio = [
            {"sessionId": session_id, "path": path}
            for session_id, path in self.audio_paths.items()
            if self.sessions[session_id]["createdAt"] < audio_before
        ]
        transcripts = [
            session_id
            for session_id, segments in self.segments.items()
            if segments and self.sessions[session_id]["createdAt"] < transcript_before
        ]
        memory = [
            node_id
            for node_id, node in self.memory_nodes.items()
            if node["createdAt"] < memory_before
        ]
        return {"audio": audio, "transcripts": transcripts, "memory": memory}

    def delete_retention_candidates(
        self,
        *,
        audio_session_ids: list[str],
        transcript_session_ids: list[str],
        memory_node_ids: list[str],
    ) -> dict:
        audio_deleted = sum(self.audio_paths.pop(session_id, None) is not None for session_id in audio_session_ids)
        transcript_deleted = 0
        for session_id in transcript_session_ids:
            transcript_deleted += len(self.segments.get(session_id, []))
            self.segments[session_id] = []
        memory_deleted = sum(self.memory_nodes.pop(node_id, None) is not None for node_id in memory_node_ids)
        return {
            "audio": audio_deleted,
            "transcripts": transcript_deleted,
            "memory": memory_deleted,
        }


# ---------------------------------------------------------------------------
# Token ledger
# ---------------------------------------------------------------------------

_DEFAULT_SCOPE_ID = "user_local"
_DEFAULT_SCOPE_TYPE = "USER"
_DEFAULT_INITIAL_BALANCE = 100_000


@dataclass
class TokenLedger:
    balance: int = _DEFAULT_INITIAL_BALANCE
    scope_id: str = _DEFAULT_SCOPE_ID
    scope_type: str = _DEFAULT_SCOPE_TYPE
    entries: list = field(default_factory=list)

    def __post_init__(self) -> None:
        timestamp = now_iso()
        self.entries.append({
            "id": f"ledger_{uuid4().hex[:12]}",
            "scopeId": self.scope_id,
            "scopeType": self.scope_type,
            "sessionId": None,
            "spendType": "PURCHASE",
            "delta": self.balance,
            "balanceAfter": self.balance,
            "description": "initial grant",
            "createdAt": timestamp,
        })
        self._updated_at = timestamp

    def get_balance(self, scope_id: str = _DEFAULT_SCOPE_ID, scope_type: str = _DEFAULT_SCOPE_TYPE) -> dict:
        return {
            "scopeId": scope_id,
            "scopeType": scope_type,
            "balance": self.balance,
            "currency": "TOKENS",
            "updatedAt": self._updated_at,
        }

    def list_ledger(self) -> list:
        return self.entries
