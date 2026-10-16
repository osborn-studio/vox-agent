"""T01c: extract the macOS Context / three-pane baseline subset from a fixed source.

The baseline is the installed macOS release source (v4.2.8.1 = 5c9f3a7, the
Context sidebar build that is not on main). From it this script extracts, as
facts rather than opinions:

- the Context-facing API paths the Swift client calls (engine routes are
  listed separately: they are outside this subset);
- the sidebar selection cases and the sidebar's bilingual labels.

Usage:
  python scripts/t01c_context_baseline.py --write   # regenerate the snapshot
  python scripts/t01c_context_baseline.py --check   # compare (skips if the SHA is absent)

The snapshot is docs/verification/windows-t01c-context-baseline.json; the
mapping to Windows lives in windows-t01c-context-baseline.md and a test keeps
both in step. This is a subset (T01c), not the full baseline inventory (T01).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "5c9f3a74d3bf438373bac50fb4e0d3dc3d3f5975"
SOURCE_REF = "v4.2.8.1"
SNAPSHOT = ROOT / "docs/verification/windows-t01c-context-baseline.json"
ENGINE_PREFIXES = ("v1/agents", "v1/tasks", "v1/permissions", "v1/message")
_PATH = re.compile(r'"/?(v1/[A-Za-z0-9_/{}.-]*)')
_AUTH = re.compile(r'request\("([a-z-]+)"')
_CASE = re.compile(r"^\s*case\s+(.+)$", re.M)
_LABEL = re.compile(r'L\(\s*"([^"]+)",\s*"([^"]+)"\s*\)', re.S)
# Literal path segments appended to a v1 path at run time ("/messages", ...).
_SEGMENT = re.compile(r'"(/[a-z-]+/?|[a-z-]+/)"')


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def available() -> bool:
    return subprocess.run(["git", "cat-file", "-e", SOURCE_COMMIT], cwd=ROOT, capture_output=True).returncode == 0


def _show(path: str) -> str:
    return _git("show", f"{SOURCE_COMMIT}:{path}")


def _normalize(path: str) -> str:
    return path.rstrip("/.")


def extract() -> dict:
    files = [f for f in _git("ls-tree", "-r", "--name-only", SOURCE_COMMIT, "macos/VoxLog").split() if f.endswith(".swift")]
    context, engine, segments = set(), set(), set()
    for name in files:
        text = _show(name)
        context_paths = [p for p in map(_normalize, _PATH.findall(text)) if p and not p.startswith(ENGINE_PREFIXES)]
        if context_paths:
            segments.update(seg.strip("/") for seg in _SEGMENT.findall(text))
        for raw in _PATH.findall(text):
            path = _normalize(raw)
            # "v1" and "v1/auth/feishu" are bases the Swift code appends to.
            if not path or path in ("v1", "v1/auth/feishu"):
                continue
            (engine if path.startswith(ENGINE_PREFIXES) else context).add(path)
        if name.endswith("FeishuAccountClient.swift"):
            context.update(f"v1/auth/feishu/{segment}" for segment in _AUTH.findall(text))
    selection = _show("macos/VoxLog/UI/Chat/SidebarSelection.swift")
    enum = selection[selection.index("enum SidebarSelection"):selection.index("}", selection.index("enum SidebarSelection"))]
    cases = sorted({c.strip() for line in _CASE.findall(enum) for c in line.split(",")})
    labels = [{"zh": zh, "en": en} for zh, en in _LABEL.findall(_show("macos/VoxLog/UI/Chat/ChatSidebar.swift"))]
    unique = []
    for label in labels:
        if label not in unique:
            unique.append(label)
    return {
        "sourceCommit": SOURCE_COMMIT,
        "sourceRef": SOURCE_REF,
        "scope": "T01c subset: Context API paths, sidebar selections and sidebar labels; not the full baseline (T01)",
        "contextApiPaths": sorted(context),
        "engineApiPathsOutOfScope": sorted(engine),
        "dynamicPathSegments": sorted(segments),
        "sidebarSelections": cases,
        "sidebarLabels": unique,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not available():
        print(f"source {SOURCE_REF} ({SOURCE_COMMIT[:7]}) is not in this clone; skipped", file=sys.stderr)
        return 1 if args.write else 0
    text = json.dumps(extract(), ensure_ascii=False, indent=2) + "\n"
    if args.write:
        SNAPSHOT.write_text(text, encoding="utf-8")
        return 0
    if SNAPSHOT.read_text(encoding="utf-8") != text:
        print(f"{SNAPSHOT.relative_to(ROOT)} is stale; run --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
