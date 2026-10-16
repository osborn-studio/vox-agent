"""Test the production byte-equivalence normalizer, never platform hash waivers."""
import base64
import gzip
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).with_name('build_mobile_security_candidates.py')
spec = importlib.util.spec_from_file_location('candidate_builder', MODULE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CompressionContract(unittest.TestCase):
    def test_exact_tar_payload_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / 'reference.tgz'
            actual = root / 'actual.tgz'
            reference.write_bytes(gzip.compress(b'exact complete tar byte stream', mtime=1))
            data = reference.read_bytes()
            identity = {'sha256': hashlib.sha256(data).hexdigest(),
                        'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()}
            actual.write_bytes(gzip.compress(b'exact complete tar byte stream', mtime=2))
            self.assertNotEqual(actual.read_bytes(), data)
            self.assertTrue(builder.canonicalize_compression(actual, reference, identity))
            self.assertEqual(actual.read_bytes(), data)
            self.assertFalse(builder.canonicalize_compression(actual, reference, identity))
            altered = gzip.compress(b'different tar file/mode/metadata', mtime=2)
            actual.write_bytes(altered)
            self.assertFalse(builder.canonicalize_compression(actual, reference, identity))
            self.assertEqual(actual.read_bytes(), altered)
            reference.write_bytes(data + b'tampering')
            with self.assertRaisesRegex(ValueError, 'canonical reference'):
                builder.canonicalize_compression(actual, reference, identity)


if __name__ == '__main__':
    unittest.main()
