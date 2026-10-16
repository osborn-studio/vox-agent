"""T29: Windows engine bundle build plan and loopback verification."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_windows_engine as build  # noqa: E402
import frozen_engine  # noqa: E402
import verify_engine_loopback as verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class FrozenEntrypointTest(unittest.TestCase):
    def test_only_loopback_hosts_are_accepted(self):
        for host in ("127.0.0.1", "::1"):
            self.assertEqual(frozen_engine.parse_args(["--host", host, "--port", "18900"]).host, host)
        for host in ("0.0.0.0", "::", "192.168.1.5", "localhost.evil.example"):
            with self.assertRaises(SystemExit):
                frozen_engine.parse_args(["--host", host, "--port", "18900"])

    def test_the_port_must_be_a_real_port(self):
        for port in ("0", "70000", "-1", "x"):
            with self.assertRaises(SystemExit):
                frozen_engine.parse_args(["--port", port])


class BuildPlanTest(unittest.TestCase):
    def test_pyinstaller_parameters_match_the_macos_release(self):
        script = (ROOT / "scripts/build_voxagent_dmg.sh").read_text(encoding="utf-8")
        macos = re.findall(r"--collect-all (\S+)", script)
        self.assertEqual(list(build.COLLECT_ALL), macos)
        self.assertIn("--name voxagent-engine", script)
        self.assertIn("scripts/frozen_engine.py", script)

    def test_the_bundle_name_carries_the_target_triple_tauri_expects(self):
        self.assertEqual(build.bundle_name("x86_64-pc-windows-msvc"), "voxagent-engine-x86_64-pc-windows-msvc.exe")
        self.assertEqual(build.bundle_name("aarch64-apple-darwin"), "voxagent-engine-aarch64-apple-darwin")

    def test_pyinstaller_cannot_cross_compile_so_a_foreign_target_is_refused(self):
        with self.assertRaises(build.BuildError):
            build.check_target("x86_64-pc-windows-msvc", host_system="Darwin")
        with self.assertRaises(build.BuildError):
            build.check_target("aarch64-apple-darwin", host_system="Windows")
        build.check_target("x86_64-pc-windows-msvc", host_system="Windows")
        build.check_target("aarch64-apple-darwin", host_system="Darwin")
        with self.assertRaises(build.BuildError):
            build.check_target("not a triple", host_system="Windows")

    def test_the_python_and_the_binary_must_match_the_target_architecture(self):
        self.assertTrue(build.arch_matches("x86_64-pc-windows-msvc", "AMD64"))
        self.assertTrue(build.arch_matches("aarch64-pc-windows-msvc", "ARM64"))
        self.assertTrue(build.arch_matches("aarch64-apple-darwin", "arm64"))
        self.assertFalse(build.arch_matches("aarch64-pc-windows-msvc", "AMD64"))
        self.assertFalse(build.arch_matches("x86_64-apple-darwin", "arm64"))
        self.assertFalse(build.arch_matches("riscv64gc-unknown-linux-gnu", "riscv64"))
        sys.path.insert(0, str(ROOT / "scripts"))
        from test_windows_preflight_stages import pe_bytes
        self.assertEqual(build.binary_arch(pe_bytes(0x8664)), "x86_64")
        self.assertEqual(build.binary_arch(pe_bytes(0xAA64)), "aarch64")
        macho_arm64 = (0xFEEDFACF).to_bytes(4, "little") + (0x0100000C).to_bytes(4, "little") + b"\0" * 24
        macho_x64 = (0xFEEDFACF).to_bytes(4, "little") + (0x01000007).to_bytes(4, "little") + b"\0" * 24
        self.assertEqual(build.binary_arch(macho_arm64), "aarch64")
        self.assertEqual(build.binary_arch(macho_x64), "x86_64")
        self.assertIsNone(build.binary_arch(b"#!/bin/sh\n"))

    def test_the_command_is_fixed_and_repository_relative(self):
        command = build.pyinstaller_command("/py", Path("/work"), Path("/dist"))
        self.assertEqual(command[:4], ["/py", "-m", "PyInstaller", "--clean"])
        self.assertIn("--onefile", command)
        self.assertEqual(command[-1], str(ROOT / "scripts/frozen_engine.py"))
        self.assertEqual(command.count("--collect-all"), len(build.COLLECT_ALL))

    def test_the_manifest_binds_the_binary_to_its_source(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / build.bundle_name("x86_64-pc-windows-msvc")
            binary.write_bytes(b"MZ fake")
            manifest = build.write_manifest(binary, "x86_64-pc-windows-msvc", "a" * 40)
            data = json.loads(manifest.read_text())
            self.assertEqual(data["target"], "x86_64-pc-windows-msvc")
            self.assertEqual(data["source_commit"], "a" * 40)
            self.assertEqual(data["sha256"], build.sha256(binary))
            self.assertEqual(data["file"], binary.name)

    def test_release_config_bundles_the_engine_and_default_config_does_not(self):
        release = json.loads((ROOT / "apps/desktop-tauri/src-tauri/tauri.release-engine.conf.json").read_text())
        self.assertEqual(release["bundle"]["externalBin"], ["binaries/voxagent-engine"])
        default = json.loads((ROOT / "apps/desktop-tauri/src-tauri/tauri.conf.json").read_text())
        self.assertNotIn("externalBin", default.get("bundle", {}))
        ignore = (ROOT / "apps/desktop-tauri/src-tauri/.gitignore").read_text()
        self.assertIn("/binaries/", ignore)


class LoopbackVerificationTest(unittest.TestCase):
    """Starts the real engine from source (the same entrypoint PyInstaller freezes)."""

    def test_the_source_engine_admits_only_its_launcher(self):
        token = "known-test-token_0123456789abcdefghijklmnop"
        report = verify.run([sys.executable, str(ROOT / "scripts/frozen_engine.py")], timeout=60, token=token)
        self.assertEqual(report["status"], "PASS", json.dumps(report, indent=2))
        checks = report["checks"]
        self.assertEqual(set(checks), set(verify.EXPECTED_CHECKS))
        self.assertTrue(all(checks.values()), checks)
        self.assertNotIn(token, json.dumps(report), "the report never contains the token")


if __name__ == "__main__":
    unittest.main()
