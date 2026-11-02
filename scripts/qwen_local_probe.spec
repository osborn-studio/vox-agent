# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PR-0 Qwen3-ASR-0.6B CPU probe (Windows).

One-directory build on purpose: it reports the real on-disk size and avoids
one-file extraction time distorting the cold latency. The PACKAGES list is the
import-time dependency set of ``qwen_asr.Qwen3ASRModel`` (transformers backend)
as installed by ``pip install qwen-asr torch`` on 2026-10-20:

  qwen-asr 0.0.6 Requires: transformers==4.57.6, nagisa==0.2.11, soynlp==0.0.493,
  accelerate==1.12.0, qwen-omni-utils, librosa, soundfile, sox, gradio, flask, pytz

gradio, flask, sox, pytz and qwen-omni-utils are only used by the package's demo
and server entry points, which the probe never imports, so they are not
collected. nagisa (Japanese tokenizer, DyNet38 extension + bundled model data)
is imported at module level by ``qwen_asr.inference.qwen3_forced_aligner`` and
must ship; soynlp is imported lazily in the same module.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

PACKAGES = [
    "qwen_asr",
    "transformers",
    "torch",
    "tokenizers",
    "safetensors",
    "accelerate",
    "numpy",
    "soundfile",
    "librosa",
    "soxr",
    "numba",
    "llvmlite",
    "nagisa",
]

datas, binaries, hiddenimports = [], [], []
for name in PACKAGES:
    d, b, h = collect_all(name)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("soynlp")
hiddenimports += ["psutil", "scipy.signal", "scipy.sparse", "sklearn.utils"]

a = Analysis(
    ["spike_qwen_local_probe.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "gradio", "flask", "vllm", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="qwen-local-probe",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="qwen-local-probe")
