# Windows ASR qualification uses real adapters and explicit evidence

## Status

Accepted

## Date

2026-07-19

## Context

The Windows Tauri shell exposed a command named `preview_local_transcript` and a
Rust crate named for whisper.cpp, but that code never decoded audio. It generated
elapsed-time copy and a synthetic confidence value. The Python whisper.cpp path
also returned a fabricated preview transcript when the runtime or model failed.
Those behaviors made the Windows shell look complete while providing no ASR
evidence—the exact failure mode an upgraded Vox Agent product must avoid.

Local Python adapters also staged audio with an open `NamedTemporaryFile`, which
is incompatible with Windows file locking, and Sherpa readiness conflated the
Python package with the SenseVoice and VAD assets. Model defaults still pointed
at the VoxLog namespace.

## Decision

- Delete the placeholder Rust whisper.cpp crate. During capture, Tauri reports
  capture duration and the selected route only; it explicitly says the model has
  not run. Provider/model transcript evidence is rendered only from the final
  backend response.
- Make all local fast-path adapters fail closed. Missing runtimes, inference
  errors, and empty results return bounded errors and never fabricated text.
- Close Windows temporary WAV files before an ASR runtime opens them, cap them at
  64 MiB, and remove them after inference.
- Use Sherpa-ONNX/SenseVoice as the recommended installable Windows local path.
  Qualification reports package and model/VAD assets separately and supplies
  explicit install commands. whisper.cpp remains optional; Qwen3 ASR local is
  experimental until its heavier model flow is proven on Windows.
- Store new model downloads under `~/.voxagent/models`. A complete
  `~/.voxlog/models` bundle remains a read-only migration fallback.
- Version the provider matrix as `2026-07-19.2`. Runtime-ready is not the same as
  evidence-qualified: `qualified_count` increases only for a passed explicit
  probe.
- Add `scripts/verify_windows_asr.py`. Static inspection is side-effect free. An
  explicit `--audio` smoke stores the provider, latency, audio byte count,
  transcript character count, and transcript SHA-256, never the content.
- Report only whether HTTP proxy environment variables are present. HTTPX keeps
  its default `trust_env` behavior; proxy URLs or values never cross the API.

Sherpa publishes prebuilt CPU wheels for Windows x64/x86 and Windows CUDA wheels
in its [official installation guide](https://k2-fsa.github.io/sherpa/onnx/python/install.html).
The optional whisper.cpp path follows the
[pywhispercpp installation guidance](https://github.com/absadiki/pywhispercpp),
and Qwen local follows the
[Qwen3-ASR package guidance](https://github.com/QwenLM/Qwen3-ASR).

## Consequences

- The cockpit no longer implies streaming/local transcription that did not
  happen.
- A clean Windows operator has one recommended offline install and one bounded
  evidence command.
- Existing VoxLog model downloads remain usable without making the old namespace
  canonical.
- Real Windows microphone/audio evidence and credentialed cloud ASR smokes remain
  the separate WIN-4D release gate; macOS/Linux tests cannot satisfy it.
- Deferred cloud providers remain visible as deferred instead of being presented
  as parity-complete.
