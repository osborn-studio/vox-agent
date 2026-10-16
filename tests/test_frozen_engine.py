from scripts.frozen_engine import parse_args


def test_frozen_engine_accepts_coordinator_launch_contract():
    args = parse_args([
        "--host", "127.0.0.1",
        "--port", "18900",
        "--log-level", "warning",
    ])

    assert args.host == "127.0.0.1"
    assert args.port == 18900
    assert args.log_level == "warning"


def test_release_bundle_includes_frozen_engine():
    build_script = open("scripts/build_voxagent_dmg.sh", encoding="utf-8").read()
    workflow = open(".github/source-workflows/release-dmg.yml.txt", encoding="utf-8").read()

    assert "scripts/frozen_engine.py" in build_script
    assert 'voxagent-engine' in build_script
    assert 'Contents/Resources/bin' in build_script
    assert "actions/setup-python" in workflow
    assert "pip install" in workflow


def test_github_actions_use_node24_runtime_generations():
    verify = open(".github/source-workflows/test-engine.yml.txt", encoding="utf-8").read()
    release = open(".github/source-workflows/release-dmg.yml.txt", encoding="utf-8").read()
    combined = verify + release

    assert "actions/checkout@v7" in combined
    assert "actions/setup-python@v6" in combined
    assert "actions/setup-node@v7" in combined
    assert "actions/upload-artifact@v7" in combined
    for deprecated in (
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/setup-node@v4",
        "actions/upload-artifact@v4",
        "softprops/action-gh-release@v2",
    ):
        assert deprecated not in combined


def test_release_publisher_creates_drafts_without_replacing_assets():
    workflow = open(".github/source-workflows/release-dmg.yml.txt", encoding="utf-8").read()

    # Publishing now uses the create-only CLI, not an action that updates releases.
    assert 'gh release create "$GITHUB_REF_NAME"' in workflow
    assert "--verify-tag --draft --prerelease --latest=false" in workflow
    assert "softprops/action-gh-release@" not in workflow
    assert "gh release upload" not in workflow
    assert "gh release edit" not in workflow
