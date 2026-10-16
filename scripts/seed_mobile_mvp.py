#!/usr/bin/env python3
"""Seed the killer-workflow mock meeting into a running VOXAGENT engine.

Drives `engine.mock_asr.seed_workflow` against a real engine over HTTP
(`httpx.Client`), then runs the three verification searches from
CLAUDE_BUILD_INSTRUCTIONS §12 and prints a human-readable summary.

Usage:
    .venv/bin/python scripts/seed_mobile_mvp.py
    .venv/bin/python scripts/seed_mobile_mvp.py --base-url http://127.0.0.1:18999

Start the engine first (`vox-engine start` or `python -m engine.cli start`);
this script exits 1 with a clean message if the engine is unreachable.
"""

from __future__ import annotations

import argparse
import os
import sys

# Bootstrap: allow `python scripts/seed_mobile_mvp.py` from the repo root
# without installing the package first (mirrors how other scripts/ modules
# in this repo are invoked directly rather than via an installed console
# script).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from engine.mock_asr import seed_workflow

DEFAULT_BASE_URL = "http://127.0.0.1:18900"

VERIFICATION_QUERIES = ["Japan launch", "follow up", "正式会议纪要"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the VOXAGENT killer-workflow mock meeting into a running engine.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Engine base URL (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    try:
        with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
            result = seed_workflow(client)

            print("Seeded killer workflow:")
            print(f"  session:       {result['sessionId']}")
            print(f"  segments:      {len(result['segmentIds'])}")
            print(f"  memory nodes:  {len(result['nodeIds'])}")
            print()
            print("Verification searches:")
            for query in VERIFICATION_QUERIES:
                r = client.get("/v1/memory/search", params={"q": query})
                r.raise_for_status()
                count = len(r.json().get("results", []))
                status = "OK" if count > 0 else "MISSING"
                print(f'  [{status}] "{query}" -> {count} result(s)')

            r = client.get("/v1/memory/action-items")
            r.raise_for_status()
            print(f"\nAction items: {len(r.json())}")

            r = client.get("/v1/tokens/balance")
            r.raise_for_status()
            balance = r.json()
            print(f"Token balance: {balance.get('balance')} {balance.get('currency', '')}")

    except httpx.ConnectError:
        print(f"Engine not reachable at {args.base_url}. Start with: vox-engine start")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Seed failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
