#!/usr/bin/env python3
"""Bounded metadata-only inventory; never reads ciphertext, Keychain or transport."""
import argparse
import json
import os
from pathlib import Path
import re
import stat

SNAPSHOT = re.compile(r"[a-f0-9]{64}\.v1\.aes", re.ASCII)
DECISION = re.compile(r"[a-f0-9]{64}\.v1\.decision", re.ASCII)
ARCHIVE = re.compile(r"[a-f0-9]{64}\.[a-f0-9]{64}\.v1\.aes", re.ASCII)


class InventoryError(Exception):
    """Only a safe fixed code may be surfaced."""


def _chain(path, boundary):
    if path == boundary or boundary not in path.parents or len(path.relative_to(boundary).parts) > 32:
        raise InventoryError("INVALID_BOUNDARY")
    cursor = path
    while True:
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            if cursor == boundary:
                raise InventoryError("BOUNDARY_UNAVAILABLE") from None
        except OSError:
            raise InventoryError("ANCESTOR_UNAVAILABLE") from None
        else:
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or (cursor != boundary and info.st_mode & 0o022):
                raise InventoryError("UNSAFE_ANCESTOR")
        if cursor == boundary:
            return
        cursor = cursor.parent


def _scan(path, *, archive=False, input_store=False, request_store=False):
    result = {"status": "NOT_FOUND", "entriesSeen": 0, "scanTruncated": False, "countsComplete": True,
              "snapshots": 0, "decisions": 0, "foreignEntries": 0, "orphanTemps": 0,
              "unsafeCipherEntries": 0, "recognizedCipherBytes": 0,
              "newEntryCapacityReached": False, "snapshotCapacityReached": False,
              "byteCapacityReached": False}
    descriptor = None
    try:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return result
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            result["status"] = "UNSAFE"
            result["countsComplete"] = False
            return result
        result["status"] = "METADATA_ONLY"
        maximum = 64_000 if input_store else 2_000_000
        with os.scandir(descriptor) as stream:
            for entry in stream:
                result["entriesSeen"] += 1
                if result["entriesSeen"] > 256:
                    result["scanTruncated"] = True
                    result["countsComplete"] = False
                    break  # 257 is a lower bound, never a claim of full directory size.
                is_snapshot = bool((ARCHIVE if archive else SNAPSHOT).fullmatch(entry.name))
                is_decision = request_store and bool(DECISION.fullmatch(entry.name))
                if not (is_snapshot or is_decision):
                    if entry.name.startswith((".request-", ".input-", ".draft-")):
                        result["orphanTemps"] += 1
                    elif entry.name != ".vault.lock" and not (request_store and entry.name == "Archived"):
                        result["foreignEntries"] += 1
                    continue
                result["snapshots" if is_snapshot else "decisions"] += 1
                try:
                    info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                except OSError:
                    result["unsafeCipherEntries"] += 1
                    continue
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                        or info.st_mode & 0o077 or not 0 < info.st_size <= maximum):
                    result["unsafeCipherEntries"] += 1
                    continue
                result["recognizedCipherBytes"] += info.st_size
        result["newEntryCapacityReached"] = result["entriesSeen"] >= 256
        result["snapshotCapacityReached"] = result["snapshots"] >= 64
        result["byteCapacityReached"] = result["recognizedCipherBytes"] >= (4_000_000 if input_store else 64_000_000)
        if result["scanTruncated"] or result["unsafeCipherEntries"] or result["foreignEntries"] or result["orphanTemps"]:
            result["status"] = "ACTION_REQUIRED"
    except OSError:
        result["status"] = "UNAVAILABLE"
        result["countsComplete"] = False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return result


def operator_actions(stores):
    """Fixed advice codes only; never authorize mutation from a live inventory."""
    actions = []
    for name, value in stores.items():
        codes = []
        if value["status"] in ("UNSAFE", "UNAVAILABLE"):
            codes.append("CHECK_OWNER_PERMISSIONS_AND_VOLUME")
        if not value.get("countsComplete", False):
            codes.append("COUNTS_INCOMPLETE_DO_NOT_ASSUME_FREE_CAPACITY")
        if value.get("unsafeCipherEntries", 0):
            codes.append("PRESERVE_UNSAFE_ENTRIES_FOR_OWNER_REVIEW")
        if value.get("foreignEntries", 0) or value.get("orphanTemps", 0):
            codes.append("PRESERVE_UNKNOWN_AND_TEMP_ENTRIES")
        if any(value.get(k, False) for k in (
                "newEntryCapacityReached", "snapshotCapacityReached", "byteCapacityReached")):
            codes.append("PAUSE_NEW_ALLOCATIONS_AND_PLAN_VERIFIED_EXPORT")
        if value["status"] == "NOT_FOUND":
            codes.append("CHECK_EXPECTED_STORE_LOCATION_NO_KEY_RESET")
        if codes:
            actions.append({"store": name, "codes": codes})
    return actions


def inspect_storage(root, boundary):
    # abspath normalizes separators/dot components without resolving any symlinks.
    root = Path(os.path.abspath(root))
    boundary = Path(os.path.abspath(boundary))
    _chain(root, boundary)
    scopes = {"inputs": root / "Inputs", "drafts": root / "Drafts", "requests": root / "Requests",
              "requestArchives": root / "Requests" / "Archived"}
    stores = {}
    for name, path in scopes.items():
        try:
            _chain(path, boundary)
            stores[name] = _scan(path, archive=name == "requestArchives", input_store=name == "inputs", request_store=name == "requests")
        except InventoryError as error:
            stores[name] = {"status": "UNSAFE", "code": str(error), "countsComplete": False}
    return {"version": 1, "meaning": "bounded-metadata-only-not-recovery-proof", "stores": stores,
            "ciphertextAuthentication": "NOT_RUN", "keychain": "NOT_RUN", "recovery": "NOT_RUN",
            "operatorActions": operator_actions(stores),
            "mutationAuthorization": "NONE"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "Library/Application Support/VoxAgentPrivateContext")
    parser.add_argument("--boundary", type=Path, default=Path.home() / "Library")
    args = parser.parse_args()
    try:
        value = inspect_storage(args.root, args.boundary)
    except InventoryError as error:
        print(json.dumps({"version": 1, "status": "INVALID", "code": str(error)}))
        return 2
    print(json.dumps(value, sort_keys=True))
    bad = any(v["status"] not in ("NOT_FOUND", "METADATA_ONLY") or any(
        v.get(k, False) for k in ("newEntryCapacityReached", "snapshotCapacityReached", "byteCapacityReached"))
        for v in value["stores"].values())
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
