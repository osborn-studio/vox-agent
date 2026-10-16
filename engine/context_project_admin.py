"""Read-only operator preflight for explicit project membership.

No tenant calls, database migrations, secret resolution, runtime writes or grants.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

from .context_projects import ContextProject


class CatalogError(ValueError):
    pass


def validate_catalog(catalog: Path, members_database: Path) -> dict:
    # Errors never echo operator file content or model validation inputs.
    try:
        if catalog.stat().st_size > 1_000_000:
            raise CatalogError("CATALOG_TOO_LARGE")
        raw = json.loads(catalog.read_text())
        if not isinstance(raw, list) or len(raw) > 1000:
            raise CatalogError("INVALID_CATALOG")
        projects = [ContextProject.model_validate(item) for item in raw]
        if len({(p.org_id, p.id) for p in projects}) != len(projects):
            raise CatalogError("DUPLICATE_PROJECT")
    except CatalogError:
        raise
    except (OSError, ValueError, ValidationError):
        raise CatalogError("INVALID_CATALOG") from None
    if not members_database.is_file():
        raise CatalogError("MEMBER_DATABASE_UNAVAILABLE")
    issues = []
    try:
        # URI mode=ro prevents accidental file creation; query_only forbids writes.
        with closing(sqlite3.connect(members_database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as database:
            database.execute("PRAGMA query_only=ON")
            for project in projects:
                for user_id in project.allowed_users:
                    row = database.execute("SELECT org_id, status FROM user WHERE id=?", (user_id,)).fetchone()
                    if row is None or row != (project.org_id, "active"):
                        issues.append({"projectId": project.id, "code": "MEMBER_NOT_ACTIVE_IN_ORG"})
                        break
    except sqlite3.Error:
        raise CatalogError("MEMBER_DATABASE_UNAVAILABLE") from None
    return {"valid": not issues, "projectCount": len(projects), "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--members-database", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_catalog(args.catalog, args.members_database)
    except CatalogError as error:
        print(json.dumps({"valid": False, "error": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
