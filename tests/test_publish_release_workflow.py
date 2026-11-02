"""Guards for the manual draft-release workflow (publish-release.yml)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _job(text: str, name: str) -> str:
    start = text.index(f"\n  {name}:\n")
    rest = text[start + 1:]
    following = [rest.find(f"\n  {other}:\n") for other in ("prepare", "build-macos", "build-windows", "publish")]
    ends = [index for index in following if index > 0]
    return rest[: min(ends)] if ends else rest


def test_only_manual_dispatch_and_no_tag_trigger():
    text = _text("publish-release.yml")
    on = text[text.index("\non:\n"):text.index("\npermissions:")]
    assert "workflow_dispatch:" in on
    for trigger in ("push:", "pull_request", "tags:", "release:", "schedule:", "workflow_run"):
        assert trigger not in on


def test_write_permission_is_confined_to_the_publish_job():
    text = _text("publish-release.yml")
    assert "permissions:\n  contents: read\n" in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert code.count("contents: write") == 1
    assert "contents: write" in _job(text, "publish")
    for job in ("prepare", "build-macos", "build-windows"):
        assert "contents: write" not in _job(text, job)


def test_publish_job_runs_no_repository_code_and_creates_only_a_draft():
    publish = _job(_text("publish-release.yml"), "publish")
    assert "actions/checkout" not in publish
    assert "gh release create" in publish
    assert "--draft" in publish
    assert "--target" in publish
    for forbidden in ("gh release edit", "--latest", "git push", "git tag", "gh release delete"):
        assert forbidden not in publish


def test_assets_are_checked_before_the_release_is_created():
    publish = _job(_text("publish-release.yml"), "publish")
    assert publish.index("sha256sum --check --strict") < publish.index("gh release create")


def test_existing_releases_and_tags_are_never_replaced_and_main_only():
    prepare = _job(_text("publish-release.yml"), "prepare")
    assert 'gh release view "v$INPUT_VERSION"' in prepare
    assert "git ls-remote --exit-code --tags" in prepare
    assert '"refs/heads/main"' in prepare


def test_no_secrets_and_no_self_hosted_runners():
    text = _text("publish-release.yml")
    assert "secrets." not in text
    assert "self-hosted" not in text


def test_both_build_workflows_are_callable_and_stay_read_only():
    for name in ("release-dmg.yml", "release-windows.yml"):
        text = _text(name)
        on = text[text.index("\non:\n"):text.index("\npermissions:")]
        assert "workflow_call:" in on
        assert "contents: write" not in text
    text = _text("publish-release.yml")
    assert "uses: ./.github/workflows/release-dmg.yml" in text
    assert "uses: ./.github/workflows/release-windows.yml" in text
