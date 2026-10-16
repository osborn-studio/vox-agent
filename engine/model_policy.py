"""Runtime model-routing policy (enterprise admin, master brief §9.2 / §11.6).

Holds optional runtime overrides for the default answerer and ASR adapter,
layered over the VOXAGENT_ANSWERER / VOXAGENT_ASR_ADAPTER env defaults. A
per-request `answerer`/`asrAdapter` in an endpoint body still wins over the
policy. `effective()` reports what is actually active plus whether the LLM is
configured — it never exposes the API key, only a boolean and the model name.

The application context owns the active instance and persists admin overrides
through ``PolicyStore`` so restarts preserve intentional routing changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .agent_answerer import _ANSWERER_FACTORIES
from .asr import _ADAPTER_FACTORIES

_LLM_ENV = ("VOXAGENT_LLM_BASE_URL", "VOXAGENT_LLM_API_KEY", "VOXAGENT_LLM_MODEL")


def available_answerers() -> list[str]:
    return sorted(_ANSWERER_FACTORIES)


def available_asr_adapters() -> list[str]:
    return sorted(_ADAPTER_FACTORIES)


@dataclass
class ModelPolicy:
    # Runtime overrides; None means "fall back to the env default".
    answerer: str | None = None
    asr_adapter: str | None = None

    def effective(self) -> dict:
        return {
            "answerer": self.answerer or os.environ.get("VOXAGENT_ANSWERER", "mock"),
            "asrAdapter": self.asr_adapter or os.environ.get("VOXAGENT_ASR_ADAPTER", "mock"),
            "answererOverride": self.answerer,
            "asrAdapterOverride": self.asr_adapter,
            "availableAnswerers": available_answerers(),
            "availableAsrAdapters": available_asr_adapters(),
            # Whether the `llm` answerer is usable — a boolean + the model name
            # only; the API key is never surfaced.
            "llmConfigured": all(os.environ.get(v) for v in _LLM_ENV),
            "llmModel": os.environ.get("VOXAGENT_LLM_MODEL"),
        }
