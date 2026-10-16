from pathlib import Path


def test_development_entrypoints_do_not_embed_fixed_credentials():
    client = Path("client.py").read_text(encoding="utf-8")
    dev_script = Path("scripts/dev_phase0.sh").read_text(encoding="utf-8")
    smoke_script = Path("scripts/smoke_phase0.sh").read_text(encoding="utf-8")

    assert "voxlog-dev-token" not in client
    assert 'os.getenv("VOXLOG_API_TOKEN", "")' in client

    for script in (dev_script, smoke_script):
        assert "VOXLOG_API_TOKEN=dev-token" not in script
        assert "VOXLOG_PY_BACKEND_API_TOKEN=dev-token" not in script
        assert "token_urlsafe" in script
