"""Canonical Vox Agent local-ASR paths with recoverable VoxLog migration fallback."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from runtime.data_paths import legacy_voxlog_data_dirs, resolve_vox_agent_data_dir

SHERPA_BUNDLE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
VAD_FILE = "silero_vad.onnx"


def vox_agent_models_root() -> Path:
    return resolve_vox_agent_data_dir() / "models"


def legacy_voxlog_models_root() -> Path:
    return legacy_voxlog_data_dirs()[1] / "models"


def _has_sherpa_assets(model_dir: Path) -> bool:
    return (model_dir / "model.int8.onnx").is_file() and (model_dir / "tokens.txt").is_file()


# Verified model cache ids (engine/model_manifests, T31).
SENSEVOICE_CACHE_ID = "sensevoice-int8"
SILERO_VAD_CACHE_ID = "silero-vad"

_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
# Windows device names are reserved with any extension (CON, con.txt, ...).
_DEVICE = re.compile(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?")


def is_plain_model_name(value: object) -> bool:
    """One directory name on every platform: no separators, drive letters,
    ADS colons, hidden names, trailing dots, device names or the pointer."""
    return (
        isinstance(value, str)
        and _PLAIN.fullmatch(value) is not None
        and not value.endswith(".")
        and _DEVICE.fullmatch(value) is None
        and value.casefold() != "active"
    )


def is_link(path: Path) -> bool:
    """A symlink, or on Windows any reparse point (junction, mount point)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _plain_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not is_link(path)
    except OSError:
        return False


def read_model_pointer(model_dir: Path) -> str | None:
    """The revision named by `<model_dir>/ACTIVE`, or None. Shared by the
    resolver and the installer so both read exactly the same value."""
    pointer = model_dir / "ACTIVE"
    try:
        if is_link(model_dir) or is_link(pointer):
            return None
        with open(pointer, "rb") as handle:
            raw = handle.read(131)
    except OSError:
        return None
    # One line ending: LF, or CRLF from a pointer written in text mode on Windows.
    for ending in (b"\r\n", b"\n"):
        if raw.endswith(ending):
            raw = raw[: -len(ending)]
            break
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return value if is_plain_model_name(value) else None


def _verified_cache_dir(model_id: str) -> Path | None:
    """The active revision of the verified model cache, if one is installed.
    Root, model folder, pointer and revision must all be plain (no links)."""
    root = vox_agent_models_root()
    model_dir = root / model_id
    if not (_plain_dir(root) and _plain_dir(model_dir)):
        return None
    revision = read_model_pointer(model_dir)
    if revision is None:
        return None
    tree = model_dir / revision
    if not _plain_dir(tree) or tree.parent != model_dir:
        return None
    return tree


def resolve_sherpa_model_dir() -> Path:
    override = os.environ.get("VOXAGENT_SHERPA_MODEL_DIR")
    if override:
        return Path(override).expanduser()
    verified = _verified_cache_dir(SENSEVOICE_CACHE_ID)
    if verified is not None:
        return verified
    primary = vox_agent_models_root() / SHERPA_BUNDLE
    legacy = legacy_voxlog_models_root() / SHERPA_BUNDLE
    if not _has_sherpa_assets(primary) and _has_sherpa_assets(legacy):
        return legacy
    return primary


def resolve_sherpa_vad_model() -> Path:
    override = os.environ.get("VOXAGENT_SHERPA_VAD_MODEL")
    if override:
        return Path(override).expanduser()
    verified = _verified_cache_dir(SILERO_VAD_CACHE_ID)
    if verified is not None and (verified / VAD_FILE).is_file():
        return verified / VAD_FILE
    primary = vox_agent_models_root() / VAD_FILE
    legacy = legacy_voxlog_models_root() / VAD_FILE
    if not primary.is_file() and legacy.is_file():
        return legacy
    return primary


def sherpa_assets_available() -> bool:
    return _has_sherpa_assets(resolve_sherpa_model_dir()) and resolve_sherpa_vad_model().is_file()
