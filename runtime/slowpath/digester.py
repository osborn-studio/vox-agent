"""Digest builders for slow path memory compilation."""

from __future__ import annotations

from collections import Counter

from runtime.models.events import VoiceEvent


class SessionDigester:
    def build(self, event: VoiceEvent) -> dict:
        base_text = (event.polished_text or event.display_text or event.raw_text).strip()
        tokens = [token.strip(".,:;!?()[]{}").lower() for token in base_text.split() if token.strip()]
        token_counts = Counter(token for token in tokens if len(token) > 3)
        entities = [token for token, _count in token_counts.most_common(5)]

        summary = base_text[:220] if base_text else ""
        intent = self._guess_intent(event, base_text)
        suggested_tags = self._suggest_tags(event, entities)

        return {
            "session_id": event.session_id,
            "digest_type": "session_digest",
            "source_event_id": event.id,
            "summary": summary,
            "intent": intent,
            "suggested_tags": suggested_tags,
            "mentioned_entities": entities,
        }

    def _guess_intent(self, event: VoiceEvent, text: str) -> str:
        lowered = text.lower()
        if event.target_app.lower() in {"cursor", "claude code", "terminal"}:
            return "coding"
        if any(word in lowered for word in ("plan", "roadmap", "next step", "design")):
            return "planning"
        if any(word in lowered for word in ("fix", "bug", "error", "issue")):
            return "debugging"
        if any(word in lowered for word in ("review", "feedback", "comment")):
            return "review"
        return "general"

    def _suggest_tags(self, event: VoiceEvent, entities: list[str]) -> list[str]:
        tags = []
        if event.target_app:
            tags.append(f"app:{event.target_app.lower()}")
        if event.agent:
            tags.append(f"agent:{event.agent.lower()}")
        if event.session_id:
            tags.append(f"session:{event.session_id}")
        tags.extend(f"entity:{entity}" for entity in entities[:3])
        return tags


class DailyDigester:
    def build(self, events: list[VoiceEvent], *, date_key: str) -> dict:
        if not events:
            return {
                "digest_date": date_key,
                "digest_type": "daily_digest",
                "source_event_id": "",
                "summary": "",
                "intent": "general",
                "suggested_tags": [],
                "mentioned_entities": [],
            }

        normalized = [
            (event.polished_text or event.display_text or event.raw_text).strip()
            for event in events
        ]
        combined = " ".join(text for text in normalized if text).strip()
        tokens = [token.strip(".,:;!?()[]{}").lower() for token in combined.split() if token.strip()]
        token_counts = Counter(token for token in tokens if len(token) > 3)
        entities = [token for token, _count in token_counts.most_common(8)]

        app_counts = Counter(
            event.target_app.strip().lower()
            for event in events
            if event.target_app.strip()
        )
        top_apps = [app for app, _count in app_counts.most_common(3)]

        summary_source = " / ".join(text for text in normalized[-3:] if text)
        summary = summary_source[:280] if summary_source else ""
        intent = self._guess_daily_intent(combined, top_apps)

        tags = [f"day:{date_key}"]
        tags.extend(f"app:{app}" for app in top_apps)
        tags.extend(f"entity:{entity}" for entity in entities[:4])

        return {
            "digest_date": date_key,
            "digest_type": "daily_digest",
            "source_event_id": events[-1].id,
            "summary": summary,
            "intent": intent,
            "suggested_tags": tags,
            "mentioned_entities": entities,
        }

    def _guess_daily_intent(self, combined_text: str, apps: list[str]) -> str:
        lowered = combined_text.lower()
        if any(app in {"cursor", "claude code", "terminal"} for app in apps):
            return "coding"
        if any(word in lowered for word in ("plan", "roadmap", "milestone", "design")):
            return "planning"
        if any(word in lowered for word in ("review", "feedback", "comment")):
            return "review"
        if any(word in lowered for word in ("fix", "issue", "bug", "error")):
            return "debugging"
        return "general"


class ProjectDigester:
    def build(self, events: list[VoiceEvent], *, project_key: str) -> dict:
        if not events:
            return {
                "project_key": project_key,
                "digest_type": "project_digest",
                "source_event_id": "",
                "summary": "",
                "intent": "general",
                "suggested_tags": [],
                "mentioned_entities": [],
            }

        normalized = [
            (event.polished_text or event.display_text or event.raw_text).strip()
            for event in events
            if (event.polished_text or event.display_text or event.raw_text).strip()
        ]
        combined = " ".join(normalized).strip()
        tokens = [token.strip(".,:;!?()[]{}").lower() for token in combined.split() if token.strip()]
        token_counts = Counter(token for token in tokens if len(token) > 3)
        entities = [token for token, _count in token_counts.most_common(10)]
        recent_sessions = [event.session_id for event in events if event.session_id][-3:]
        summary_source = " / ".join(normalized[-4:])
        summary = summary_source[:320] if summary_source else ""

        tags = [f"project:{project_key}"]
        tags.extend(f"session:{session_id}" for session_id in recent_sessions)
        tags.extend(f"entity:{entity}" for entity in entities[:5])

        lowered = combined.lower()
        if any(word in lowered for word in ("plan", "roadmap", "milestone", "design")):
            intent = "planning"
        elif any(word in lowered for word in ("fix", "issue", "bug", "error")):
            intent = "debugging"
        elif any(word in lowered for word in ("review", "feedback", "comment")):
            intent = "review"
        else:
            intent = "general"

        return {
            "project_key": project_key,
            "digest_type": "project_digest",
            "source_event_id": events[-1].id,
            "summary": summary,
            "intent": intent,
            "suggested_tags": tags,
            "mentioned_entities": entities,
        }
