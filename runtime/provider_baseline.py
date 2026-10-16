"""macOS speech-provider baseline and its Windows answer (T28a).

Each entry of the macOS ASR registry (macos/VoxLog/ASR/ASRProviderRegistry.swift)
maps to a provider in the Windows qualification matrix
(runtime/provider_qualification.py) or to an explicit GAP with a follow-up
task. A mapped provider is a contract, not a qualification: on Windows it
stays unqualified until a probe with real audio passes (ADR 2026-07-19
provider qualification; T28b for the local path on a named device).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GAP_REASONS = {
    "platform_unavailable": "the provider is part of macOS",
    "deferred_cloud": "cloud adapter not ported to the Windows runtime yet",
    "not_implemented_on_macos": "configuration only on macOS; there is no baseline behaviour to port",
}


@dataclass(frozen=True)
class BaselineEntry:
    label: str
    macos_implemented: bool
    windows_id: str | None = None
    gap: str | None = None
    follow_up: str | None = None
    note: str = ""


BASELINE: dict[str, BaselineEntry] = {
    "sherpa": BaselineEntry(
        "Qwen3-ASR-0.6B Local", True, windows_id="qwen-local",
        note="Experimental on Windows (ADR 2026-07-19); the recommended Windows local path is "
             "sherpa-sensevoice, qualified only by T28b on a named device.",
    ),
    "openai": BaselineEntry("OpenAI", True, windows_id="openai-whisper"),
    "bailian": BaselineEntry("Bailian CN", True, windows_id="qwen-cn",
                             note="macOS streams; Windows runs batch recognition."),
    "bailianUS": BaselineEntry("Bailian US", True, windows_id="qwen-us"),
    "apple": BaselineEntry("Apple Speech", True, gap="platform_unavailable", follow_up="none (macOS-only)"),
    "volcano": BaselineEntry("Volcano (Doubao)", True, gap="deferred_cloud", follow_up="T33"),
    "deepgram": BaselineEntry("Deepgram", True, gap="deferred_cloud", follow_up="T33"),
    "assemblyai": BaselineEntry("AssemblyAI", True, gap="deferred_cloud", follow_up="T33"),
    "elevenlabs": BaselineEntry("ElevenLabs", True, gap="deferred_cloud", follow_up="T33"),
    "soniox": BaselineEntry("Soniox", True, gap="deferred_cloud", follow_up="T33"),
    "baidu": BaselineEntry("Baidu", True, gap="deferred_cloud", follow_up="T33"),
    "azure": BaselineEntry("Azure Speech", False, gap="not_implemented_on_macos", follow_up="none"),
    "google": BaselineEntry("Google Cloud STT", False, gap="not_implemented_on_macos", follow_up="none"),
    "aws": BaselineEntry("AWS Transcribe", False, gap="not_implemented_on_macos", follow_up="none"),
    "aliyun": BaselineEntry("Alibaba Cloud", False, gap="not_implemented_on_macos", follow_up="none"),
    "tencent": BaselineEntry("Tencent", False, gap="not_implemented_on_macos", follow_up="none"),
    "iflytek": BaselineEntry("iFlytek", False, gap="not_implemented_on_macos", follow_up="none"),
    "custom": BaselineEntry("Custom", False, gap="not_implemented_on_macos", follow_up="none"),
}

def macos_registry(swift_source: str) -> tuple[set[str], set[str]]:
    """(implemented, config_only) provider ids from ASRProviderRegistry.swift.

    Strict: every key of the `all` registry literal and every later
    `dict[.x] = ...` assignment is enumerated first; each must be a literal
    `ProviderEntry(configType: ..., createClient: ...)` that can be
    classified, and no provider may be defined twice. Anything else raises,
    so a provider added in another form cannot slip past the baseline test.
    """

    start = swift_source.find("static let all:")
    end = swift_source.find("return dict", start)
    if start < 0 or end < 0:
        raise ValueError("ASR registry `static let all` block not found")
    block = swift_source[start:end]
    keys = [(m.group(1), m.end()) for m in re.finditer(r"^\s*\.(\w+)\s*:", block, re.M)]
    keys += [(m.group(1), m.end()) for m in re.finditer(r"dict\[\.(\w+)\]\s*=", block)]
    implemented: set[str] = set()
    config_only: set[str] = set()
    for name, offset in keys:
        if name in implemented or name in config_only:
            raise ValueError(f"macOS provider {name!r} is defined twice")
        window = block[offset: offset + 400]
        if re.match(r"\s*ProviderEntry\(\s*configType:[^,]+,\s*createClient:\s*nil\s*\)", window):
            config_only.add(name)
        elif re.match(r"\s*ProviderEntry\(\s*configType:[^,]+,\s*createClient:\s*\{", window):
            implemented.add(name)
        else:
            raise ValueError(f"cannot classify macOS provider entry {name!r}")
    if not implemented:
        raise ValueError("no implemented macOS providers found")
    return implemented, config_only


def windows_answer(macos_id: str) -> str:
    entry = BASELINE[macos_id]
    if entry.windows_id:
        return f"maps to {entry.windows_id} (unqualified until probed)"
    detail = {"platform_unavailable": "macOS-only Apple Speech"}.get(entry.gap, GAP_REASONS[entry.gap])
    return f"GAP: {entry.gap} ({detail})"
