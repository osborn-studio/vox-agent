# Windows voice flow: Codex audit records

Procedure: `docs/superpowers/plans/2026-10-20-windows-voice-to-context.md`,
"Standard Codex audit procedure". One record per pull request; a PR is not
merged while a high-severity finding is open.

## PR-0: Qwen3-ASR-0.6B frozen CPU feasibility spike — claude/youthful-ishizaka-e565fc — 286910d

- Date: 2026-10-20
- Model: gpt-6-astra, medium (Codex CLI 0.160.1; `codex review` takes the
  model as `-c model="gpt-6-astra"`, not `--model`)
- Diff review findings (first pass at 47411ba): 0 high / 0 medium / 0 low
  ("No actionable defects were identified in the standalone probe, workflow,
  manifest, or supporting tests"; Codex could not run pytest because its shell
  resolved a Python without pytest, so the test runs below are ours)
- Adversarial pass (first pass at 47411ba): FAIL, 0 high / 2 medium
- Dispositions:
  - `scripts/spike_qwen_local_probe.py` `load_manifest` accepted an empty
    `files` list, so `--verify-only` exited 0 with `files_ok: true` against a
    missing model directory → fixed in 8baf127 (empty list raises
    `SystemExit`; `test_load_manifest_rejects_an_empty_file_list`)
  - `download_missing` wrote every download to the shared `<name>.part` and
    renamed it, so two probes in one model directory could truncate or consume
    each other's partial file → fixed in 8baf127 (per-process
    `<name>.<pid>-<random>.part`, removed in `finally`;
    `test_download_uses_a_private_temp_file_and_leaves_none_behind`)
- Diff review findings (rerun at 8baf127): 0 high / 1 medium (P2) / 0 low
- Adversarial pass (rerun at 8baf127): PASS, 0 high
- Dispositions (second pass):
  - `load_model` chose CUDA when `torch.cuda.is_available()`, so the owner's
    NVIDIA PC would measure the GPU route and `build_report` would mark the run
    `success: false` → fixed in fc00277 (`device_map="cpu"` always; the
    reported device comes from the loaded model;
    `test_load_model_forces_cpu_even_when_cuda_is_available`)
- Diff review findings (final at fc00277): 0 high / 0 medium / 0 low ("No actionable defects were identified"; Codex ran the 14 focused tests itself this time)
- Adversarial pass (third pass at fc00277): FAIL, 0 high / 1 medium (P2)
- Dispositions (third pass):
  - `download_missing` replaced the destination before checking the bytes, so
    a corrupt or short download from one run could overwrite a file another
    run had already verified in a shared directory → fixed in 286910d (size
    and SHA-256 are checked on the private temp file; a mismatch is reported
    and the destination is left alone; `test_download_verifies_before_publishing`)
- Diff review findings (final at 286910d): 0 high / 0 medium / 0 low
- Adversarial pass (final at 286910d): PASS, 0 high
- Gate: nothing open at any severity at 286910d; this record is the only
  change after that commit. The executor does not merge; the owner merges.
