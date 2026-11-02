"""Standard upgrades must carry a runnable Qwen helper even with external weights."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_small_app_bundle
from verify_release_dmg import verify_dmg

ROOT = Path(__file__).resolve().parents[1]


class MountedReleaseTests(unittest.TestCase):
    def check_image(self, complete, fail_help=False):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = test_small_app_bundle.ReleaseRuntimeTests()
            app = fixture.make_bundle(tmp)
            if complete:
                fixture.add_runtime(app)
            commands = []
            mount = None

            def run(command, **kwargs):
                nonlocal mount
                commands.append(command)
                if command[:2] == ['hdiutil', 'attach']:
                    self.assertIn('-readonly', command)
                    mount = Path(command[command.index('-mountpoint') + 1])
                    shutil.copytree(app, mount / 'Vox Agent.app')
                if fail_help and command[-1] == '--help':
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)

            with patch('verify_release_dmg.subprocess.run', side_effect=run):
                if not complete:
                    with self.assertRaisesRegex(ValueError, 'qwen3-asr-server'):
                        verify_dmg(Path(tmp) / 'fixture.dmg')
                elif fail_help:
                    with self.assertRaises(subprocess.CalledProcessError):
                        verify_dmg(Path(tmp) / 'fixture.dmg')
                else:
                    verify_dmg(Path(tmp) / 'fixture.dmg')
            self.assertEqual(commands[-1][:2], ['hdiutil', 'detach'])
            self.assertFalse(mount.parent.exists())
            return commands

    def test_missing_runtime_blocks_final_media(self):
        commands = self.check_image(complete=False)
        self.assertEqual(len(commands), 2)  # No execution of an incomplete bundle.

    def test_valid_runtime_is_signature_checked_and_started(self):
        commands = self.check_image(complete=True)
        self.assertEqual(commands[1][:4], ['codesign', '--verify', '--deep', '--strict'])
        self.assertTrue(commands[2][0].endswith('/Contents/MacOS/qwen3-asr-server'))
        self.assertEqual(commands[2][-1], '--help')

    def test_broken_helper_blocks_release_and_detaches(self):
        self.check_image(complete=True, fail_help=True)

    def test_failed_detach_preserves_mount_directory(self):
        mount = None

        def fail(command, **kwargs):
            nonlocal mount
            if command[:2] == ['hdiutil', 'attach']:
                mount = Path(command[command.index('-mountpoint') + 1])
            raise subprocess.CalledProcessError(1, command)

        with patch('verify_release_dmg.subprocess.run', side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                verify_dmg(Path('fixture.dmg'))
        self.assertTrue(mount.is_dir())
        shutil.rmtree(mount.parent)  # No real mount is used in this fixture.


class ReleaseWiringTests(unittest.TestCase):
    def test_standard_builder_cannot_skip_runtime_or_final_media_check(self):
        script = (ROOT / 'scripts/build_voxagent_dmg.sh').read_text()
        build = script.index('bash "$ROOT_DIR/scripts/build_qwen_runtime.sh"')
        verify = script.index('--require-qwen-runtime')
        sign = script.index('codesign --force')
        image = script.index('hdiutil create')
        mounted = script.index('scripts/verify_release_dmg.py')
        checksum = script.index('scripts/verify_update_assets.py')
        self.assertLess(build, verify)
        self.assertLess(verify, sign)
        self.assertLess(sign, image)
        self.assertLess(image, mounted)
        self.assertLess(mounted, checksum)
        self.assertNotIn('SKIP_QWEN', script)

    def test_release_workflow_installs_runtime_and_checks_before_upload(self):
        workflow = (ROOT / '.github/workflows/release-dmg.yml').read_text()
        self.assertLess(workflow.index('qwen3-asr-server/requirements.txt'),
                        workflow.index('bash ./scripts/build_voxagent_dmg.sh'))
        self.assertLess(workflow.index('scripts/verify_release_dmg.py'),
                        workflow.index('uses: actions/upload-artifact'))
        self.assertIn('scripts/test_release_runtime.py',
                      (ROOT / '.github/workflows/verify.yml').read_text())


if __name__ == '__main__':
    unittest.main()
