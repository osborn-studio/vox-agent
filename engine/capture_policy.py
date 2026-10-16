"""Capture policy for configured organization workspaces."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


def _hash_source(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _workspace_id(source_hash: str) -> str:
    return f"ws_{source_hash[:16]}"


def _normalize_repo_url(repo_url: str) -> str:
    normalized = repo_url.strip()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def _normalize_path(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


@dataclass(frozen=True)
class CaptureWorkspace:
    """Public capture workspace configuration without raw source locations."""

    org_id: str
    workspace_id: str
    source_hash: str
    source_type: str
    capture_enabled: bool = True


class CapturePolicy:
    """Allows capture only for explicitly configured workspaces or repos."""

    def __init__(self) -> None:
        self._workspaces: dict[str, CaptureWorkspace] = {}
        self._workspace_paths: dict[str, str] = {}
        self._repo_sources: dict[str, str] = {}

    def register_workspace(
        self,
        org_id: str,
        local_path: Path | str | None = None,
        repo_url: str | None = None,
    ) -> CaptureWorkspace:
        if (local_path is None and repo_url is None) or (
            local_path is not None and repo_url is not None
        ):
            raise ValueError("register_workspace requires exactly one local_path or repo_url")

        if local_path is not None:
            return self._register_local_workspace(org_id, local_path)
        return self._register_repo_workspace(org_id, repo_url or "")

    def is_capture_enabled(self, workspace_id: str) -> bool:
        workspace = self._workspaces.get(workspace_id)
        return bool(workspace and workspace.capture_enabled)

    def is_repo_capture_enabled(self, repo_url: str) -> bool:
        normalized_url = _normalize_repo_url(repo_url)
        workspace_id = self._repo_sources.get(normalized_url)
        return bool(workspace_id and self.is_capture_enabled(workspace_id))

    def pause_capture(self, workspace_id: str) -> CaptureWorkspace:
        return self._set_capture_enabled(workspace_id, False)

    def resume_capture(self, workspace_id: str) -> CaptureWorkspace:
        return self._set_capture_enabled(workspace_id, True)

    def workspace_for_path(self, file_path: Path | str) -> str | None:
        normalized_file = _normalize_path(file_path)
        matches = []
        for workspace_id, workspace_path in self._workspace_paths.items():
            try:
                Path(normalized_file).relative_to(workspace_path)
            except ValueError:
                continue
            matches.append((workspace_path, workspace_id))

        if not matches:
            return None
        return max(matches, key=lambda item: len(item[0]))[1]

    def is_path_capture_allowed(self, file_path: Path | str) -> bool:
        workspace_id = self.workspace_for_path(file_path)
        return bool(workspace_id and self.is_capture_enabled(workspace_id))

    def _register_local_workspace(
        self,
        org_id: str,
        local_path: Path | str,
    ) -> CaptureWorkspace:
        normalized_path = _normalize_path(local_path)
        source_hash = _hash_source(f"path:{normalized_path}")
        workspace = CaptureWorkspace(
            org_id=org_id,
            workspace_id=_workspace_id(source_hash),
            source_hash=source_hash,
            source_type="local_path",
        )
        self._workspaces[workspace.workspace_id] = workspace
        self._workspace_paths[workspace.workspace_id] = normalized_path
        return workspace

    def _register_repo_workspace(
        self,
        org_id: str,
        repo_url: str,
    ) -> CaptureWorkspace:
        normalized_url = _normalize_repo_url(repo_url)
        if not normalized_url:
            raise ValueError("repo_url cannot be empty")

        source_hash = _hash_source(f"repo:{normalized_url}")
        workspace = CaptureWorkspace(
            org_id=org_id,
            workspace_id=_workspace_id(source_hash),
            source_hash=source_hash,
            source_type="repo_url",
        )
        self._workspaces[workspace.workspace_id] = workspace
        self._repo_sources[normalized_url] = workspace.workspace_id
        return workspace

    def _set_capture_enabled(
        self,
        workspace_id: str,
        capture_enabled: bool,
    ) -> CaptureWorkspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise KeyError(workspace_id)

        updated = CaptureWorkspace(
            org_id=workspace.org_id,
            workspace_id=workspace.workspace_id,
            source_hash=workspace.source_hash,
            source_type=workspace.source_type,
            capture_enabled=capture_enabled,
        )
        self._workspaces[workspace_id] = updated
        return updated
