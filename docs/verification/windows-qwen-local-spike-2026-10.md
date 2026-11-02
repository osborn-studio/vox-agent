# Windows Qwen3-ASR-0.6B frozen CPU spike (PR-0)

- Date: 2026-10-20
- Runner: GitHub-hosted `windows-latest` = Windows Server 2025 image
  `win25-vs2026` version `20261007.264.1` (Windows NT 10.0.26100), AMD EPYC
  7763, 4 logical processors, 16,379 MB RAM, CPU only (no CUDA, no PATH Python
  during the frozen run)
- Branch: `claude/youthful-ishizaka-e565fc`; workflow
  `.github/workflows/qwen-local-spike.yml`
- Run 1: commit `0944e79`, https://github.com/osborn-studio/vox-agent/actions/runs/39454930700
  (first English utterance, 14.55 s)
- Run 2: commit `f063c82`, https://github.com/osborn-studio/vox-agent/actions/runs/39456708933
  (trimmed English utterance, 10.97 s)
- qwen-asr 0.0.6, transformers 4.57.6, torch 2.14.1+cpu (CPU wheel from
  download.pytorch.org), accelerate 1.12.0, tokenizers 0.22.2, safetensors
  0.8.0, librosa 1.0.0, numba 0.68.0, nagisa 0.2.11 (dyNET38 2.2), soundfile
  0.14.0, numpy 2.5.4, Python 3.12.10, PyInstaller 6.22.3 with contrib hooks
  2026.9

The probe (`scripts/spike_qwen_local_probe.py`) never writes transcript text:
the artifact `qwen-local-spike-report` holds the per-file verification status,
timings, the transcript's SHA-256 and length, peak RSS, the frozen directory
size, `runner.json`, `pip-freeze.txt`, `frozen-size.json` and the expected
text that SAPI was asked to speak.

## Result

| Measure | Run 1 (14.55 s audio) | Run 2 (10.97 s audio) |
|---|---|---|
| Nine pinned files verified offline (`files_ok`) | yes | yes |
| Device reported by torch | cpu | cpu |
| Frozen directory size (`frozen_dir_bytes`) | 1,073,976,650 bytes (1,024 MB) | 1,073,976,650 bytes (1,024 MB) |
| Model load time (`latency_ms.load`) | 2,356 ms | 3,305 ms |
| Cold transcribe latency | 10,781 ms | 13,062 ms |
| Warm transcribe latency | 10,157 ms | 10,664 ms |
| Warm real-time factor (warm ms / audio ms) | 0.70 | 0.97 |
| Peak RSS during load + inference | 5,224.7 MB | 5,480.4 MB |
| Transcript matched expected | yes (sha256 `29b76462…dcfa3`, 215 chars) | yes (sha256 `0a954b64…625fce`, 161 chars) |
| Probe exit code (clean shutdown) | 0 | 0 |
| Whole job wall time | 9 min 48 s (model download 18 s, freeze 5 min 18 s, probe 38 s) | 10 min 49 s (model cache restore 16 s, freeze 5 min 51 s, probe 46 s) |

The runner image only ships the en-US SAPI voices (Microsoft David Desktop,
Microsoft Zira Desktop), so both runs transcribed English synthesised by
Microsoft David at the default rate; the Chinese text in
`scripts/spike_utterance_zh.txt` is used automatically when a `zh-CN` voice is
installed (the owner's PC). Transcript correctness is judged by
`difflib.SequenceMatcher` ratio ≥ 0.8 after stripping spaces, punctuation and
case; both runs matched.

## Decision (spec §3.2 rule: warm ≤ 6 s for a 10-second utterance)

Run 2 is the direct measurement (10.97 s of speech): warm = 10,664 ms, cold =
13,062 ms. Run 1 (14.55 s of speech) gave warm = 10,157 ms. Latency did not
shrink with the shorter audio, so on this CPU the autoregressive decode (161
and 215 characters respectively), not the encoder, sets the floor. Both runs
exceed the 6 s threshold by a wide margin on this 4-vCPU runner.

**sherpa-sensevoice default, Qwen local opt-in** for new Windows installs,
unless the owner's PC measurement in PR-F comes in under the threshold (the
spec's rule names the owner's PC CPU; this runner has 4 logical processors of
a server part, so a desktop CPU may well pass). PR-C keeps the `qwen-local`
route "experimental" and wires it as an opt-in; the matrix entry moves only on
named-device evidence.

## What PR-B and PR-C take from this

- Offline loading confirmed: `Qwen3ASRModel.from_pretrained(<dir>, dtype=torch.float32, device_map="cpu")`
  with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` loads from the nine
  pinned files in 2.4 s; the processor (`AutoProcessor`, `fix_mistral_regex=True`)
  loads from the same directory. `model.transcribe([<wav path>])` works with a
  16 kHz mono 16-bit PCM WAV through `librosa.load` in the frozen build, so
  `QwenLocalASR`'s existing call shape (`runtime/fastpath/stt.py`) carries over.
- PyInstaller `collect_all` list that worked (`scripts/qwen_local_probe.spec`):
  `qwen_asr, transformers, torch, tokenizers, safetensors, accelerate, numpy,
  soundfile, librosa, soxr, numba, llvmlite, nagisa`, plus `collect_submodules("soynlp")`
  and hidden imports `psutil, scipy.signal, scipy.sparse, sklearn.utils`;
  excludes `tkinter, matplotlib, IPython, jupyter, gradio, flask, vllm, pytest`.
  `collect_all("soundfile")` is a no-op (soundfile is a module, not a package);
  the contrib hook ships `libsndfile` on its own. One-directory build, no UPX.
- Frozen size budget for `voxagent-backend.exe`: 1,024 MB as built, of which
  torch 450 MB, llvmlite 115 MB, `av.libs` 64 MB, scipy 51 + 19 MB,
  transformers 50 MB, nagisa 45 MB, numpy 20 + 20 MB, PIL 13 MB, pandas 12 MB,
  sklearn 12 MB, numba 12 MB, tokenizers 7 MB (`frozen-size.json`). The spec's
  400–600 MB estimate was low: `librosa` alone drags in numba + llvmlite +
  scipy + scikit-learn (about 200 MB) and `av`/`pandas`/`PIL` arrive through
  transformers' optional imports. PR-B can exclude `av`, `pandas`, `PIL`,
  `sklearn` and `torch/include` to approach 700 MB, but must re-run this
  workflow after each exclusion.
- nagisa (Japanese tokenizer with the dyNET38 extension) is imported at module
  level by `qwen_asr.inference.qwen3_forced_aligner` and must ship; soynlp is
  imported lazily in the same module. Both emit `SyntaxWarning`s at freeze time
  only.
- Memory: peak RSS 5.2 GB in float32 on CPU (1.9 GB of weights plus
  activations and the load-time copy). A 16 GB machine is the realistic
  minimum while the backend holds the model; PR-C's inference lock also bounds
  this to one model copy.
- Problems seen: none at runtime. Freeze-time warnings only (missing optional
  `einops`, `tensorboard`, `pytest` submodules; soundfile collect no-op).
  `actions/cache` accepted the 1.9 GB model entry (saved after run 1, restored
  in run 2), so a re-run costs about 11 minutes, dominated by the pip install and the freeze.
- pip on the runner backtracks for about two minutes resolving `qwen-asr`'s
  unpinned `gradio`/`flask`/`qwen-omni-utils` requirements; PR-B should pin
  the install set from `pip-freeze.txt` in the artifact.

## Not measured

- An NVIDIA or desktop CPU: the runner is a 4-vCPU server slice. The spec's
  threshold is defined on the owner's PC; PR-F repeats the probe there.
- Chinese speech: no `zh-CN` SAPI voice on the hosted image. The workflow
  switches to the Chinese utterance automatically on a machine that has one.
- One-file PyInstaller mode (deliberately not used; the spec asks for the real
  on-disk size and undistorted cold latency).
- A user-installed CUDA torch picked up by the frozen build (out of scope per
  spec §3.2 until a frozen build proves it).
