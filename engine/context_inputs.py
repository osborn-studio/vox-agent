"""Own submitted Context readback, not a private inbox or work evidence.

No model calls, Feishu reads, mutation, or automatic export. The original configured
backup destination survives delivery failures. Pagination never broadens permission.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import Principal, require_principal
from .input_backup import BackupInput


PAGE_NOTICES = [
    "Only currently authorized accepted backups are covered; original destination determines visibility.",
    "Omission flags cover hidden targets in the window and project/corrupt records on this page. Combine flags across pages.",
    "Follow nextCursor while hasMore is true, even for short or empty pages. This is not a private inbox or complete work record.",
]


def parse_window(start, end):
    try:
        dates = [datetime.fromisoformat(value) for value in (start, end)]
        if any(date.tzinfo is None or date.utcoffset() is None for date in dates):
            raise ValueError()
        start_date, end_date = [date.astimezone(timezone.utc) for date in dates]
        if not timedelta(0) < end_date - start_date <= timedelta(days=31):
            raise ValueError()
        return start_date.timestamp(), end_date.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(422, "INVALID_CONTEXT_WINDOW") from None


def decode_cursor(value):
    if value is None:
        return None
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
            raise ValueError()
        created, identifier = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if type(created) not in (int, float) or not math.isfinite(created) or abs(created) > 1e12:
            raise ValueError()
        if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
            raise ValueError()
        return float(created), identifier
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise HTTPException(422, "INVALID_CONTEXT_CURSOR") from None


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([row["created"], row["id"]]).encode()).decode().rstrip("=")


def read_own_inputs(context, principal, start, end, *, limit=20, cursor=None):
    """Actor/target scoped SQL, bounded scan; project/corruption filtering follows.

    A page may be empty/short with hasMore=True. Always follow nextCursor, never
    infer completeness from the item count. This is original input readback,
    not proof of the exact rendered message or database tamper resistance.
    """
    targets = tuple(t.id for t in context.targets
                    if t.org_id == principal.org_id and context.allowed_user(t, principal.user_id))
    projects = {p.id for p in context.projects
                if p.org_id == principal.org_id and principal.user_id in p.allowed_users}
    result = {"version": 1, "scope": "own-submitted-backup-inputs", "items": [],
              "nextCursor": None, "hasMore": False, "omittedRecords": False,
              "coverage": "currently-authorized-accepted-backups", "notices": list(PAGE_NOTICES)}
    hidden_target_clause = ""
    if targets:
        hidden_target_clause = " AND (o.id IS NULL OR o.target NOT IN (" + ",".join("?" for _ in targets) + "))"
    clauses = ["r.org=?", "r.actor=?", "r.created>=?", "r.created<?",
               "o.target IN (" + ",".join("?" for _ in targets) + ")"]
    params = [principal.org_id, principal.user_id, start, end, *targets]
    if cursor is not None:
        clauses.append("(r.created<? OR (r.created=? AND r.id<?))")
        params.extend([cursor[0], cursor[0], cursor[1]])
    params.append(limit + 1)
    deadline = time.monotonic() + 0.25
    instructions = 0

    def bounded_read():
        nonlocal instructions
        instructions += 1000
        return instructions >= 200000 or time.monotonic() >= deadline

    try:
        # Independent WAL reader: never acquire the delivery/writer mutex. A
        # time/instruction budget bounds scans, not only returned row count.
        with closing(sqlite3.connect(context.backup_store.path.as_uri() + "?mode=ro", uri=True,
                                     timeout=0.2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(bounded_read, 1000)
            db.execute("BEGIN")
            result["omittedRecords"] = db.execute(
                "SELECT 1 FROM backup_records r LEFT JOIN backup_outbox o ON r.id=o.id "
                "WHERE r.org=? AND r.actor=? AND r.created>=? AND r.created<?"
                + hidden_target_clause + " LIMIT 1", [principal.org_id, principal.user_id, start, end, *targets]
            ).fetchone() is not None
            if not targets:
                return result
            rows = db.execute(
                "SELECT r.id,r.event,r.created,substr(r.raw_payload,1,100001) AS raw_payload,"
                "r.fingerprint,o.target,o.status FROM backup_records r "
                "JOIN backup_outbox o ON r.id=o.id WHERE " + " AND ".join(clauses)
                + " ORDER BY r.created DESC,r.id DESC LIMIT ?", params
            ).fetchall()
    except sqlite3.DatabaseError:
        raise HTTPException(503, "CONTEXT_READ_UNAVAILABLE") from None
    result["hasMore"] = len(rows) > limit
    page = rows[:limit]
    if result["hasMore"]:
        result["nextCursor"] = encode_cursor(page[-1])
    for row in page:
        try:
            if not isinstance(row["raw_payload"], str) or len(row["raw_payload"]) > 100000:
                raise ValueError()
            payload = json.loads(row["raw_payload"])
            if not isinstance(payload, dict):
                raise ValueError()
            repository = payload.pop("resolvedRepository", None)
            value = BackupInput.model_validate(payload)
            if hashlib.sha256(context.backup_store.canonical_request(value).encode()).hexdigest() != row["fingerprint"]:
                raise ValueError()
            if value.eventId != row["event"] or value.targetId != row["target"]:
                raise ValueError()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value.eventId):
                raise ValueError()
            attribution = None
            if value.projectId is not None:
                if value.projectId not in projects:
                    result["omittedRecords"] = True
                    continue
                if value.repoSource != "manual" or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value.projectId):
                    raise ValueError()
                if not isinstance(repository, str) or len(repository) > 200 or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository
                ) or any(part in {".", ".."} for part in repository.split("/")):
                    raise ValueError()
                attribution = {"projectId": value.projectId, "repositoryId": repository,
                               "source": "manual"}
            elif value.repoSource is not None or repository is not None:
                raise ValueError()
            if row["status"] not in {"pending", "sending", "unknown", "delivered", "blocked", "rejected"}:
                raise ValueError()
            submitted = datetime.fromtimestamp(row["created"], timezone.utc).isoformat()
            result["items"].append({"eventId": value.eventId, "text": value.text,
                                    "submittedAt": submitted, "targetId": value.targetId,
                                    "shareIntent": "backup-destination", "deliveryStatus": row["status"],
                                    "attribution": attribution, "evidenceKind": "user-statement"})
        except (ValueError, TypeError, OverflowError, RecursionError):
            result["omittedRecords"] = True
    return result


def register_context_input_routes(app):
    # A missing timezone database is a startup/configuration failure, not a
    # request-time 500 after deployment. Native/CI hosts ship this IANA zone.
    week_timezone = ZoneInfo("Asia/Shanghai")
    def active_context(request, principal):
        context = request.app.state.context
        user = context.auth_service.org_store.get_user(principal.user_id)
        if not user or user["status"] != "active" or user["org_id"] != principal.org_id:
            raise HTTPException(403, "ACCOUNT_DISABLED")
        return context

    @app.get("/v1/context-inputs")
    def own_inputs(request: Request, principal: Principal = Depends(require_principal)):
        query = request.query_params
        if set(query) - {"start", "end", "limit", "cursor"} or any(
            len(query.getlist(key)) != 1 for key in query
        ):
            raise HTTPException(422, "INVALID_CONTEXT_QUERY")
        start, end = parse_window(query.get("start"), query.get("end"))
        raw_limit = query.get("limit", "20")
        if not re.fullmatch(r"[0-9]{1,2}", raw_limit) or not 1 <= int(raw_limit) <= 50:
            raise HTTPException(422, "INVALID_CONTEXT_LIMIT")
        cursor = decode_cursor(query.get("cursor"))
        context = active_context(request, principal)
        value = read_own_inputs(context, principal, start, end, limit=int(raw_limit), cursor=cursor)
        return JSONResponse(content=value, headers={"Cache-Control": "no-store",
                                                    "Referrer-Policy": "no-referrer"})

    @app.get("/v1/context-weekly-draft")
    def weekly_draft(request: Request, principal: Principal = Depends(require_principal)):
        query = request.query_params
        try:
            if "week" not in query or set(query) - {"week", "cursor"} or any(
                len(query.getlist(key)) != 1 for key in query
            ):
                raise ValueError()
            week = query["week"]
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", week):
                raise ValueError()
            start = datetime.fromisoformat(week).replace(tzinfo=week_timezone)
            if start.weekday() != 0:
                raise ValueError()
            end = start + timedelta(days=7)
        except (ValueError, TypeError, OverflowError):
            raise HTTPException(422, "INVALID_CONTEXT_WEEK") from None
        context = active_context(request, principal)
        page = read_own_inputs(context, principal, start.timestamp(), end.timestamp(),
                               limit=50, cursor=decode_cursor(query.get("cursor")))
        # Deterministic source assembly only. Delivery is not completed code or
        # verification evidence; every entry remains a quoted user statement.
        value = {"version": 1, "week": week, "timezone": "Asia/Shanghai",
                 "kind": "source-review-draft", "requiresUserReview": True,
                 "coverage": page["coverage"], "hasMore": page["hasMore"],
                 "omittedRecords": page["omittedRecords"], "nextCursor": page["nextCursor"],
                 "notices": ["Only currently authorized accepted backup inputs are included; local/private inputs are not covered. Original destination determines visibility.",
                             "User statements do not prove completed or verified work. Missing data does not mean no work.",
                             *PAGE_NOTICES],
                 "sources": page["items"]}
        return JSONResponse(content=value, headers={"Cache-Control": "no-store",
                                                    "Referrer-Policy": "no-referrer"})
