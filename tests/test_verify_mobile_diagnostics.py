from __future__ import annotations

from scripts.verify_mobile_diagnostics import validate_bundle


def _bundle():
    event = {
        "occurredAt": "2026-07-26T12:00:00.000Z",
        "correlationId": "mobile-request_123",
        "endpointClass": "remote-https",
        "networkState": "reachable",
        "errorCode": "REQUEST_REJECTED",
    }
    return {
        "schema": "voxagent.mobile.diagnostics.v1",
        "generatedAt": "2026-07-26T12:01:00.000Z",
        "app": {
            "version": "4.1.0",
            "buildVersion": "410001",
            "executionEnvironment": "standalone",
        },
        "device": {
            "os": "ios",
            "osVersion": "18.5",
            "deviceClass": "phone",
        },
        "latest": {
            "networkState": "reachable",
            "endpointClass": "remote-https",
            "errorCode": "REQUEST_REJECTED",
            "correlationId": "mobile-request_123",
        },
        "recentRequests": [event],
        "crashReporting": "not-configured-manual-diagnostics-only",
    }


def test_validate_mobile_diagnostic_bundle_accepts_bounded_metadata() -> None:
    assert validate_bundle(_bundle()) == []


def test_validate_mobile_diagnostic_bundle_rejects_private_or_augmented_data() -> None:
    payload = _bundle()
    payload["prompt"] = "private transcript"
    payload["app"]["version"] = "password=private-value"
    payload["recentRequests"][0]["correlationId"] = "unsafe private id"

    assert validate_bundle(payload) == [
        "invalid_app_value",
        "invalid_correlation_id",
        "invalid_top_level",
        "sensitive_content_pattern",
    ]
