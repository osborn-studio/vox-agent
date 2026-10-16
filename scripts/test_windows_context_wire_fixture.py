"""Compare synthetic Windows wire fixtures with actual isolated cloud store receipts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.input_backup import BackupInput, BackupStore, Target  # noqa: E402


class ContextWireFixtureTests(unittest.TestCase):
    def test_actual_cloud_store_request_and_receipt_contract(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] /
            'apps/desktop-tauri/test/fixtures/context-backup-wire-v1.json').read_text())
        self.assertEqual(fixture['version'], 1)
        for case in fixture['cases']:
            with self.subTest(project=case['request'].get('projectId')):
                with tempfile.TemporaryDirectory() as temp:
                    store = BackupStore(Path(temp) / 'synthetic.sqlite')
                    try:
                        request = BackupInput.model_validate(case['request'])
                        target = Target('team', 1, 'synthetic-org', 'oc_synthetic', frozenset({'synthetic-actor'}))
                        result = store.accept('synthetic-org', 'synthetic-actor', request, target,
                            repository_snapshot=request.repository if request.projectId else None)
                        self.assertEqual(result['status'], 'pending')
                        if case['receipt']['status'] == 'delivered':
                            with store.db:
                                store.db.execute("UPDATE backup_outbox SET status='delivered',message_id=?",
                                    (case['receipt']['messageId'],))
                        result = store.status('synthetic-org', 'synthetic-actor', request.eventId)
                        self.assertEqual(result, case['receipt'])
                        self.assertIsNone(store.status('other-org', 'synthetic-actor', request.eventId))
                        self.assertIsNone(store.status('synthetic-org', 'other-actor', request.eventId))
                    finally:
                        store.close()


if __name__ == '__main__':
    unittest.main()
