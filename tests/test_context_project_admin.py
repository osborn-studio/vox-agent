import json
import sqlite3

import pytest

from engine.context_project_admin import CatalogError, validate_catalog


@pytest.fixture
def files(tmp_path):
    catalog = tmp_path / "projects.json"
    members = tmp_path / "members.db"
    with sqlite3.connect(members) as database:
        database.execute("CREATE TABLE user (id TEXT PRIMARY KEY, org_id TEXT, status TEXT)")
        database.executemany("INSERT INTO user VALUES(?,?,?)", [("alice", "team", "active"), ("bob", "other", "active"), ("left", "team", "inactive")])
    catalog.write_text(json.dumps([{"id": "vox", "org_id": "team", "name": "Vox", "repository_id": "team/vox", "allowed_users": ["alice"]}]))
    return catalog, members


def test_valid_catalog_does_not_write_database_or_source(files):
    catalog, members = files
    original = (catalog.read_bytes(), members.read_bytes())
    assert validate_catalog(catalog, members) == {"valid": True, "projectCount": 1, "issues": []}
    assert original == (catalog.read_bytes(), members.read_bytes())


@pytest.mark.parametrize("member", ["missing", "bob", "left", "x' OR 1=1 --"])
def test_membership_is_active_same_org_and_parameterized(files, member):
    catalog, members = files
    raw = json.loads(catalog.read_text())
    raw[0]["allowed_users"] = [member]
    catalog.write_text(json.dumps(raw))
    result = validate_catalog(catalog, members)
    assert not result["valid"]
    assert result["issues"] == [{"projectId": "vox", "code": "MEMBER_NOT_ACTIVE_IN_ORG"}]
    assert member not in json.dumps(result)


def test_missing_database_is_not_created(files):
    catalog, members = files
    missing = members.with_name("missing.db")
    with pytest.raises(CatalogError, match="MEMBER_DATABASE_UNAVAILABLE"):
        validate_catalog(catalog, missing)
    assert not missing.exists()


@pytest.mark.parametrize("change", ["duplicate", "local_path", "unknown_field", "duplicate_members", "id_newline", "repository_newline"])
def test_invalid_catalog_reports_only_safe_code(files, change):
    catalog, members = files
    raw = json.loads(catalog.read_text())
    if change == "duplicate":
        raw.append(raw[0])
    elif change == "local_path":
        raw[0]["repository_id"] = "/Users/private/secret"
    elif change == "unknown_field":
        raw[0]["secret"] = "never-echo-input"
    elif change == "id_newline":
        raw[0]["id"] = "vox\n"
    elif change == "repository_newline":
        raw[0]["repository_id"] = "team/vox\n"
    else:
        raw[0]["allowed_users"] = ["alice", "alice"]
    catalog.write_text(json.dumps(raw))
    with pytest.raises(CatalogError) as caught:
        validate_catalog(catalog, members)
    assert str(caught.value) in {"INVALID_CATALOG", "DUPLICATE_PROJECT"}


def test_corrupt_database_is_preserved(files):
    catalog, members = files
    members.write_bytes(b"corrupt fixture")
    with pytest.raises(CatalogError, match="MEMBER_DATABASE_UNAVAILABLE"):
        validate_catalog(catalog, members)
    assert members.read_bytes() == b"corrupt fixture"
