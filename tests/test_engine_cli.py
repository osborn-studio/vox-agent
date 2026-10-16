"""Tests for engine.cli — command line interface."""

import json

import httpx
import pytest
from unittest.mock import patch
from engine.cli import main


class TestCLIHelp:
    def test_no_args_shows_help(self, capsys):
        with patch("sys.argv", ["vox-engine"]):
            main()
        output = capsys.readouterr().out
        assert "vox-engine" in output.lower() or "usage" in output.lower()

    def test_start_in_args(self, capsys):
        """Verify 'start' is a valid subcommand (don't actually start)."""
        with patch("sys.argv", ["vox-engine", "--help"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0
        output = capsys.readouterr().out
        assert "start" in output
        assert "task" in output
        assert "tools" in output

    def test_help_lists_new_subcommands(self, capsys):
        with patch("sys.argv", ["vox-engine", "--help"]):
            with pytest.raises(SystemExit):
                main()
        output = capsys.readouterr().out
        assert "memory" in output
        assert "device" in output
        assert "tokens" in output
        assert "serve-mcp" in output


class TestBareSubcommandsRequireSub:
    """`vox-engine memory` / `device` / `tokens` with no sub-subcommand must
    error with a nonzero exit instead of silently doing nothing.
    """

    def test_bare_memory_errors(self, capsys):
        with patch("sys.argv", ["vox-engine", "memory"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        assert "memory_command" in capsys.readouterr().err

    def test_bare_device_errors(self, capsys):
        with patch("sys.argv", ["vox-engine", "device"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        assert "device_command" in capsys.readouterr().err

    def test_bare_tokens_errors(self, capsys):
        with patch("sys.argv", ["vox-engine", "tokens"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        assert "tokens_command" in capsys.readouterr().err


class TestMemorySearch:
    def test_search_prints_json_results(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/search?q=mobile&limit=10",
            json={
                "query": "mobile",
                "results": [
                    {
                        "node": {"id": "mem_1", "title": "Ship the mobile companion"},
                        "score": 1.0,
                        "highlights": ["mobile"],
                    }
                ],
            },
        )
        with patch("sys.argv", ["vox-engine", "memory", "search", "mobile"]):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["query"] == "mobile"
        assert data["results"][0]["node"]["title"] == "Ship the mobile companion"

    def test_search_respects_limit_flag(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/search?q=japan&limit=3",
            json={"query": "japan", "results": []},
        )
        with patch("sys.argv", ["vox-engine", "memory", "search", "japan", "--limit", "3"]):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["query"] == "japan"

    def test_search_pretty_prints_summary(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/search?q=mobile&limit=10",
            json={
                "query": "mobile",
                "results": [
                    {"node": {"id": "mem_1", "title": "Ship it"}, "score": 1.0, "highlights": []}
                ],
            },
        )
        with patch("sys.argv", ["vox-engine", "memory", "search", "mobile", "--pretty"]):
            main()
        output = capsys.readouterr().out
        assert "mem_1" in output
        assert "Ship it" in output

    def test_search_non_200_exits_nonzero_without_raising(self, httpx_mock, capsys):
        # A 500 with a non-JSON body must not raise JSONDecodeError — it
        # should print a clean error and exit nonzero.
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/search?q=mobile&limit=10",
            status_code=500,
            text="internal server error",
        )
        with patch("sys.argv", ["vox-engine", "memory", "search", "mobile"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        output = capsys.readouterr().out
        assert "failed" in output.lower()


class TestMemoryRecent:
    def test_recent_prints_json(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/recent?days=7&limit=20",
            json=[{"id": "mem_1", "title": "Recent node", "createdAt": "2026-07-03T00:00:00+00:00"}],
        )
        with patch("sys.argv", ["vox-engine", "memory", "recent"]):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data[0]["id"] == "mem_1"

    def test_recent_days_and_limit_flags(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/recent?days=14&limit=5",
            json=[],
        )
        with patch("sys.argv", ["vox-engine", "memory", "recent", "--days", "14", "--limit", "5"]):
            main()
        output = capsys.readouterr().out
        assert json.loads(output) == []

    def test_recent_non_200_exits_nonzero_without_raising(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/memory/recent?days=7&limit=20",
            status_code=503,
            text="service unavailable",
        )
        with patch("sys.argv", ["vox-engine", "memory", "recent"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        output = capsys.readouterr().out
        assert "failed" in output.lower()


class TestDevicePairingCode:
    def test_pairing_code_prints_json(self, httpx_mock, capsys):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/devices/pairing-codes",
            json={"code": "ABC123", "qrPayload": "vox://pair/ABC123", "expiresAt": "2026-07-03T01:00:00+00:00"},
        )
        with patch("sys.argv", ["vox-engine", "device", "pairing-code"]):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["code"] == "ABC123"

    def test_pairing_code_pretty_prints_summary(self, httpx_mock, capsys):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/devices/pairing-codes",
            json={"code": "ABC123", "qrPayload": "vox://pair/ABC123", "expiresAt": "2026-07-03T01:00:00+00:00"},
        )
        with patch("sys.argv", ["vox-engine", "device", "pairing-code", "--pretty"]):
            main()
        output = capsys.readouterr().out
        assert "ABC123" in output

    def test_pairing_code_non_200_exits_nonzero_without_raising(self, httpx_mock, capsys):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/devices/pairing-codes",
            status_code=500,
            text="internal server error",
        )
        with patch("sys.argv", ["vox-engine", "device", "pairing-code"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        output = capsys.readouterr().out
        assert "failed" in output.lower()


class TestDevicePair:
    def test_pair_posts_code_owner_workspace(self, httpx_mock, capsys):
        def custom_response(request: httpx.Request):
            body = json.loads(request.content)
            assert body == {"code": "ABC123", "ownerUserId": "user_1", "workspaceId": "ws_1"}
            return httpx.Response(200, json={"id": "dev_1", "status": "CONNECTED"})

        httpx_mock.add_callback(custom_response, url="http://127.0.0.1:18900/v1/devices/pair")
        with patch(
            "sys.argv",
            ["vox-engine", "device", "pair", "ABC123", "--owner", "user_1", "--workspace", "ws_1"],
        ):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["id"] == "dev_1"
        assert data["status"] == "CONNECTED"


class TestDeviceSimulateRecord:
    def test_simulate_record_posts_event_and_prints_session(self, httpx_mock, capsys):
        def custom_response(request: httpx.Request):
            body = json.loads(request.content)
            assert body == {"type": "RECORD_BUTTON_PRESSED", "payload": {}}
            return httpx.Response(
                200,
                json={"id": "evt_1", "deviceId": "dev_1", "type": "RECORD_BUTTON_PRESSED", "sessionId": "cap_1"},
            )

        httpx_mock.add_callback(custom_response, url="http://127.0.0.1:18900/v1/devices/dev_1/events")
        with patch("sys.argv", ["vox-engine", "device", "simulate-record", "dev_1"]):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["sessionId"] == "cap_1"


class TestTokensBalance:
    def test_balance_prints_json(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/tokens/balance",
            json={"scopeId": "user_local", "scopeType": "USER", "balance": 99000, "currency": "TOKENS"},
        )
        with patch("sys.argv", ["vox-engine", "tokens", "balance"]):
            main()
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["balance"] == 99000

    def test_balance_pretty_prints_summary(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/tokens/balance",
            json={"scopeId": "user_local", "scopeType": "USER", "balance": 99000, "currency": "TOKENS"},
        )
        with patch("sys.argv", ["vox-engine", "tokens", "balance", "--pretty"]):
            main()
        output = capsys.readouterr().out
        assert "99000" in output or "99,000" in output

    def test_balance_non_200_exits_nonzero_without_raising(self, httpx_mock, capsys):
        httpx_mock.add_response(
            url="http://127.0.0.1:18900/v1/tokens/balance",
            status_code=500,
            text="internal server error",
        )
        with patch("sys.argv", ["vox-engine", "tokens", "balance"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        output = capsys.readouterr().out
        assert "failed" in output.lower()


class TestCaptureProcess:
    def test_capture_process_posts_and_prints_json(self, capsys, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/process",
            json={"id": "cap_1", "status": "READY", "detectedLanguages": ["zh"]},
        )
        with patch(
            "sys.argv",
            ["vox-engine", "capture", "process", "cap_1", "--audio", "/tmp/a.wav", "--asr", "sherpa"],
        ):
            main()
        output = capsys.readouterr().out
        assert '"READY"' in output

    def test_capture_process_sends_body(self, capsys, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/process",
            json={"id": "cap_1", "status": "READY", "detectedLanguages": []},
        )
        with patch(
            "sys.argv",
            ["vox-engine", "capture", "process", "cap_1", "--audio", "/tmp/a.wav", "--asr", "sherpa"],
        ):
            main()
        request = httpx_mock.get_requests()[0]
        body = json.loads(request.content)
        assert body == {"audioPath": "/tmp/a.wav", "asrAdapter": "sherpa"}

    def test_capture_process_defaults_to_empty_body(self, capsys, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/process",
            json={"id": "cap_1", "status": "READY", "detectedLanguages": []},
        )
        with patch("sys.argv", ["vox-engine", "capture", "process", "cap_1"]):
            main()
        assert json.loads(httpx_mock.get_requests()[0].content) == {}

    def test_capture_process_non_200_exits_nonzero_without_raising(self, capsys, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/process",
            status_code=500,
            text="internal server error",
        )
        with patch("sys.argv", ["vox-engine", "capture", "process", "cap_1"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        output = capsys.readouterr().out
        assert "failed" in output.lower()

    def test_capture_process_pretty_prints_summary(self, capsys, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/process",
            json={"id": "cap_1", "status": "READY", "detectedLanguages": ["zh", "en"]},
        )
        with patch(
            "sys.argv",
            ["vox-engine", "capture", "process", "cap_1", "--pretty"],
        ):
            main()
        output = capsys.readouterr().out
        assert "cap_1" in output
        assert "READY" in output
        assert "zh" in output and "en" in output


class TestCaptureUpload:
    def test_upload_posts_file_and_prints_json(self, tmp_path, capsys, httpx_mock):
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFFxxxx")
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/audio",
            json={"sessionId": "cap_1", "audioPath": "/x/cap_1.wav", "bytes": 8},
        )
        with patch("sys.argv", ["vox-engine", "capture", "upload", "cap_1", "--file", str(wav)]):
            main()
        output = capsys.readouterr().out
        assert '"cap_1"' in output
        # a multipart body was sent (not JSON)
        req = httpx_mock.get_requests()[0]
        assert req.headers["content-type"].startswith("multipart/form-data")
        assert b"RIFFxxxx" in req.content

    def test_upload_pretty_prints_summary(self, tmp_path, capsys, httpx_mock):
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"data")
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/audio",
            json={"sessionId": "cap_1", "audioPath": "/x/cap_1.wav", "bytes": 4},
        )
        with patch("sys.argv", ["vox-engine", "capture", "upload", "cap_1", "-f", str(wav), "--pretty"]):
            main()
        output = capsys.readouterr().out
        assert "4 bytes" in output
        assert "capture process cap_1" in output  # next-step hint

    def test_upload_missing_file_exits_nonzero(self, capsys):
        with patch("sys.argv", ["vox-engine", "capture", "upload", "cap_1", "--file", "/no/such.wav"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        assert "not found" in capsys.readouterr().out.lower()

    def test_upload_non_200_exits_nonzero(self, tmp_path, capsys, httpx_mock):
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"data")
        httpx_mock.add_response(
            method="POST",
            url="http://127.0.0.1:18900/v1/capture/sessions/cap_1/audio",
            status_code=404,
            text="capture session not found",
        )
        with patch("sys.argv", ["vox-engine", "capture", "upload", "cap_1", "--file", str(wav)]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        assert "failed" in capsys.readouterr().out.lower()


class TestBareCaptureRequiresSub:
    def test_bare_capture_errors(self, capsys):
        with patch("sys.argv", ["vox-engine", "capture"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        assert "capture_command" in capsys.readouterr().err


class TestServeMcp:
    def test_serve_mcp_prints_hint_when_sdk_missing(self, capsys, monkeypatch):
        # Force the "SDK absent" branch deterministically (the real mcp SDK
        # may or may not be installed in a given environment, and if
        # present, serve() would otherwise start a blocking server). serve()
        # is expected to raise RuntimeError with an install hint; the CLI
        # command catches it, prints the hint, and exits nonzero.
        import voxagent_mcp.voxagent_server as srv

        monkeypatch.setattr(srv, "_load_mcp_sdk", lambda: None)
        with patch("sys.argv", ["vox-engine", "serve-mcp"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code != 0
        output = capsys.readouterr().out
        assert "pip install mcp" in output
