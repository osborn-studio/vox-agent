"""Verified model cache: manifests, staged download, atomic activation (T31).

A manifest ships with the app (engine/model_manifests/); a download server is
never trusted for it. It binds provider, architecture, revision, license and
every file's path, size and SHA-256. Its digest uses the same canonical form as
macos/VoxLog/Services/ModelManifest.swift, so both platforms agree on it.

Layout under the cache root (outside the app, kept across upgrades)::

    <root>/<id>/<revision>/...      verified trees (one installer at a time)
    <root>/<id>/ACTIVE              the active revision, replaced atomically
    <root>/<id>/.staging-<random>/  work in progress, never read by ASR

Install downloads only missing or damaged files, checks size and SHA-256 while
writing into staging, and activates with one rename plus one atomic pointer
replace. Archives are extracted with no links, no escapes and exactly the
manifest's files; each member is capped at its manifest size (compression-bomb
guard). An existing revision is reused only after its bytes verify, never
because a directory has the expected name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from runtime.local_asr_paths import is_link, is_plain_model_name, read_model_pointer

MAX_FILES = 4096
MAX_FILE_BYTES = 8 * 1024**3
MAX_TOTAL_BYTES = 16 * 1024**3
MAX_ARCHIVE_BYTES = 8 * 1024**3
_HASH = re.compile(r"[0-9a-f]{64}")
_PATH_CHARS = re.compile(r"[A-Za-z0-9._/-]{1,1024}")
_LABEL = re.compile(r"[A-Za-z0-9._+-]{1,128}")
_ALLOWED_HOSTS = ("https://github.com/", "https://huggingface.co/", "https://modelscope.cn/")
# Hosts a release download may redirect to (GitHub release assets, HF LFS).
_REDIRECT_HOSTS = {
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
    "huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.huggingface.co", "cas-bridge.xethub.hf.co",
    "cdn-lfs.hf.co", "us.aws.cdn.hf.co", "eu.aws.cdn.hf.co",
    "modelscope.cn",
}

Fetcher = Callable[[str, Path, int], None]
"""fetch(url, destination, max_bytes): write at most max_bytes + 1 bytes."""


class ModelCacheError(RuntimeError):
    pass


# Windows device names are reserved with any extension (CON, con.txt, ...).
_DEVICE = re.compile(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?")


def _safe_part(part: str) -> bool:
    return bool(part) and part not in (".", "..") and not part.endswith(".") and len(part) <= 255 \
        and not _DEVICE.fullmatch(part)


def _safe_path(value: str) -> bool:
    if not _PATH_CHARS.fullmatch(value):
        return False
    return all(_safe_part(part) for part in value.split("/"))


def _plain_name(value: str) -> bool:
    """A model id or revision is one directory name, never hidden or the pointer.
    Shared with the ASR resolver so both read ACTIVE the same way."""
    return is_plain_model_name(value)


def canonical_digest(files: list[dict]) -> str:
    ordered = sorted(files, key=lambda f: f["path"].encode("utf-8"))
    canonical = "[" + ",".join(
        f'{{"path":"{f["path"]}","sha256":"{f["sha256"]}","size":{int(f["size"])}}}' for f in ordered
    ) + "]"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelManifest:
    id: str
    provider: str
    architecture: str
    revision: str
    license: str
    source: dict
    files: tuple[FileEntry, ...]
    digest: str
    total_bytes: int

    @staticmethod
    def parse(text: str) -> ModelManifest:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ModelCacheError("manifest is not JSON") from exc
        expected = {"schema", "id", "provider", "architecture", "revision", "license", "source", "files", "digest"}
        if not isinstance(data, dict) or set(data) != expected or data["schema"] != 1:
            raise ModelCacheError("manifest fields are not the schema-1 set")
        for key in ("id", "provider", "architecture", "revision", "license"):
            if not isinstance(data[key], str) or not _LABEL.fullmatch(data[key]):
                raise ModelCacheError(f"manifest {key} is invalid")
        for key in ("id", "revision"):
            if not _plain_name(data[key]):
                raise ModelCacheError(f"manifest {key} is not a plain directory name")
        files = data["files"]
        if not isinstance(files, list) or not 0 < len(files) <= MAX_FILES:
            raise ModelCacheError("manifest file list is empty or too long")
        entries, seen, prefixes, total = [], set(), {}, 0
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                raise ModelCacheError("manifest entry fields are invalid")
            path, size, digest = item["path"], item["size"], item["sha256"]
            if not isinstance(path, str) or not _safe_path(path):
                raise ModelCacheError(f"unsafe manifest path: {path!r}")
            if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE_BYTES:
                raise ModelCacheError("manifest size is invalid")
            if not isinstance(digest, str) or not _HASH.fullmatch(digest):
                raise ModelCacheError("manifest sha256 is invalid")
            if path.lower() in seen:
                raise ModelCacheError(f"manifest path collision: {path}")
            seen.add(path.lower())
            prefix = ""
            for part in path.split("/"):
                prefix = f"{prefix}/{part}" if prefix else part
                existing = prefixes.setdefault(prefix.lower(), prefix)
                if existing != prefix:
                    raise ModelCacheError(f"manifest case collision: {path}")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise ModelCacheError("manifest total size is too large")
            entries.append(FileEntry(path, size, digest))
        for path in seen:
            parts = path.split("/")
            if any("/".join(parts[:n]) in seen for n in range(1, len(parts))):
                raise ModelCacheError(f"manifest file is also a directory: {path}")
        if canonical_digest(files) != data["digest"]:
            raise ModelCacheError("manifest digest does not match its files")
        source = _check_source(data["source"])
        return ModelManifest(
            id=data["id"], provider=data["provider"], architecture=data["architecture"],
            revision=data["revision"], license=data["license"], source=source,
            files=tuple(sorted(entries, key=lambda e: e.path.encode())), digest=data["digest"],
            total_bytes=total,
        )


def _allowed_url(url: object) -> bool:
    return isinstance(url, str) and url.startswith(_ALLOWED_HOSTS) and "@" not in url.split("/", 3)[2] \
        and "?" not in url and "#" not in url


def redirect_allowed(url: str) -> bool:
    """A download may only land on HTTPS, on a known host, without userinfo."""
    if not url.startswith("https://"):
        return False
    authority = url[len("https://"):].split("/", 1)[0]
    return authority in _REDIRECT_HOSTS


def http_fetch(url: str, dest: Path, max_bytes: int, *, opener=None, timeout: float = 60.0) -> None:
    """Stream `url` to `dest`, writing at most max_bytes + 1 bytes so an
    oversize body is detectable. Redirects must stay on allowed hosts."""
    import urllib.request

    if not _allowed_url(url):
        raise ModelCacheError("download url is not an allowed HTTPS origin")

    class _Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not redirect_allowed(newurl):
                raise ModelCacheError("download redirected to a host that is not allowed")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    open_url = opener or urllib.request.build_opener(_Redirects, urllib.request.ProxyHandler()).open
    with open_url(url, timeout=timeout) as response, open(dest, "wb") as out:
        if not redirect_allowed(response.geturl()):
            raise ModelCacheError("download ended on a host that is not allowed")
        written = 0
        while written <= max_bytes:
            chunk = response.read(min(1 << 20, max_bytes + 1 - written))
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)


def _check_source(source: object) -> dict:
    if not isinstance(source, dict):
        raise ModelCacheError("manifest source is invalid")
    if source.get("kind") == "files" and set(source) == {"kind", "baseUrl"}:
        if not _allowed_url(source["baseUrl"]) or not source["baseUrl"].endswith("/"):
            raise ModelCacheError("manifest baseUrl is not an allowed HTTPS origin")
        return dict(source)
    if source.get("kind") == "archive" and set(source) == {"kind", "url", "size", "sha256", "stripPrefix"}:
        if not _allowed_url(source["url"]):
            raise ModelCacheError("manifest archive url is not an allowed HTTPS origin")
        size = source["size"]
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ARCHIVE_BYTES:
            raise ModelCacheError("manifest archive size is invalid")
        if not isinstance(source["sha256"], str) or not _HASH.fullmatch(source["sha256"]):
            raise ModelCacheError("manifest archive sha256 is invalid")
        prefix = source["stripPrefix"]
        if not isinstance(prefix, str) or (prefix and not (prefix.endswith("/") and _safe_path(prefix[:-1]))):
            raise ModelCacheError("manifest stripPrefix is invalid")
        return dict(source)
    raise ModelCacheError("manifest source kind is unknown")


def _file_digest(path: Path, limit: int) -> tuple[int, str]:
    digest, total = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            total += len(chunk)
            if total > limit:
                return total, ""
            digest.update(chunk)
    return total, digest.hexdigest()


def _matches(path: Path, entry: FileEntry) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != entry.size:
        return False
    return _file_digest(path, entry.size) == (entry.size, entry.sha256)


def _check_root(root: Path) -> None:
    if is_link(root):
        raise ModelCacheError("model cache root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if is_link(root) or not root.is_dir():
        raise ModelCacheError("model cache root is not a directory")


def active_revision(root: Path, model_id: str) -> str | None:
    return read_model_pointer(Path(root) / model_id)


@dataclass
class VerifyResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def _verify_tree(tree: Path, manifest: ModelManifest) -> VerifyResult:
    problems = []
    expected = {e.path: e for e in manifest.files}
    if is_link(tree) or not tree.is_dir():
        return VerifyResult(False, ["revision directory is missing"])
    for current, dirs, names in os.walk(tree, followlinks=False):
        for name in dirs + names:
            full = Path(current) / name
            relative = full.relative_to(tree).as_posix()
            info = os.lstat(full)
            if is_link(full):
                problems.append(f"{relative}: not a regular file" if relative in expected else f"{relative}: is a link")
                continue
            if stat.S_ISDIR(info.st_mode):
                continue
            if relative not in expected:
                problems.append(f"{relative}: not in manifest")
            elif not stat.S_ISREG(info.st_mode):
                problems.append(f"{relative}: not a regular file")
    for entry in manifest.files:
        path = tree / entry.path
        if f"{entry.path}: not a regular file" in problems:
            continue
        if not path.exists() and not path.is_symlink():
            problems.append(f"{entry.path}: missing")
        elif not _matches(path, entry):
            problems.append(f"{entry.path}: digest mismatch")
    return VerifyResult(not problems, sorted(problems))


def verify_active(root: Path, manifest: ModelManifest) -> VerifyResult:
    root = Path(root)
    model_dir = root / manifest.id
    if is_link(root) or is_link(model_dir) or is_link(model_dir / "ACTIVE"):
        return VerifyResult(False, ["model cache path is a symlink"])
    revision = active_revision(root, manifest.id)
    if revision != manifest.revision:
        return VerifyResult(False, [f"active revision is {revision!r}, expected {manifest.revision!r}"])
    return _verify_tree(root / manifest.id / revision, manifest)


@dataclass
class InstallResult:
    revision: str
    downloaded: int
    reused: bool
    #: Files copied from a local verified copy instead of downloaded (T32).
    reused_files: int = 0


def _sync_dir(path: Path) -> None:
    """Make renames inside `path` durable (POSIX; NTFS journals metadata)."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_tree(tree: Path) -> None:
    """Flush every staged file and directory before activation, so a power
    loss cannot leave ACTIVE durable while the model bytes are not."""
    for current, dirs, names in os.walk(tree, topdown=False, followlinks=False):
        for name in names:
            with open(Path(current) / name, "rb+") as handle:
                os.fsync(handle.fileno())
        _sync_dir(Path(current))


class _InstallLock:
    """One installer per model across processes; the OS drops it if we die."""

    def __init__(self, root: Path, model_id: str):
        self.path = root / f".{model_id}.install-lock"
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise ModelCacheError("another install of this model is running") from exc
        return self

    def __exit__(self, *exc):
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


def _clear_pointer(model_dir: Path, revision: str) -> None:
    """Deactivate `revision` before its tree is replaced, so a crash leaves no
    pointer to a missing tree (readers then fall back, never read a gap)."""
    pointer = model_dir / "ACTIVE"
    if active_revision(model_dir.parent, model_dir.name) == revision:
        pointer.unlink()
        _sync_dir(model_dir)


def _write_pointer(model_dir: Path, revision: str) -> None:
    temporary = model_dir / f".ACTIVE.{secrets.token_hex(8)}"
    # Binary LF on every platform: text mode would write CRLF on Windows.
    with open(temporary, "wb") as handle:
        handle.write(revision.encode("ascii") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, model_dir / "ACTIVE")
    _sync_dir(model_dir)


def _extract_archive(archive: Path, staging: Path, manifest: ModelManifest) -> None:
    prefix = manifest.source["stripPrefix"]
    expected = {e.path: e for e in manifest.files}
    written = set()
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar:
                name = member.name
                if member.isdir():
                    continue
                if not member.isreg():
                    raise ModelCacheError(f"archive member is not a regular file: {name}")
                if not name.startswith(prefix) or name[len(prefix):] not in expected:
                    raise ModelCacheError(f"archive member is not in the manifest: {name}")
                relative = name[len(prefix):]
                entry = expected[relative]
                if relative in written:
                    raise ModelCacheError(f"archive member appears twice: {name}")
                if member.size != entry.size:
                    raise ModelCacheError(f"archive member size differs from the manifest: {name}")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ModelCacheError(f"archive member cannot be read: {name}")
                digest, total = hashlib.sha256(), 0
                with source, open(target, "wb") as out:
                    while chunk := source.read(1 << 20):
                        total += len(chunk)
                        if total > entry.size:
                            raise ModelCacheError(f"archive member exceeds its manifest size: {name}")
                        digest.update(chunk)
                        out.write(chunk)
                if total != entry.size or digest.hexdigest() != entry.sha256:
                    raise ModelCacheError(f"archive member does not match the manifest: {name}")
                written.add(relative)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ModelCacheError("archive could not be read") from exc
    missing = set(expected) - written
    if missing:
        raise ModelCacheError(f"archive is missing manifest files: {sorted(missing)[:3]}")


def _plain_chain(folder: Path, relative: str) -> Path | None:
    """`folder/relative` if no component from `folder` down is a link
    (symlink or Windows reparse point), else None."""
    path = Path(folder)
    if is_link(path):
        return None
    for part in relative.split("/"):
        path = path / part
        if is_link(path):
            return None
    return path


def _open_regular(path: Path) -> int:
    """Open without following a final link; refuse anything but a regular file."""
    # Non-blocking, so a FIFO or device cannot hang the install before the
    # type check; it changes nothing for regular files.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("not a regular file")
    return fd


def _copy_from(path: Path, dest: Path, max_bytes: int) -> tuple[int, str]:
    """Copy at most max_bytes + 1 bytes from one open handle; return what was
    copied (count and SHA-256) so the caller checks the bytes it got."""
    digest, written = hashlib.sha256(), 0
    with os.fdopen(_open_regular(path), "rb") as src, open(dest, "wb") as out:
        while written <= max_bytes:
            chunk = src.read(min(1 << 20, max_bytes + 1 - written))
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
            written += len(chunk)
    return written, digest.hexdigest()


def local_fetcher(folder: Path) -> Fetcher:
    """Offline import (T32): serve `<folder>/<file name>` for each requested
    URL. Bytes are verified exactly like a download; links are refused."""
    folder = Path(folder)

    def fetch(url: str, dest: Path, max_bytes: int) -> None:
        name = url.rsplit("/", 1)[-1]
        source = _plain_chain(folder, name) if name and "\\" not in name else None
        try:
            if source is None:
                raise OSError("link or bad name")
            _copy_from(source, dest, max_bytes)
        except OSError as exc:
            raise ModelCacheError(f"not in the offline folder: {name}") from exc

    return fetch


def _reuse_dirs(model_dir: Path, revision: str, extra: Sequence[Path]) -> list[Path]:
    """Where identical files may already be: this revision, other installed
    revisions of the model, then caller-given folders (e.g. legacy bundles).
    Hidden folders (staging, retired) and links are never sources."""
    found = []
    candidates = [model_dir / revision]
    try:
        candidates += sorted(p for p in model_dir.iterdir() if _plain_name(p.name) and p.name != revision)
    except OSError:
        pass
    inside = os.path.realpath(model_dir)
    for folder in map(Path, extra):
        real = os.path.realpath(folder)
        if folder.name.startswith(".") or real == inside or real.startswith(inside + os.sep):
            continue
        candidates.append(folder)
    for folder in candidates:
        if folder.is_dir() and not is_link(folder) and folder not in found:
            found.append(folder)
    return found


def _reuse(entry: FileEntry, target: Path, folders: Sequence[Path]) -> bool:
    """Copy an identical file into staging, verifying the bytes copied from a
    single no-follow open; a mismatch leaves nothing behind."""
    for folder in folders:
        for relative in dict.fromkeys((entry.path, entry.path.rsplit("/", 1)[-1])):
            candidate = _plain_chain(folder, relative)
            if candidate is None:
                continue
            try:
                copied = _copy_from(candidate, target, entry.size)
            except OSError:
                target.unlink(missing_ok=True)
                continue
            if copied == (entry.size, entry.sha256) and _matches(target, entry):
                return True
            target.unlink(missing_ok=True)
    return False


def install(manifest: ModelManifest, root: Path, fetch: Fetcher, *, reuse_dirs: Sequence[Path] = ()) -> InstallResult:
    """Make `manifest` the verified active revision, downloading only what is
    missing or damaged. Raises before activation on any mismatch."""

    root = Path(root)
    _check_root(root)
    with _InstallLock(root, manifest.id):
        return _install_locked(manifest, root, fetch, reuse_dirs)


def _install_locked(manifest: ModelManifest, root: Path, fetch: Fetcher, reuse_dirs: Sequence[Path]) -> InstallResult:
    model_dir = root / manifest.id
    if is_link(model_dir):
        raise ModelCacheError("model directory must not be a symlink")
    model_dir.mkdir(exist_ok=True)
    if is_link(model_dir):
        raise ModelCacheError("model directory must not be a symlink")
    # Leftovers of an interrupted install or replacement are never reused.
    for leftover in model_dir.iterdir():
        if leftover.name.startswith((".staging-", ".retired-", ".ACTIVE.")):
            if leftover.is_dir() and not is_link(leftover):
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)
    final = model_dir / manifest.revision
    if final.exists() and _verify_tree(final, manifest).ok:
        if active_revision(root, manifest.id) != manifest.revision:
            _write_pointer(model_dir, manifest.revision)
        return InstallResult(manifest.revision, 0, True)

    staging = model_dir / f".staging-{secrets.token_hex(8)}"
    staging.mkdir()
    downloaded = reused = 0
    folders = _reuse_dirs(model_dir, manifest.revision, reuse_dirs)
    try:
        if manifest.source["kind"] == "archive":
            # Only download the archive when some file is not here already.
            for entry in manifest.files:
                target = staging / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                if not _reuse(entry, target, folders):
                    break
                reused += 1
            else:
                folders = None
        if manifest.source["kind"] == "archive" and folders is not None:
            reused = 0
            shutil.rmtree(staging)
            staging.mkdir()
            archive = staging / ".archive"
            fetch(manifest.source["url"], archive, manifest.source["size"])
            downloaded += 1
            if _file_digest(archive, manifest.source["size"]) != (manifest.source["size"], manifest.source["sha256"]):
                raise ModelCacheError("downloaded archive does not match the manifest")
            _extract_archive(archive, staging, manifest)
            archive.unlink()
        elif manifest.source["kind"] == "files":
            for entry in manifest.files:
                target = staging / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                if _reuse(entry, target, folders):
                    reused += 1
                    continue
                fetch(manifest.source["baseUrl"] + entry.path, target, entry.size)
                downloaded += 1
                if not _matches(target, entry):
                    raise ModelCacheError(f"downloaded file does not match the manifest: {entry.path}")
        check = _verify_tree(staging, manifest)
        if not check.ok:
            raise ModelCacheError(f"staged model does not verify: {check.problems[:3]}")
        _sync_tree(staging)
        retired = None
        if final.exists() or final.is_symlink():
            # Only a damaged tree of the same revision is replaced. Deactivate
            # it first; the old tree is deleted only after the new one is live.
            _clear_pointer(model_dir, manifest.revision)
            retired = model_dir / f".retired-{secrets.token_hex(8)}"
            os.replace(final, retired)
        os.replace(staging, final)
        _write_pointer(model_dir, manifest.revision)
        if retired is not None:
            if is_link(retired):
                retired.unlink()
            else:
                shutil.rmtree(retired, ignore_errors=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return InstallResult(manifest.revision, downloaded, False, reused)
