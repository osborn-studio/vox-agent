"""Read-only check: preview/candidate tags must not trigger the macOS release workflow (T47q)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WORKFLOW = '.github/source-workflows/release-dmg.yml.txt'
PREVIEW_TAGS = ('v4.2.9-preview.1', 'v4.2.9-rc.1', 'v4.2.9-windows-preview.1')
FINAL_TAG = 'v4.2.9'


def github_glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a GitHub Actions filter pattern to a full-match regex.

    GitHub semantics (workflow-syntax "Filter pattern cheat sheet"):
    ``*`` zero or more characters except ``/``; ``**`` zero or more of any
    character; ``?`` zero or one of the preceding character; ``+`` one or
    more of the preceding character; ``[...]`` one character from a class;
    ``\\`` escapes the next character. Everything else is literal, including
    ``.``. Unsupported constructs raise ValueError so callers fail closed.
    """
    out: list[str] = []
    has_atom = False
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '*':
            if pattern[i:i + 2] == '**':
                out.append('.*')
                i += 2
            else:
                out.append('[^/]*')
                i += 1
            has_atom = False
            continue
        if c in '?+':
            if not has_atom:
                raise ValueError(f'"{c}" without a preceding character')
            out.append(c)
            has_atom = False
        elif c == '[':
            end = pattern.find(']', i + 1)
            if end == -1:
                raise ValueError('unterminated character class')
            out.append(pattern[i:end + 1])
            has_atom = True
            i = end + 1
            continue
        elif c == '\\':
            if i + 1 >= len(pattern):
                raise ValueError('dangling escape')
            out.append(re.escape(pattern[i + 1]))
            has_atom = True
            i += 2
            continue
        else:
            out.append(re.escape(c))
            has_atom = True
        i += 1
    return re.compile('^' + ''.join(out) + '$')


# Characters GitHub filter patterns need, minus anything YAML could transform.
# Backslashes (YAML escapes such as \u0076) and quotes are rejected, so the
# raw text we evaluate is exactly the string GitHub receives.
_SAFE_PATTERN = re.compile(r'^!?[A-Za-z0-9._/\-\[\]+*?]+$')
_ITEM = re.compile(r'^\s*-\s*(?P<quote>["\'])(?P<value>[^"\'\n]*)(?P=quote)\s*(?:#.*)?$')
_KEY = re.compile(r'^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?P<rest>.*)$')
_PUSH_FILTER_KEYS = {'tags', 'branches', 'branches-ignore', 'paths', 'paths-ignore'}


def _strip_comment(rest: str) -> str:
    return rest.split(' #', 1)[0].strip() if not rest.strip().startswith('#') else ''


def _is_skippable(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith('#')


def parse_push_tag_filters(workflow_text: str):
    """Strictly parse the `on.push.tags` list.

    Returns None when the workflow has no push trigger, [] when push has no
    tags list, else the list of tag patterns. Raises ValueError for anything
    this deliberately small parser cannot prove it read exactly as GitHub would
    (tabs, quoted or duplicate keys, flow/inline values, unknown push keys,
    unexpected indentation, unsafe scalars) so the caller fails closed.
    Comments are skipped and never end a block.
    """
    if '\t' in workflow_text:
        raise ValueError('tab characters are not supported')
    lines = workflow_text.splitlines()
    on_index = None
    top_level_keys = set()
    for index, line in enumerate(lines):
        if _is_skippable(line) or line.startswith(' '):
            continue
        # Every top-level line must be a plain `key:`; anything else (document
        # markers, `? key` complex keys, `key :` spacing, quoted or flow keys)
        # could hide another trigger declaration, so refuse to evaluate.
        key = _KEY.match(line)
        if not key:
            raise ValueError('unsupported top-level line')
        name = key.group('key')
        # YAML 1.1 resolves on/yes/true (any case) to the same boolean key.
        canonical = 'on' if name.lower() in {'on', 'true', 'yes', 'y'} else name
        if canonical in top_level_keys:
            raise ValueError(f'duplicate top-level key {name}')
        top_level_keys.add(canonical)
        if canonical == 'on':
            if _strip_comment(key.group('rest')):
                raise ValueError('inline on value (e.g. `on: push`) is not supported')
            on_index = index
    if on_index is None:
        return None

    events, push_keys, current_event, current_key = set(), {}, None, None
    for line in lines[on_index + 1:]:
        if _is_skippable(line):
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent == 0:
            break
        if indent == 2:
            key = _KEY.match(line)
            if not key:
                raise ValueError('unsupported event declaration')
            name = key.group('key')
            if name in events:
                raise ValueError(f'duplicate event {name}')
            events.add(name)
            current_event, current_key = name, None
            if name == 'push' and _strip_comment(key.group('rest')):
                raise ValueError('inline push value is not supported')
            continue
        if current_event != 'push':
            continue  # contents of other events do not affect tag pushes
        if indent == 4:
            key = _KEY.match(line)
            if not key or key.group('key') not in _PUSH_FILTER_KEYS:
                raise ValueError('unsupported push filter key')
            if key.group('key') in push_keys:
                raise ValueError('duplicate push filter key')
            if _strip_comment(key.group('rest')):
                raise ValueError('inline push filter values are not supported')
            current_key = key.group('key')
            push_keys[current_key] = []
            continue
        if indent >= 6 and current_key is not None and line.lstrip().startswith('- '):
            if current_key == 'tags':
                item = _ITEM.match(line)
                if not item or not _SAFE_PATTERN.match(item.group('value')):
                    raise ValueError('tag filter item is not a quoted scalar of safe pattern characters')
                push_keys['tags'].append(item.group('value'))
            continue
        raise ValueError('unexpected line inside push trigger')
    if 'push' not in events:
        return None
    return push_keys.get('tags', [])


def push_tag_patterns(workflow_text: str) -> list[str]:
    return parse_push_tag_filters(workflow_text) or []


def tag_matches_push_filter(workflow_text: str, tag: str) -> bool:
    """Evaluate patterns in order: a later match overrides an earlier one."""
    matched = False
    for pattern in push_tag_patterns(workflow_text):
        negative = pattern.startswith('!')
        if github_glob_to_regex(pattern[1:] if negative else pattern).match(tag):
            matched = not negative
    return matched


def _blocked(reasons: list[str], **extra) -> dict:
    return {'contract': 'release-trigger-isolation-v3', 'workflow': WORKFLOW,
            'status': 'BLOCKED', 'blockers': reasons, **extra}


def check_workflow_text(text: str) -> dict:
    try:
        patterns = parse_push_tag_filters(text)
    except ValueError as exc:
        return _blocked(['unparsable_filter_pattern'], error=str(exc))
    if patterns is None:
        return _blocked(['push_trigger_not_found'])
    if not patterns:
        return _blocked(['push_tag_filter_not_explicit'])
    try:
        preview = {tag: tag_matches_push_filter(text, tag) for tag in PREVIEW_TAGS}
        final = tag_matches_push_filter(text, FINAL_TAG)
    except (ValueError, re.error) as exc:
        return _blocked(['unparsable_filter_pattern'], error=str(exc))
    blockers = [f'preview_tag_triggers_release:{tag}' for tag, hit in preview.items() if hit]
    if not final:
        blockers.append('final_tag_does_not_trigger_release')
    return {
        'contract': 'release-trigger-isolation-v3', 'workflow': WORKFLOW,
        'push_tag_patterns': patterns,
        'preview_tag_triggers': any(preview.values()), 'preview_tags': preview,
        'final_tag_triggers': final, 'status': 'BLOCKED' if blockers else 'PASS',
        'blockers': blockers,
        'limitations': ['Strict subset parser of the release workflow trigger; anything outside '
                        'the subset is BLOCKED. Does not prove GitHub runtime behavior or budget state.'],
    }


def check_repo(repo: Path) -> dict:
    try:
        text = (repo / WORKFLOW).read_text(encoding='utf-8')
    except OSError:
        return {'status': 'BLOCKED', 'blockers': ['release_workflow_unreadable'], 'workflow': WORKFLOW}
    return check_workflow_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = check_repo(args.repo)
    print(json.dumps(result, indent=2))
    return 2 if result['status'] != 'PASS' else 0


if __name__ == '__main__':
    raise SystemExit(main())
