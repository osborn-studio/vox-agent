# ADR: agent instruction authority and mirror exception

- Date: 2026-07-18
- Status: Accepted
- Scope: root `AGENTS.md`, `CLAUDE.md`, and UI design context

## Decision

`CLAUDE.md` is the detailed project instruction source. `AGENTS.md` is a compact
bootstrap for Codex and other non-Claude agents and explicitly requires them to
open `CLAUDE.md` plus referenced task context before changing code.

The files are intentionally not byte-identical, symlinked, or connected by a
one-line `@AGENTS.md` import. `CLAUDE.md` must directly retain the mandatory
`DESIGN.md` instruction and the current project map. Agents that read
`AGENTS.md` follow its pointer and receive the same detailed source.

## Context

The repository already used this direction when the owner required every UI
agent session to read `DESIGN.md` in full. Replacing `CLAUDE.md` with
`@AGENTS.md` would remove that direct Claude Code context, while copying all
detailed instructions into `AGENTS.md` would duplicate a fast-moving inventory
and create the drift it is meant to prevent.

osstack 0.9.0's doctor currently recognizes only the opposite convention:
`AGENTS.md` canonical with `CLAUDE.md` as a symlink or exact one-line import. It
therefore reports `agent_instructions` as an error for this repository even
though the authority direction is explicit and tested. That result is a known
validator limitation, not permission to rewrite user-owned instruction files.

## Consequences

- `AGENTS.md` stays small and must keep an explicit semantic pointer to
  `CLAUDE.md` and its referenced files.
- Detailed architecture, test commands, security boundaries, naming, and design
  rules live only in `CLAUDE.md` and their linked authoritative documents.
- `tests/test_instruction_contract.py` protects the direction and mandatory
  design pointer without requiring byte equality or rewriting dynamic memory
  context appended to `AGENTS.md`.
- Inventory claims in `CLAUDE.md` are dated snapshots and must be measured from
  the worktree before refresh.
- The osstack validator should eventually support a declared instruction mode
  or reverse-reference exception. Until then, its mirror finding remains an
  explicit false positive; other doctor findings still apply normally.

## Rejected alternatives

- **Replace `CLAUDE.md` with `@AGENTS.md`:** loses the direct project/design
  context required by the owner.
- **Symlink the files:** cannot represent different bootstrap and detailed
  consumer needs.
- **Duplicate the complete text:** creates two editable authorities and genuine
  drift risk.
- **Edit `AGENTS.md` to satisfy the checker:** overwrites a user-owned protected
  file for a tool-specific cosmetic result.
