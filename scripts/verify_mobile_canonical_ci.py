"""Explicit new-contract CI adapter; no historic compatibility waiver by exit alone."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

STAGES = (
    "provenance", "canonical_contract", "verify_mobile_patch_installer.cjs",
    "verify_mobile_xcode_uuid.cjs", "verify_mobile_prebuild_parity_tests.cjs",
    "cache_file", "cli", "online_csr", "provenance_tests", "gate_mutations",
    "dispatch", "builder", "matcher_parity", "delivery_install", "historical_compatibility",
)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate report key")
        result[key] = value
    return result


def validate_report(stdout, exit_code):
    if exit_code != 0:
        raise ValueError("Canonical runner failed")
    lines = stdout.strip().splitlines()
    if not lines:
        raise ValueError("Missing canonical report")
    report = json.loads(lines[-1], object_pairs_hook=unique_object)
    if not isinstance(report, dict):
        raise ValueError("Report must be an object")
    if report.get("contract") != "vox-canonical-signature-v1":
        raise ValueError("Wrong contract")
    if (report.get("attackRegression") is not False
            or report.get("unresolvedCompatibility") is not False
            or report.get("knownBreakingChange") is not True):
        raise ValueError("Historical break not explicitly accounted for")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(STAGES):
        raise ValueError("Missing or extra safety stages")
    for result, expected in zip(results, STAGES, strict=True):
        if not isinstance(result, dict) or result.get("name") != expected:
            raise ValueError("Safety stage identity/order mismatch")
        status = result.get("status")
        expected_status = 2 if expected == "historical_compatibility" else 0
        if (type(status) is not int or status != expected_status
                or ("error" in result and result["error"] is not None)):
            raise ValueError("Safety stage failed")
    return report


def check_runner(runner, expected_hash):
    if len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash):
        raise ValueError("Trusted workflow must supply exact runner SHA256")
    if hashlib.sha256(runner.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("External runner pin mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--node-bin", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--upstream-dir", type=Path, required=True)
    parser.add_argument("--oracle-root", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    runner = repo / "scripts/verify_mobile_candidate_safety.cjs"
    check_runner(runner, args.runner_sha256)
    if not args.node_bin.is_absolute() or not args.node_bin.is_file():
        raise ValueError("Trusted workflow must supply an absolute Node executable")
    environment = {k: v for k, v in os.environ.items()
                   if k not in {"NODE_OPTIONS", "NODE_PATH"}}
    process = subprocess.run(
        [str(args.node_bin), str(runner), "--app-root", str(args.app_root.resolve()),
         "--upstream-dir", str(args.upstream_dir.resolve()),
         "--oracle-root", str(args.oracle_root.resolve()),
         "--contract", "vox-canonical-signature-v1"],
        cwd=repo, env=environment, capture_output=True, text=True, timeout=1800, check=False,
    )
    print(process.stdout, end="")
    print(process.stderr, end="")
    validate_report(process.stdout, process.returncode)
    print("PASS approved canonical contract; historical compatibility remains explicitly broken")


if __name__ == "__main__":
    main()
