"""Tests for scripts/scan_plaintext_leaks.py (T44c). Synthetic data only."""

import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import scan_plaintext_leaks as scanner

MARKER = "synthetic-leak-marker-7f3a"


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, *markers):
        return scanner.scan([self.root], list(markers or [MARKER]))

    def test_clean_tree_has_no_hits(self):
        self.write("db/journal.sqlite", os.urandom(4096))
        self.write("logs/app.log", b"started; 3 records; no content\n")
        report = self.scan()
        self.assertEqual(report["hits"], [])
        self.assertEqual(report["scanned_files"], 2)

    def test_every_encoding_is_detected_inside_binary_files(self):
        cases = {
            "plain.bin": MARKER.encode(),
            "utf16le.bin": MARKER.encode("utf-16-le"),
            "utf16be.bin": MARKER.encode("utf-16-be"),
            "hex.txt": MARKER.encode().hex().encode(),
            "b64.txt": base64.b64encode(b"x" + MARKER.encode() + b"yz"),
            "b64b.txt": base64.b64encode(b"ab" + MARKER.encode()),
        }
        for name, payload in cases.items():
            self.write(f"nested/{name}", os.urandom(100) + payload + os.urandom(100))
        hits = {(Path(h["file"]).name, h["encoding"]) for h in self.scan()["hits"]}
        self.assertIn(("plain.bin", "utf-8"), hits)
        self.assertIn(("utf16le.bin", "utf-16-le"), hits)
        self.assertIn(("utf16be.bin", "utf-16-be"), hits)
        self.assertIn(("hex.txt", "hex"), hits)
        self.assertIn(("b64.txt", "base64"), hits)
        self.assertIn(("b64b.txt", "base64"), hits)

    def test_report_never_contains_the_marker_or_file_content(self):
        self.write("leak.txt", b"prefix " + MARKER.encode() + b" suffix")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = scanner.main(["--root", str(self.root), "--marker", MARKER])
        output = buffer.getvalue()
        self.assertEqual(code, 1)
        self.assertNotIn(MARKER, output)
        self.assertNotIn("prefix", output)
        report = json.loads(output)
        self.assertEqual(report["hits"][0]["marker"], 0)
        self.assertEqual(report["hits"][0]["file"], "leak.txt")

    def test_symlinks_are_not_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        Path(outside.name, "secret.txt").write_bytes(MARKER.encode())
        os.symlink(outside.name, self.root / "link")
        self.assertEqual(self.scan()["hits"], [])

    def test_short_markers_are_refused(self):
        with self.assertRaises(ValueError):
            scanner.scan([self.root], ["abc"])

    def test_markers_spanning_read_chunks_are_found(self):
        size = scanner.CHUNK
        data = b"\0" * (size - 5) + MARKER.encode() + b"\0" * 10
        self.write("big.bin", data)
        self.assertEqual(len(self.scan()["hits"]), 1)

    def test_clean_exit_code_is_zero(self):
        self.write("ok.txt", b"nothing here")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(scanner.main(["--root", str(self.root), "--marker", MARKER]), 0)


class Base64AlignmentTest(unittest.TestCase):
    def test_every_prefix_alignment_of_short_and_long_markers_is_found(self):
        for marker in ["abcdefgh", "leak-12345", MARKER, "x" * 31]:
            for prefix in range(6):
                for suffix in range(4):
                    data = os.urandom(prefix) + marker.encode() + os.urandom(suffix)
                    encoded = base64.b64encode(data)
                    forms = [p for e, p in scanner.patterns(marker) if e == "base64"]
                    self.assertTrue(any(f in encoded for f in forms), (marker, prefix, suffix))


class RedactionTest(unittest.TestCase):
    def test_a_marker_in_a_file_name_is_redacted_from_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, f"{MARKER}.txt").write_bytes(MARKER.encode())
            report = scanner.scan([Path(tmp)], [MARKER])
            text = json.dumps(report)
            self.assertNotIn(MARKER, text)
            self.assertEqual(len(report["hits"]), 1)
            self.assertIn("coverage", report)


if __name__ == "__main__":
    unittest.main()
