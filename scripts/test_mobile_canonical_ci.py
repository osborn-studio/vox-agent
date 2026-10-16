"""Offline adapter refusal vectors; no runner/service invocation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from verify_mobile_canonical_ci import STAGES, check_runner, validate_report


class CanonicalCI(unittest.TestCase):
    def setUp(self):
        self.report = {
            "contract": "vox-canonical-signature-v1", "attackRegression": False,
            "unresolvedCompatibility": False, "knownBreakingChange": True,
            "results": [{"name": n, "status": 2 if n == "historical_compatibility" else 0}
                        for n in STAGES],
        }

    def test_explicit_canonical_acceptance(self):
        self.assertEqual(validate_report(json.dumps(self.report), 0), self.report)

    def test_refusal_vectors(self):
        mutations = []
        for key, value in [("contract", "historical-forge"), ("attackRegression", True),
                           ("unresolvedCompatibility", True), ("knownBreakingChange", False)]:
            m = copy.deepcopy(self.report); m[key] = value; mutations.append(m)
        for index, value in [(0, 2), (0, None), (0, False), (-1, 0), (-1, 1)]:
            m = copy.deepcopy(self.report); m["results"][index]["status"] = value; mutations.append(m)
        m = copy.deepcopy(self.report); m["results"].pop(); mutations.append(m)
        m = copy.deepcopy(self.report); m["results"][1] = m["results"][0]; mutations.append(m)
        for error in ["timed out", "", 0, False, [], {}]:
            m = copy.deepcopy(self.report); m["results"][0]["error"] = error; mutations.append(m)
        for m in mutations:
            with self.subTest(m=m), self.assertRaises(ValueError):
                validate_report(json.dumps(m), 0)
        for status in [1, 2, 3, None]:
            with self.assertRaises(ValueError):
                validate_report(json.dumps(self.report), status)

    def test_missing_malformed_and_duplicate_reports(self):
        for text in ["", "{}", "[]", "null", "not json", '{"contract":"x","contract":"y"}']:
            with self.assertRaises(ValueError):
                validate_report(text, 0)

    def test_external_pin(self):
        import hashlib
        with tempfile.TemporaryDirectory(prefix="vox-owned-ci-pin-") as d:
            p = Path(d) / "runner"; p.write_bytes(b"original")
            pin = hashlib.sha256(p.read_bytes()).hexdigest()
            check_runner(p, pin)
            p.write_bytes(b"changed")
            with self.assertRaises(ValueError): check_runner(p, pin)
            with self.assertRaises(ValueError): check_runner(p, "")

    def test_delivery_pin_equality(self):
        root = Path(__file__).resolve().parent.parent
        delivery = root / "security/mobile-candidate/delivery"
        pin = json.loads((delivery / "inventory.json").read_text())["runnerSHA256"]
        self.assertIn("--runner-sha256 " + pin, (delivery / "ci-step-proposal.yml").read_text())
        check_runner(root / "scripts/verify_mobile_candidate_safety.cjs", pin)


if __name__ == "__main__":
    unittest.main()
