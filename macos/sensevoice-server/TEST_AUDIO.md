# Synthetic transport fixture

`test_audio.wav` and `test_audio.npy` are generated from integer arithmetic by
`scripts/generate_synthetic_test_audio.py`. They contain a six-second 200 Hz
triangle wave, at 16 kHz mono PCM16. No microphone, person, recording, speech
model, third-party audio sample, or external service is involved. The generator
and resulting fixtures are distributed under this repository's MIT license.

Regenerate from the repository root:

```sh
python3 scripts/generate_synthetic_test_audio.py --output macos/sensevoice-server
```

Use these fixtures for PCM/container and transport checks. They contain no words
and cannot verify transcription quality. The NPY array is little-endian int16,
shape `(96000,)`, and exactly equals the WAV PCM samples.

This replacement makes the current tree's provenance explicit. The former
samples introduced by commit `60f83821117676b70630658afcfc3cfb5c2f7108` remain in
Git history. Their origin and redistribution rights are unresolved; this change
does not authorize publishing that history.
