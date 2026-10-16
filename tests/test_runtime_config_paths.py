"""Vox Agent owns its runtime data paths; VoxLog is read-only migration input."""

from pathlib import Path

from runtime.models.config import resolve_vox_agent_data_dir, resolve_vox_agent_env


PRODUCT_OWNED_PATH_MODULES = (
    "apps/desktop/server.py",
    "engine/access_audit.py",
    "engine/agent_groups.py",
    "engine/agent_memory.py",
    "engine/app_context.py",
    "engine/backup.py",
    "engine/config_validator.py",
    "engine/cost_tracker.py",
    "engine/org_store.py",
    "engine/pinning.py",
    "engine/plugins.py",
    "engine/profiles.py",
    "engine/quickstart.py",
    "engine/recurring.py",
    "engine/skills.py",
    "engine/snapshot.py",
    "engine/store.py",
    "engine/task_archive.py",
    "engine/task_log.py",
    "engine/templates.py",
    "engine/workspace.py",
    "engine/work_events.py",
    "engine/api/memory.py",
)


def test_runtime_data_dir_prefers_vox_agent_override_and_never_defaults_to_voxlog(tmp_path):
    override = tmp_path / "portable-data"

    assert resolve_vox_agent_data_dir(
        {"VOXAGENT_DATA_DIR": str(override)}, home=tmp_path
    ) == override
    assert resolve_vox_agent_data_dir({}, home=tmp_path) == tmp_path / ".voxagent"
    assert ".voxlog" not in str(resolve_vox_agent_data_dir({}, home=tmp_path))


def test_runtime_data_dir_uses_local_app_data_on_windows(tmp_path):
    local_app_data = tmp_path / "LocalAppData"

    assert resolve_vox_agent_data_dir(
        {"LOCALAPPDATA": str(local_app_data)},
        home=tmp_path,
        platform_name="win32",
    ) == local_app_data / "Vox Agent"


def test_runtime_env_prefers_canonical_file_and_uses_legacy_only_as_fallback(tmp_path):
    canonical = tmp_path / ".voxagent" / ".env"
    legacy = tmp_path / ".voxlog2" / ".env"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("VOXLOG_ENV=office\n", encoding="utf-8")

    assert resolve_vox_agent_env(canonical, legacy) == legacy

    canonical.parent.mkdir(parents=True)
    canonical.write_text("VOXAGENT_PROFILE=home\n", encoding="utf-8")
    assert resolve_vox_agent_env(canonical, legacy) == canonical


def test_config_defaults_use_vox_agent_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VOXAGENT_DATA_DIR", str(tmp_path / "Vox Agent"))
    monkeypatch.delenv("VOXAGENT_DB_PATH", raising=False)
    monkeypatch.delenv("VOXLOG_DB_PATH", raising=False)

    from runtime.models.config import VoxAgentConfig

    config = VoxAgentConfig()

    assert config.data_dir == Path(tmp_path / "Vox Agent")
    assert config.db_path == config.data_dir / "history.db"
    assert config.log_dir == config.data_dir / "logs"


def test_product_owned_writers_do_not_default_to_voxlog_paths():
    repository = Path(__file__).resolve().parents[1]

    for relative in PRODUCT_OWNED_PATH_MODULES:
        source = (repository / relative).read_text(encoding="utf-8")
        assert 'Path.home() / ".voxlog"' not in source, relative
        assert 'Path.home() / ".voxlog2"' not in source, relative
        assert '"Application Support" / "VoxLog"' not in source, relative


def test_org_stores_follow_runtime_vox_agent_override(monkeypatch, tmp_path):
    from engine.access_audit import AccessAuditStore
    from engine.org_store import OrgStore
    from engine.work_events import WorkEventStore

    canonical = tmp_path / "Vox Agent"
    monkeypatch.setenv("VOXAGENT_DATA_DIR", str(canonical))

    stores = (OrgStore(), WorkEventStore(), AccessAuditStore())
    assert {store.data_dir for store in stores} == {canonical}
