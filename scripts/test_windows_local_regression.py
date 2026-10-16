"""Tests for the Windows 4.2.8 local contract/security regression (T44a)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import windows_local_regression as reg  # noqa: E402


def result(check_id, exit_code=0, summary="1 passed"):
    return reg.CheckResult(check_id=check_id, exit_code=exit_code, seconds=1.0,
                           summary=summary, output_sha256="0" * 64)


class ReportTest(unittest.TestCase):
    def all_passed(self):
        return [result(check.id) for check in reg.CHECKS]

    def test_all_checks_passing_is_a_local_scope_pass_never_a_product_pass(self):
        report = reg.build_report(self.all_passed(), source_commit="a" * 40, tree_dirty=False)
        self.assertEqual(report["verdict"], "LOCAL_SCOPE_PASS")
        self.assertIs(report["productPass"], False)
        self.assertTrue(report["exclusions"], "exclusions are always listed")
        self.assertIn("not a full product pass", report["statement"])

    def test_any_failing_check_fails_the_run(self):
        results = self.all_passed()
        results[0] = result(results[0].check_id, exit_code=1)
        self.assertEqual(reg.build_report(results, "a" * 40, False)["verdict"], "FAIL")

    def test_a_check_that_did_not_run_makes_the_run_partial(self):
        results = self.all_passed()[1:]
        report = reg.build_report(results, "a" * 40, False)
        self.assertEqual(report["verdict"], "PARTIAL")
        self.assertEqual(report["notRun"], [reg.CHECKS[0].id])

    def test_a_dirty_tree_is_recorded_and_cannot_pass(self):
        report = reg.build_report(self.all_passed(), "a" * 40, True)
        self.assertTrue(report["treeDirty"])
        self.assertEqual(report["verdict"], "PARTIAL")

    def test_an_unknown_commit_cannot_pass(self):
        for commit in ("", "not-a-sha", None):
            report = reg.build_report(self.all_passed(), commit, False)
            self.assertEqual(report["verdict"], "PARTIAL", commit)

    def test_a_tree_that_changed_during_the_run_cannot_pass(self):
        report = reg.build_report(self.all_passed(), "a" * 40, False, tree_changed=True)
        self.assertEqual(report["verdict"], "PARTIAL")
        self.assertTrue(report["treeChangedDuringRun"])

    def test_unknown_results_are_refused(self):
        with self.assertRaises(ValueError):
            reg.build_report([result("made-up")], "a" * 40, False)


class GitStateTest(unittest.TestCase):
    def test_a_failing_git_query_reports_an_unknown_state(self):
        original = reg._git
        reg._git = lambda *args: None
        try:
            self.assertEqual(reg.tree_state(), (None, None))
        finally:
            reg._git = original


class CoverageTest(unittest.TestCase):
    def test_every_t44a_dependency_is_covered_or_explicitly_excluded(self):
        catalog = json.loads(reg.OVERLAY.read_text(encoding="utf-8"))
        task = next(t for t in catalog["tasks"] if t["id"] == "T44a")
        missing = reg.uncovered(task["dependencies"])
        self.assertEqual(missing, [])
        self.assertEqual(sorted(reg.dependencies()), sorted(task["dependencies"]))

    def test_an_uncovered_dependency_is_reported(self):
        self.assertEqual(reg.uncovered(["T99z"]), ["T99z"])

    def test_exclusions_name_a_reason_and_where_they_are_verified(self):
        for item in reg.EXCLUSIONS:
            self.assertTrue(item.reason and item.verified_by, item.id)


class SummaryTest(unittest.TestCase):
    def test_only_count_lines_are_kept_from_output(self):
        output = "\n".join([
            "/Users/someone/secret/path.py::test_x PASSED",
            "token=vox_sso_abcdef",
            "2145 passed, 9 skipped in 161.11s (0:02:41)",
            "test result: ok. 10 passed; 0 failed; 0 ignored",
            "ℹ pass 112",
            "All checks passed!",
        ])
        summary = reg.summarize(output)
        self.assertNotIn("/Users", summary)
        self.assertNotIn("vox_sso", summary)
        self.assertIn("2145 passed", summary)
        self.assertIn("pass 112", summary)
        self.assertIn("All checks passed!", summary)

    def test_the_leak_scan_counts_are_kept(self):
        summary = reg.summarize('{\n  "scanned_files": 4,\n  "hits": [],\n  "markers": ["text"]\n}')
        self.assertEqual(summary, '"scanned_files": 4\n"hits": []')

    def test_trailing_text_on_a_count_line_is_never_kept(self):
        summary = reg.summarize("1 passed token: SYNTHETIC_SECRET\nFAILED sensitive original text\n"
                                "test result: ok. 3 passed; 0 failed; extra SECRET")
        self.assertNotIn("SECRET", summary)
        self.assertNotIn("sensitive", summary)
        self.assertIn("1 passed", summary)
        self.assertIn("FAILED", summary)

    def test_commands_are_repository_relative(self):
        for check in reg.CHECKS:
            self.assertFalse(Path(check.cwd).is_absolute(), check.id)
            for part in check.command:
                self.assertNotIn("/Users/", part, check.id)


if __name__ == "__main__":
    unittest.main()
