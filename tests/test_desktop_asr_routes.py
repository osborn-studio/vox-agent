from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.desktop import server


@pytest.mark.asyncio
async def test_asr_switch_accepts_only_canonical_shared_routes(monkeypatch) -> None:
    monkeypatch.setattr(server, "_stt_override", None)

    with pytest.raises(HTTPException) as failure:
        await server.switch_asr("legacy-magic-preview", None)

    assert failure.value.status_code == 400
    assert server._stt_override is None

    response = await server.switch_asr("  SHERPA-SENSEVOICE  ", None)

    assert response["model"] == "sherpa-sensevoice"
    assert server._stt_override == "sherpa-sensevoice"
