import tempfile
import unittest
from pathlib import Path
from verify_small_app_bundle import verify


class SmallAppBundleTests(unittest.TestCase):
    def test_clean_bundle_and_rejections_preserve_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'Vox Agent.app'
            executable = app / 'Contents/MacOS/VoxLog'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'fixture')
            verify(app)
            resources = app / 'Contents/Resources'
            resources.mkdir()
            model = resources / 'model.SAFETENSORS'
            model.write_bytes(b'weights')
            with self.assertRaises(ValueError):
                verify(app)
            self.assertEqual(model.read_bytes(), b'weights')
            model.unlink()  # Isolated test fixture only.
            (resources / 'Models').symlink_to(Path(tmp) / 'missing')
            with self.assertRaises(ValueError):
                verify(app)
            self.assertTrue((resources / 'Models').is_symlink())

    def test_missing_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                verify(Path(tmp))



class ReleaseRuntimeTests(unittest.TestCase):
    def make_bundle(self, root):
        app = Path(root) / 'Vox Agent.app'
        binary = app / 'Contents/MacOS/VoxLog'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'app fixture')
        resources = app / 'Contents/Resources'
        resources.mkdir()
        (resources / 'QWEN_MODEL_CATALOG.json').write_text('{}')
        return app

    def add_runtime(self, app):
        helper = app / 'Contents/MacOS/qwen3-asr-server'
        helper.write_bytes(b'runtime fixture')
        helper.chmod(0o755)
        resources = app / 'Contents/Resources'
        (resources / 'QWEN_RUNTIME_VERSIONS.txt').write_text('mlx==0.29.3\n')
        notices = resources / 'QwenRuntimeLicenses'
        notices.mkdir()
        (notices / 'LICENSE.txt').write_text('Runtime license fixture')
        return helper

    def test_standard_release_rejects_missing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            # This is the 4.2.10 failure: weights are external, but so is the helper.
            with self.assertRaisesRegex(ValueError, 'qwen3-asr-server'):
                verify(app, require_qwen_runtime=True)
            verify(app)  # Intermediate bundle preparation still accepts this input.

    def test_complete_runtime_without_weights_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_bundle(tmp)
            self.add_runtime(app)
            verify(app, require_qwen_runtime=True)

    def test_incomplete_runtime_is_rejected(self):
        for broken in ['empty', 'not-executable', 'symlink', 'licenses', 'versions', 'catalog']:
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as tmp:
                app = self.make_bundle(tmp)
                helper = self.add_runtime(app)
                resources = app / 'Contents/Resources'
                if broken == 'empty':
                    helper.write_bytes(b'')
                elif broken == 'not-executable':
                    helper.chmod(0o644)
                elif broken == 'symlink':
                    helper.rename(Path(tmp) / 'helper')
                    helper.symlink_to(Path(tmp) / 'helper')
                elif broken == 'licenses':
                    (resources / 'QwenRuntimeLicenses/LICENSE.txt').unlink()
                elif broken == 'versions':
                    (resources / 'QWEN_RUNTIME_VERSIONS.txt').unlink()
                else:
                    (resources / 'QWEN_MODEL_CATALOG.json').unlink()
                with self.assertRaises(ValueError):
                    verify(app, require_qwen_runtime=True)


if __name__ == '__main__':
    unittest.main()
