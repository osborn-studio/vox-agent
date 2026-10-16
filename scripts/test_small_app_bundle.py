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


if __name__ == '__main__':
    unittest.main()
