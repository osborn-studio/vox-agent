#!/usr/bin/env python3
"""Auto-sync voice logs to Obsidian vault.

Designed to run daily via cron/launchd:
    0 2 * * * cd ~/vox-agents && .venv/bin/python export_cron.py

Syncs the most recent 7 UTC days into the Vox Agent directory in the vault.
"""

from __future__ import annotations

from pathlib import Path

from integrations.obsidian import sync_recent

DB_PATH = Path.home() / ".voxlog2" / "history.db"


def main():
    if not DB_PATH.exists():
        return

    exported = sync_recent(DB_PATH, days=7)
    if exported:
        print(f"Synced {len(exported)} day(s) to Obsidian:")
        for f in exported:
            print(f"  {f}")
    else:
        print("Nothing new to sync.")


if __name__ == "__main__":
    main()
