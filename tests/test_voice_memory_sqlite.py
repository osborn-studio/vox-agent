"""SQLite-specific tests for SqliteVoiceMemoryRepository.

Contract parity with the in-memory repo is covered by the parametrized
fixture in test_voice_memory_contract_flow.py (Task 5); this file tests
what only SQLite has: durability across reconnect and schema bootstrap.
"""

from engine.voice_memory_sqlite import SqliteVoiceMemoryRepository


def _payload():
    return {
        "workspaceId": "ws_t", "ownerUserId": "u_t", "source": "MOBILE",
        "mode": "MEETING", "languageHints": ["en"], "privacyLevel": "PERSONAL",
        "consentState": "GRANTED", "deviceIds": [], "localOnlyUntilSync": False,
    }


class TestDurability:
    def test_data_survives_reconnect(self, tmp_path):
        db = tmp_path / "vm.db"
        repo = SqliteVoiceMemoryRepository(db)
        session = repo.create_session(_payload())
        repo.add_segment(session["id"], {"speakerId": "s", "startMs": 0, "endMs": 5,
                                         "textRaw": "hi", "textNormalized": "Hi.",
                                         "confidence": 0.9, "model": "m", "language": "en"})
        node = repo.add_memory_node({"workspaceId": "ws_t", "sourceSessionId": session["id"],
                                     "type": "DECISION", "title": "T", "body": "B",
                                     "tags": ["x"], "confidence": 0.8})
        repo.close()

        reopened = SqliteVoiceMemoryRepository(db)
        assert reopened.get_session(session["id"])["mode"] == "MEETING"
        assert len(reopened.list_segments(session["id"])) == 1
        assert reopened.get_node(node["id"])["tags"] == ["x"]
        assert reopened.search_memory("T")["results"][0]["node"]["id"] == node["id"]
        reopened.close()

    def test_update_session_persists(self, tmp_path):
        db = tmp_path / "vm.db"
        repo = SqliteVoiceMemoryRepository(db)
        session = repo.create_session(_payload())
        repo.update_session(session["id"], {"status": "READY", "detectedLanguages": ["en"]})
        repo.close()
        reopened = SqliteVoiceMemoryRepository(db)
        stored = reopened.get_session(session["id"])
        assert stored["status"] == "READY"
        assert stored["detectedLanguages"] == ["en"]
        reopened.close()

    def test_segment_order_preserved(self, tmp_path):
        repo = SqliteVoiceMemoryRepository(tmp_path / "vm.db")
        session = repo.create_session(_payload())
        for i in range(5):
            repo.add_segment(session["id"], {"speakerId": "s", "startMs": i * 10,
                                             "endMs": i * 10 + 5, "textRaw": f"t{i}",
                                             "textNormalized": f"T{i}", "confidence": 0.9,
                                             "model": "m", "language": "en"})
        texts = [s["textRaw"] for s in repo.list_segments(session["id"])]
        assert texts == ["t0", "t1", "t2", "t3", "t4"]
        repo.close()


class TestSchema:
    def test_uses_shared_windows_safe_connection_policy(self, tmp_path):
        repo = SqliteVoiceMemoryRepository(tmp_path / "vm.db")

        assert repo._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert repo._db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert repo._db.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert repo._db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        repo.close()

    def test_bootstrap_is_idempotent(self, tmp_path):
        db = tmp_path / "vm.db"
        SqliteVoiceMemoryRepository(db).close()
        SqliteVoiceMemoryRepository(db).close()  # second open migrates cleanly
