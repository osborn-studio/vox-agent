# PR-D: Synchronous polish with explicit status

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a client ask the voice backend to polish inline and learn unambiguously whether it happened: `/v1/voice` and `/v1/voice/text` accept `polish=1`, responses carry `polish_status` and, on failure, a bounded `polish_error`; a new `POST /v1/text/polish` polishes typed drafts.

**Architecture:** `runtime/slowpath/polisher.py` gains `PolishOutcome` and `Polisher.polish_outcome()` (structured result, 15 s total deadline) while `polish()` keeps its `None` contract for the slow path. `apps/desktop/server.py` applies polish after the fast path through one helper and adds the new endpoint. The response contracts in TypeScript and Rust gain optional fields so the gateway and the Tauri shell pass them through unchanged.

**Tech Stack:** Python 3.11+/FastAPI/httpx (`httpx.MockTransport` for tests), Zod contracts (`packages/contracts`), serde in `apps/desktop-tauri/src-tauri/src/contracts.rs`.

---

### Task 1: `PolishOutcome` and `Polisher.polish_outcome`

**Files:**
- Modify: `runtime/slowpath/polisher.py`
- Test: `tests/test_polisher_outcome.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_polisher_outcome.py
"""polish_outcome reports ok / skipped / failed without leaking provider bodies."""
import asyncio
import json

import httpx
import pytest

from runtime.llm_provider import LlmClient
from runtime.models.config import VoxAgentConfig
from runtime.slowpath.polisher import PolishOutcome, Polisher


def _config(llm_main: str = "qwen-turbo", key: str = "k") -> VoxAgentConfig:
    config = VoxAgentConfig()
    # Defaults read the environment (runtime/models/config.py:62-63, :102); pin them.
    config.dashscope_key_cn = key
    config.dashscope_key_us = ""  # the Qwen route falls back to the US key when CN is empty
    config.api_token = ""
    config.profiles[config.active_profile].llm_main = llm_main
    return config


def _client(handler) -> LlmClient:
    return LlmClient(transport=httpx.MockTransport(handler), timeout_seconds=2.0)


def test_ok_outcome_returns_polished_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["messages"][1]["content"] == "嗯 那个 测试"
        return httpx.Response(200, json={"choices": [{"message": {"content": "测试"}}]})

    outcome = asyncio.run(Polisher(client=_client(handler)).polish_outcome("嗯 那个 测试", _config()))
    assert outcome == PolishOutcome(status="ok", text="测试", error=None)


def test_skipped_when_provider_not_configured():
    outcome = asyncio.run(Polisher(client=_client(lambda r: httpx.Response(500))).polish_outcome("x", _config(key="")))
    assert outcome.status == "skipped"
    assert outcome.text is None
    assert outcome.error == "qwen-turbo: not configured"


def test_skipped_on_blank_input():
    outcome = asyncio.run(Polisher(client=_client(lambda r: httpx.Response(500))).polish_outcome("   ", _config()))
    assert outcome == PolishOutcome(status="skipped", text=None, error="empty input")


def test_failed_carries_only_provider_label_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "SECRET-BODY sk-live-123"}})

    outcome = asyncio.run(Polisher(client=_client(handler)).polish_outcome("测试", _config()))
    assert outcome.status == "failed"
    assert outcome.text is None
    assert "429" in outcome.error
    assert "SECRET" not in outcome.error and "sk-live" not in outcome.error


def test_failed_on_total_deadline():
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(200, json={"choices": [{"message": {"content": "late"}}]})

    polisher = Polisher(client=_client(slow), deadline_seconds=0.05)
    outcome = asyncio.run(polisher.polish_outcome("测试", _config()))
    assert outcome.status == "failed"
    assert outcome.error == "Qwen Bailian: timed out"


def test_legacy_polish_contract_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert asyncio.run(Polisher(client=_client(handler)).polish("测试", _config())) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_polisher_outcome.py -q`
Expected: FAIL with `ImportError: cannot import name 'PolishOutcome'`.

- [ ] **Step 3: Check how `VoxAgentConfig` exposes profiles**

Run: `grep -n "profiles\|active_profile\|def profile" runtime/models/config.py | head`
Expected: a `profiles: dict[str, Profile]` field, an `active_profile: str`, and a `profile` property. If the field names differ, adjust `_config()` in the test to mutate the active `Profile.llm_main` through the real names; do not add new config fields.

- [ ] **Step 4: Implement**

Replace the class in `runtime/slowpath/polisher.py` with:

```python
from dataclasses import dataclass
from typing import Literal

from runtime.llm_provider import LlmClient, LlmProviderError, resolve_llm_provider

PolishStatus = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True)
class PolishOutcome:
    """Result of one synchronous polish attempt.

    `error` carries only a provider label and a failure class (HTTP status,
    timeout, not configured); never a response body or a credential.
    """

    status: PolishStatus
    text: str | None
    error: str | None


class Polisher:
    def __init__(self, *, client: LlmClient | None = None, deadline_seconds: float = 15.0) -> None:
        self._client = client or LlmClient(timeout_seconds=15.0)
        self._deadline_seconds = deadline_seconds

    async def polish_outcome(self, text: str, config: VoxLogConfig) -> PolishOutcome:
        if not text or not text.strip():
            return PolishOutcome("skipped", None, "empty input")
        llm_name = config.profile.llm_main
        provider = resolve_llm_provider(config, llm_name)
        if not provider:
            logger.debug("polisher.skip", provider=llm_name, reason="provider_not_configured")
            return PolishOutcome("skipped", None, f"{llm_name}: not configured")
        try:
            polished = await asyncio.wait_for(
                self._client.complete(
                    provider,
                    system_prompt=POLISH_PROMPT,
                    user_text=text,
                    max_tokens=2_000,
                ),
                timeout=self._deadline_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("polisher.fail", provider=provider.id, error="deadline")
            return PolishOutcome("failed", None, f"{provider.label}: timed out")
        except LlmProviderError as exc:
            logger.warning("polisher.fail", provider=provider.id, error=str(exc)[:100])
            return PolishOutcome("failed", None, _bounded_error(provider.label, str(exc)))
        except Exception as exc:  # noqa: BLE001 - never raise into the request path
            logger.warning("polisher.fail", provider=provider.id, error=type(exc).__name__)
            return PolishOutcome("failed", None, f"{provider.label}: {type(exc).__name__}")
        return PolishOutcome("ok", polished, None)

    async def polish(self, text: str, config: VoxLogConfig) -> str | None:
        outcome = await self.polish_outcome(text, config)
        return outcome.text if outcome.status == "ok" else None


_STATUS = re.compile(r"\((\d{3})\)")


def _bounded_error(label: str, message: str) -> str:
    """Keep the provider label and an HTTP status if the client reported one."""
    match = _STATUS.search(message)
    if match:
        return f"{label}: HTTP {match.group(1)}"
    if "timed out" in message:
        return f"{label}: timed out"
    return f"{label}: request failed"
```

Add `import asyncio` and `import re` at the top of the module. `LlmProviderError` messages are already bounded by `runtime/llm_provider.py` (label plus status); `_bounded_error` only normalises them.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_polisher_outcome.py -q`
Expected: `6 passed`.

- [ ] **Step 6: Run the slow-path tests that already cover `polish()`**

Run: `.venv/bin/python -m pytest tests -q -k "polish or slowpath or worker" --tb=short`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add runtime/slowpath/polisher.py tests/test_polisher_outcome.py
git commit -m "Add PolishOutcome with a total deadline and bounded error text

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: Backend helper and response fields

**Files:**
- Modify: `apps/desktop/server.py` (`_fastpath_response_payload`, `/v1/voice`, `/v1/voice/text`)
- Test: `tests/test_desktop_sync_polish.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_sync_polish.py
"""Inline polish is opt-in, explicit in the response, and never sends silently."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from apps.desktop import server
from runtime.models.config import VoxAgentConfig
from runtime.slowpath.polisher import PolishOutcome


class FakePolisher:
    def __init__(self, outcome: PolishOutcome) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def polish_outcome(self, text: str, config) -> PolishOutcome:
        self.calls.append(text)
        return self.outcome


class FakeCorrector:
    def correct_with_trace(self, text: str):
        return text, []


class FakeRecent:
    def put(self, *args, **kwargs) -> None:
        return None


@pytest.fixture
def client(monkeypatch):
    config = VoxAgentConfig()
    config.api_token = ""  # the default reads the environment; keep verify_token open in tests
    monkeypatch.setattr(server, "_config", config)
    monkeypatch.setattr(server, "_corrector", FakeCorrector())
    monkeypatch.setattr(server, "_store", object())
    monkeypatch.setattr(server, "_recent", FakeRecent())
    monkeypatch.setattr(server, "enqueue", lambda event: None)
    return TestClient(server.app)


def test_text_endpoint_without_flag_reports_skipped(client, monkeypatch):
    fake = FakePolisher(PolishOutcome("ok", "SHOULD NOT BE USED", None))
    monkeypatch.setattr(server, "_polisher", fake)
    body = client.post("/v1/voice/text", json={"text": "嗯 测试"}).json()
    assert body["polish_status"] == "skipped"
    assert body["polished_text"] == body["display_text"] == "嗯 测试"
    assert "polish_error" not in body
    assert fake.calls == []


def test_text_endpoint_with_flag_returns_polished(client, monkeypatch):
    fake = FakePolisher(PolishOutcome("ok", "测试", None))
    monkeypatch.setattr(server, "_polisher", fake)
    body = client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True}).json()
    assert body["polish_status"] == "ok"
    assert body["polished_text"] == "测试"
    assert body["display_text"] == "嗯 测试"
    assert fake.calls == ["嗯 测试"]


def test_text_endpoint_failure_keeps_display_text_and_reports_error(client, monkeypatch):
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("failed", None, "Qwen Bailian: HTTP 429")))
    body = client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True}).json()
    assert body["polish_status"] == "failed"
    assert body["polished_text"] == "嗯 测试"
    assert body["polish_error"] == "Qwen Bailian: HTTP 429"


def test_polish_endpoint_ok(client, monkeypatch):
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("ok", "测试", None)))
    response = client.post("/v1/text/polish", json={"text": "嗯 测试"})
    assert response.status_code == 200
    assert response.json() == {"polish_status": "ok", "polished_text": "测试"}


def test_polish_endpoint_failed_and_skipped(client, monkeypatch):
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("failed", None, "Qwen Bailian: timed out")))
    assert client.post("/v1/text/polish", json={"text": "x"}).json() == {
        "polish_status": "failed", "polished_text": "x", "polish_error": "Qwen Bailian: timed out",
    }
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("skipped", None, "qwen-turbo: not configured")))
    assert client.post("/v1/text/polish", json={"text": "x"}).json() == {
        "polish_status": "skipped", "polished_text": "x", "polish_error": "qwen-turbo: not configured",
    }


def test_polish_endpoint_rejects_empty_and_oversized(client, monkeypatch):
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("ok", "x", None)))
    assert client.post("/v1/text/polish", json={"text": "  "}).status_code == 422
    assert client.post("/v1/text/polish", json={"text": "a" * 8193}).status_code == 413


def test_apply_polish_helper_sets_every_field_explicitly():
    ok = server._apply_polish({"display_text": "原文", "polished_text": "原文"}, PolishOutcome("ok", "润色", None))
    assert ok["polished_text"] == "润色" and ok["polish_status"] == "ok" and "polish_error" not in ok
    skipped = server._apply_polish({"display_text": "原文", "polished_text": "原文"}, None)
    assert skipped["polished_text"] == "原文" and skipped["polish_status"] == "skipped" and "polish_error" not in skipped
    # Stale fields from an earlier outcome are cleared, never carried over.
    stale = {"display_text": "原文", "polished_text": "旧", "polish_status": "ok", "polish_error": "x"}
    failed = server._apply_polish(stale, PolishOutcome("failed", None, "Qwen Bailian: HTTP 500"))
    assert failed["polished_text"] == "原文" and failed["polish_status"] == "failed" and failed["polish_error"] == "Qwen Bailian: HTTP 500"


def test_inline_polish_result_reaches_the_slow_path_event(client, monkeypatch):
    captured = []
    monkeypatch.setattr(server, "enqueue", lambda event: captured.append(event))
    monkeypatch.setattr(server, "_polisher", FakePolisher(PolishOutcome("ok", "测试", None)))
    client.post("/v1/voice/text", json={"text": "嗯 测试", "polish": True})
    assert captured and captured[0].polished_text == "测试"

```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_desktop_sync_polish.py -q`
Expected: FAIL (`polish_status` missing, `/v1/text/polish` 404, `_apply_polish` missing). If the fixture fails because `verify_token` requires a token, the default `VoxAgentConfig().api_token` is empty and the dependency passes; if `server.app` import triggers the lifespan, note that `TestClient(server.app)` without a `with` block does not run lifespan events, which is what the fixture relies on.

- [ ] **Step 3: Implement the helper and response changes**

In `apps/desktop/server.py`:

```python
POLISH_TEXT_LIMIT = 8_192


def _apply_polish(payload: dict, outcome: "PolishOutcome | None") -> dict:
    """Set the explicit polish fields. Clients key on polish_status, never on text equality."""
    payload["polished_text"] = payload["display_text"]
    payload.pop("polish_error", None)
    if outcome is None:
        payload["polish_status"] = "skipped"
        return payload
    payload["polish_status"] = outcome.status
    if outcome.status == "ok" and outcome.text:
        payload["polished_text"] = outcome.text
    if outcome.error:
        payload["polish_error"] = outcome.error
    return payload


def _polish_requested(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


async def _maybe_polish(text: str, requested: bool) -> "PolishOutcome | None":
    if not requested or _polisher is None:
        return None
    return await _polisher.polish_outcome(text, _config)
```

Add `from runtime.slowpath.polisher import PolishOutcome, Polisher` to the imports (the module already imports `Polisher`).

In `voice_endpoint`, add the form field `polish: str = Form("0")` after `mode`. Inline polish must run **before** `enqueue(event)` so the slow-path worker does not call the provider a second time (`runtime/slowpath/worker.py:82`): replace the block from `# Enqueue for slow path` to the final `return` with:

```python
    outcome = await _maybe_polish(result.display_text, _polish_requested(polish))
    if outcome is not None and outcome.status == "ok" and outcome.text:
        event.polished_text = outcome.text
    enqueue(event)
    _recent.put(result, event, audio_bytes)
    payload = _fastpath_response_payload(result, event, agent=agent, role="me")
    return _apply_polish(payload, outcome)
```

In `text_voice_endpoint`, apply the same order: compute `outcome` from `display_text` and `body.get("polish", False)`, set `event.polished_text` on success, then `enqueue(event)`, `_recent.put(...)`, build the payload and `return _apply_polish(payload, outcome)`.

In `runtime/slowpath/worker.py:82`, make the worker skip provider calls for events that already carry a polished text:

```python
    if (
        event.recording_mode == RecordingMode.NORMAL
        and polisher
        and event.raw_text
        and not event.polished_text  # inline polish already ran for this event
    ):
```

and, before that block, persist an inline result so history sees it: `if event.polished_text: event.archive_status = ArchiveStatus.POLISHED; await archive.update_polish(event.id, event.polished_text)`. Add a test to the existing worker test file (`grep -rln "slowpath.worker\|from runtime.slowpath import worker" tests | head -1`) asserting that an event with `polished_text` set results in zero polisher calls and one `update_polish` call.

Add the new endpoint directly below `text_voice_endpoint`:

```python
@app.post("/v1/text/polish")
async def polish_text_endpoint(request: Request, _auth: None = Depends(verify_token)):
    """Polish a typed draft ("Polish, then send"). Never persists or enqueues."""
    assert _config
    body = await request.json()
    text = str(body.get("text", "")) if isinstance(body, dict) else ""
    if not text.strip():
        raise HTTPException(422, "text required")
    if len(text) > POLISH_TEXT_LIMIT:
        raise HTTPException(413, f"text exceeds {POLISH_TEXT_LIMIT} characters")
    outcome = await _maybe_polish(text, True)
    payload = {"polish_status": "skipped", "polished_text": text}
    if outcome is None:
        payload["polish_error"] = "polisher unavailable"
        return payload
    payload["polish_status"] = outcome.status
    if outcome.status == "ok" and outcome.text:
        payload["polished_text"] = outcome.text
    if outcome.error:
        payload["polish_error"] = outcome.error
    return payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_desktop_sync_polish.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Run the gateway tests that mock these responses**

Run: `.venv/bin/python -m pytest tests/test_gateway_app.py -q --tb=short`
Expected: pass (the gateway mocks only check forwarding; extra keys are additive).

- [ ] **Step 6: Ruff and commit**

```bash
.venv/bin/ruff check apps/desktop/server.py runtime/slowpath/polisher.py tests/test_desktop_sync_polish.py
git add apps/desktop/server.py tests/test_desktop_sync_polish.py
git commit -m "Expose opt-in inline polish with explicit polish_status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 3: TypeScript contract and gateway forwarding

**Files:**
- Modify: `packages/contracts/src/fastpath-result.ts`
- Modify: `services/api-ts/src/routes/voice.ts` (multipart field allowlist, new proxy route)
- Test: `services/api-ts/src/test/voice-polish.test.ts` (`packages/contracts` has no test runner; `services/api-ts` runs `node --import tsx --test src/test/**/*.test.ts`)

- [ ] **Step 1: Write the failing tests**

Model the file on `services/api-ts/src/test/app.test.ts` (same imports, same fake upstream pattern):

```ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { fastPathResponseSchema } from "@vox-agent/contracts"; // use the exact specifier app.test.ts uses

const base = { /* copy the complete valid fast-path object app.test.ts already uses as its upstream reply */ };

test("fast-path schema accepts responses without polish fields", () => {
  assert.equal(fastPathResponseSchema.safeParse(base).success, true);
});

test("fast-path schema accepts polish_status and polish_error, rejects unknown status", () => {
  const ok = fastPathResponseSchema.safeParse({ ...base, polish_status: "failed", polish_error: "Qwen Bailian: HTTP 429" });
  assert.equal(ok.success, true);
  assert.equal(fastPathResponseSchema.safeParse({ ...base, polish_status: "maybe" }).success, false);
});

test("gateway forwards the multipart polish field and proxies /v1/text/polish", async () => {
  // Reuse app.test.ts's fake upstream: assert the upstream /v1/voice request body contains a `polish` part
  // when the client sends one, and that POST /v1/text/polish {text} reaches upstream /v1/text/polish
  // and its {polish_status, polished_text, polish_error?} body is returned unchanged.
});
```

Fill the third test with the concrete fake-upstream helpers from `app.test.ts` (the file shows how `/v1/voice` and `/v1/voice/text` are proxied and asserted).

- [ ] **Step 2: Run to verify they fail**

Run: `npm run build --workspace packages/contracts && npm test --workspace services/api-ts`
Expected: FAIL (schema rejects `polish_status`; `/v1/text/polish` returns 404; `polish` part not forwarded).

- [ ] **Step 3: Implement**

`packages/contracts/src/fastpath-result.ts`, inside `fastPathResponseSchema` after `polished_text: z.string(),`:

```ts
  polish_status: z.enum(["ok", "skipped", "failed"]).optional(),
  polish_error: z.string().max(200).optional(),
```

`services/api-ts/src/routes/voice.ts`: add `polish: "0"` to the `fields` allowlist object at line 8 so a client's `polish` part is copied to the upstream form; add a proxy for `POST /v1/text/polish` next to the `/v1/voice/text` route that validates `{ text: z.string().min(1).max(8192) }` inbound, forwards to the Python backend, and validates the upstream reply with a new `polishTextResponseSchema` (`{ polish_status: z.enum([...]), polished_text: z.string(), polish_error: z.string().max(200).optional() }`) exported from `packages/contracts/src/fastpath-result.ts`.

- [ ] **Step 4: Run to verify it passes, then the workspace checks**

Run: `npm run build --workspace packages/contracts && npm run check --workspace packages/contracts && npm run check --workspace services/api-ts && npm test --workspace services/api-ts`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/contracts/src/fastpath-result.ts services/api-ts/src/routes/voice.ts services/api-ts/src/test/voice-polish.test.ts
git commit -m "Contracts and gateway: polish_status, polish_error and /v1/text/polish

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 4: Rust contract (serde defaults)

**Files:**
- Modify: `apps/desktop-tauri/src-tauri/src/contracts.rs:92-113` (`pub struct FastPathResponse`; it currently has no `polished_text` at all, and `HistoryItem` at `:287` is a different struct that must not change)

- [ ] **Step 1: Write the failing test** (append to the `#[cfg(test)] mod tests` in `contracts.rs`, or create one)

```rust
#[test]
fn fast_path_response_accepts_optional_polish_fields() {
    const WITHOUT: &str = r#"{"id":"u1","status":"ok","raw_text":"嗯 测试","display_text":"嗯 测试","stt_provider":"qwen-local","stt_model":"Qwen/Qwen3-ASR-0.6B","target_app":"cockpit","target_risk_level":"low","should_autopaste":true,"needs_review":false,"confidence":0.9,"dictionary_applied":[],"latency_ms":120,"session_id":"s1","utterance_id":"u1","output_id":"o1","output_mode":"paste","archive_status":"queued","created_at":"2026-10-20T00:00:00Z"}"#;
    let without: FastPathResponse = serde_json::from_str(WITHOUT).unwrap();
    assert_eq!(without.polished_text, None);
    assert_eq!(without.polish_status, None);
    let with = WITHOUT.replacen("\"created_at\"", "\"polished_text\":\"测试\",\"polish_status\":\"failed\",\"polish_error\":\"Qwen Bailian: HTTP 429\",\"created_at\"", 1);
    let with: FastPathResponse = serde_json::from_str(&with).unwrap();
    assert_eq!(with.polished_text.as_deref(), Some("测试"));
    assert_eq!(with.polish_status.as_deref(), Some("failed"));
    assert_eq!(with.polish_error.as_deref(), Some("Qwen Bailian: HTTP 429"));
}
```

Check the enum wire names (`FastPathStatus`, `TargetRiskLevel`, `OutputMode`) by reading their `#[serde(rename_all = ...)]` attributes in `contracts.rs` and adjust the fixture strings (`"ok"`, `"low"`, `"paste"`) to the real spellings.

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked fast_path_response_accepts_optional_polish_fields`
Expected: FAIL to compile: no field `polished_text` / `polish_status`.

- [ ] **Step 3: Implement**

Below `pub display_text: String,` in `FastPathResponse` add:

```rust
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polished_text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polish_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub polish_error: Option<String>,
```

Keep `String` for the status (not an enum) so an unknown future value reaches the WebView as text instead of failing deserialization.

- [ ] **Step 4: Run to verify it passes, then the full Rust suite and fmt**

Run: `cargo fmt --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --all -- --check && cargo test --manifest-path apps/desktop-tauri/src-tauri/Cargo.toml --locked`
Expected: fmt clean, all tests pass (`skip_serializing_if` keeps existing JSON snapshots unchanged).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop-tauri/src-tauri/src/contracts.rs
git commit -m "Tauri contracts: pass through polished_text, polish_status and polish_error

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 5: Full verification, PR, Codex audit

- [ ] **Step 1: Run the Python suite the way CI does**

Run: `.venv/bin/python -m pytest tests --ignore=tests/test_engine_e2e_claude.py -W error::DeprecationWarning -q --tb=short && .venv/bin/ruff check .`
Expected: pass (three known `mcp`-extra failures are acceptable locally only if the venv lacks the extra; CI installs it).

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin HEAD
gh pr create --title "PR-D: Synchronous polish with explicit polish_status" --body-file - <<'EOF'
Implements spec §3.3 (docs/superpowers/specs/2026-10-20-windows-voice-to-context-design.md): PolishOutcome with a 15 s total deadline and bounded errors; `/v1/voice` and `/v1/voice/text` accept `polish=1` and return `polish_status` (+ `polish_error`); new `POST /v1/text/polish`; additive optional fields in the TS and Rust contracts. No behaviour change for callers that do not send the flag.

Verified: pytest (new tests listed), ruff, contracts check/test, api-ts check/test, cargo fmt/test.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

- [ ] **Step 3: Codex audit** per the master plan's standard procedure; append the record to `docs/verification/windows-voice-flow-2026-10.md`; fix any high-severity finding before reporting done.
