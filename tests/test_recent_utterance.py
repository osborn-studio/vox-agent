"""Tests for the in-memory recent utterance recovery store."""

import time

from runtime.fastpath.recent import RecentUtteranceStore
from runtime.models.events import FastPathResult, VoiceEvent


def _make_result() -> FastPathResult:
    return FastPathResult(
        id="evt-1",
        display_text="hello",
        raw_text="hello",
        stt_provider="whispercpp-local",
        latency_ms=100,
    )


def _make_event() -> VoiceEvent:
    return VoiceEvent(
        id="evt-1",
        utterance_id="utt-1",
        raw_text="hello",
        display_text="hello",
    )


def test_recent_store_returns_latest_item():
    store = RecentUtteranceStore(ttl_seconds=30)
    store.put(_make_result(), _make_event(), b"audio")

    item = store.latest()
    assert item is not None
    assert item.event.utterance_id == "utt-1"


def test_recent_store_expires_items():
    store = RecentUtteranceStore(ttl_seconds=0)
    store.put(_make_result(), _make_event(), b"audio")
    time.sleep(0.01)

    assert store.latest() is None


def test_recent_store_can_find_by_output_id():
    store = RecentUtteranceStore(ttl_seconds=30)
    event = _make_event()
    event.output_id = "out-1"
    store.put(_make_result(), event, b"audio")

    item = store.get_by_output_id("out-1")
    assert item is not None
    assert item.event.output_id == "out-1"


def test_recent_store_can_dismiss_item():
    store = RecentUtteranceStore(ttl_seconds=30)
    store.put(_make_result(), _make_event(), b"audio")

    assert store.dismiss("utt-1") is True
    assert store.latest() is None
