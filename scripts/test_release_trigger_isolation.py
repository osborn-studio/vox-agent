import tempfile
import unittest
from pathlib import Path

from verify_release_trigger_isolation import check_repo, check_workflow_text, tag_matches_push_filter

PREVIEW_TAG = 'v4.2.9-preview.1'
FINAL_TAG = 'v4.2.9'


class TriggerIsolationTests(unittest.TestCase):
    def workflow(self, patterns):
        lines = ['on:', '  push:', '    tags:'] + [f'      - "{p}"' for p in patterns]
        return '\n'.join(lines + ['  workflow_dispatch: {}', 'jobs: {}', ''])

    def test_wildcard_v_star_matches_preview_tags_and_is_rejected(self):
        text = self.workflow(['v*'])
        self.assertTrue(tag_matches_push_filter(text, PREVIEW_TAG))
        self.assertTrue(tag_matches_push_filter(text, FINAL_TAG))

    def test_strict_numeric_pattern_matches_final_but_not_preview(self):
        text = self.workflow(['v[0-9]+.[0-9]+.[0-9]+'])
        self.assertTrue(tag_matches_push_filter(text, FINAL_TAG))
        self.assertFalse(tag_matches_push_filter(text, PREVIEW_TAG))
        self.assertFalse(tag_matches_push_filter(text, 'v4.2.9.1'))
        self.assertFalse(tag_matches_push_filter(text, 'v4.2'))

    def test_question_mark_means_optional_previous_character(self):
        # GitHub: '?' matches zero or one of the preceding character.
        text = self.workflow(['v4.2.9-preview.1?'])
        self.assertTrue(tag_matches_push_filter(text, 'v4.2.9-preview.'))
        self.assertTrue(tag_matches_push_filter(text, PREVIEW_TAG))
        self.assertFalse(tag_matches_push_filter(text, 'v4.2.9-preview.2'))

    def test_codex_counterexample_optional_suffix_is_not_a_false_pass(self):
        text = self.workflow(['v[0-9]+.[0-9]+.[0-9]+', 'v4.2.9-preview.1?'])
        self.assertTrue(tag_matches_push_filter(text, PREVIEW_TAG))
        self.assertEqual(check_workflow_text(text)['status'], 'BLOCKED')

    def test_later_positive_pattern_reincludes_after_a_negative_one(self):
        text = self.workflow(['v[0-9]+.[0-9]+.[0-9]+', '!v*-preview.*', 'v4.2.9-preview.1'])
        self.assertTrue(tag_matches_push_filter(text, PREVIEW_TAG))
        self.assertEqual(check_workflow_text(text)['status'], 'BLOCKED')

    def test_trailing_negative_pattern_excludes(self):
        text = self.workflow(['v*', '!v*-*'])
        self.assertFalse(tag_matches_push_filter(text, PREVIEW_TAG))
        self.assertTrue(tag_matches_push_filter(text, FINAL_TAG))

    def test_push_without_a_readable_tag_filter_fails_closed(self):
        text = 'on:\n  push:\n    branches: [main]\n    tags-ignore:\n      - "v*-preview*"\njobs: {}\n'
        self.assertEqual(check_workflow_text(text)['status'], 'BLOCKED')
        bare = 'on:\n  push:\njobs: {}\n'
        self.assertEqual(check_workflow_text(bare)['status'], 'BLOCKED')

    def test_yaml_escape_sequences_cannot_smuggle_a_wildcard(self):
        # YAML decodes "\\u0076*" to "v*"; a raw-text checker would call this safe.
        text = self.workflow(['v[0-9]+.[0-9]+.[0-9]+', '\\u0076*'])
        result = check_workflow_text(text)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('unparsable_filter_pattern', result['blockers'])

    def test_unquoted_or_unusual_scalars_fail_closed(self):
        unquoted = 'on:\n  push:\n    tags:\n      - v*\njobs: {}\n'
        self.assertEqual(check_workflow_text(unquoted)['status'], 'BLOCKED')
        flow = 'on:\n  push:\n    tags: ["v*"]\njobs: {}\n'
        self.assertEqual(check_workflow_text(flow)['status'], 'BLOCKED')

    def test_invalid_character_class_is_blocked_not_raised(self):
        text = self.workflow(['v[z-a]*'])
        result = check_workflow_text(text)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('unparsable_filter_pattern', result['blockers'])

    def test_duplicate_trigger_keys_fail_closed(self):
        dup_push = ('on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
                    '  push:\n    tags:\n      - "v*"\njobs: {}\n')
        self.assertEqual(check_workflow_text(dup_push)['status'], 'BLOCKED')
        dup_tags = ('on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
                    '    tags:\n      - "v*"\njobs: {}\n')
        self.assertEqual(check_workflow_text(dup_tags)['status'], 'BLOCKED')
        dup_on = ('on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
                  'on:\n  push: {}\njobs: {}\n')
        self.assertEqual(check_workflow_text(dup_on)['status'], 'BLOCKED')

    def test_comment_inside_tag_list_does_not_hide_later_items(self):
        text = ('on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
                '    # comment\n      - "v*"\njobs: {}\n')
        self.assertEqual(check_workflow_text(text)['status'], 'BLOCKED')

    def test_quoted_duplicate_keys_and_tabs_fail_closed(self):
        quoted_dup = ('on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
                      '    "tags":\n      - "v*"\njobs: {}\n')
        self.assertEqual(check_workflow_text(quoted_dup)['status'], 'BLOCKED')
        quoted_on = ('on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
                     '"on":\n  push: {}\njobs: {}\n')
        self.assertEqual(check_workflow_text(quoted_on)['status'], 'BLOCKED')
        tabbed = 'on:\n  push:\n    tags:\n\t- "v*"\njobs: {}\n'
        self.assertEqual(check_workflow_text(tabbed)['status'], 'BLOCKED')

    def test_shorthand_push_without_filters_is_blocked(self):
        # `on: push` fires on every tag push.
        self.assertEqual(check_workflow_text('on: push\njobs: {}\n')['status'], 'BLOCKED')
        self.assertEqual(check_workflow_text('on: [push]\njobs: {}\n')['status'], 'BLOCKED')

    def test_other_events_and_trailing_comments_are_still_accepted(self):
        text = ('on:\n  push:\n    # final releases only\n    tags:\n'
                '      - "v[0-9]+.[0-9]+.[0-9]+"  # strict\n'
                '  workflow_dispatch:\n    inputs:\n      version:\n        type: string\n'
                'jobs: {}\n')
        self.assertEqual(check_workflow_text(text)['status'], 'PASS')

    def test_yaml_boolean_aliases_of_on_are_ambiguous_and_blocked(self):
        # YAML 1.1 reads `on` as boolean true, so a separate `true:`/`On:` key may collide.
        for alias in ('true', 'On', 'yes', 'ON'):
            text = (f'{alias}:\n  push:\n    tags:\n      - "v*"\n'
                    'on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\njobs: {}\n')
            self.assertEqual(check_workflow_text(text)['status'], 'BLOCKED', alias)

    def test_complex_or_spaced_top_level_keys_and_extra_documents_are_blocked(self):
        safe = 'on:\n  push:\n    tags:\n      - "v[0-9]+.[0-9]+.[0-9]+"\n'
        for suffix in ('on :\n  push:\n    tags:\n      - "v*"\n',
                       '? on\n:\n  push:\n    tags:\n      - "v*"\n',
                       '---\non:\n  push:\n    tags:\n      - "v*"\n',
                       'jobs: {}\njobs: {}\n'):
            self.assertEqual(check_workflow_text(safe + suffix)['status'], 'BLOCKED', suffix)

    def test_repository_release_workflow_does_not_fire_on_preview_tags(self):
        repo = Path(__file__).resolve().parents[1]
        result = check_repo(repo)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertFalse(result['preview_tag_triggers'])
        self.assertTrue(result['final_tag_triggers'])

    def test_missing_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            result = check_repo(Path(temp))
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertNotIn(temp, str(result))


if __name__ == '__main__':
    unittest.main()
