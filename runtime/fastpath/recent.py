"""In-memory recent utterance store for Phase 0 recovery actions."""

from __future__ import annotations

import time
from dataclasses import dataclass

from runtime.models.events import FastPathResult, VoiceEvent


@dataclass
class RecentUtterance:
    result: FastPathResult
    event: VoiceEvent
    audio: bytes
    expires_at: float


class RecentUtteranceStore:
    def __init__(self, ttl_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, RecentUtterance] = {}
        self._latest_id: str | None = None

    def put(self, result: FastPathResult, event: VoiceEvent, audio: bytes) -> None:
        self._purge_expired()
        self._items[event.utterance_id] = RecentUtterance(
            result=result,
            event=event,
            audio=audio,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._latest_id = event.utterance_id

    def get(self, utterance_id: str) -> RecentUtterance | None:
        self._purge_expired()
        item = self._items.get(utterance_id)
        if not item:
            return None
        if item.expires_at <= time.time():
            self._items.pop(utterance_id, None)
            if self._latest_id == utterance_id:
                self._latest_id = None
            return None
        return item

    def latest(self) -> RecentUtterance | None:
        self._purge_expired()
        if not self._latest_id:
            return None
        return self.get(self._latest_id)

    def get_by_output_id(self, output_id: str) -> RecentUtterance | None:
        self._purge_expired()
        for item in self._items.values():
            if item.event.output_id == output_id:
                return item
        return None

    def dismiss(self, utterance_id: str) -> bool:
        self._purge_expired()
        if utterance_id not in self._items:
            return False
        self._items.pop(utterance_id, None)
        if self._latest_id == utterance_id:
            self._latest_id = None
        return True

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
            if self._latest_id == key:
                self._latest_id = None
