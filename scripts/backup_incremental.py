#!/usr/bin/env python3
"""backup_incremental — content-addressed incremental backup of the CAS store.

Design lineage: /Data1/ericyang/r4_os_references/INDEX_M19_DATAVER.md §6 (once
artifacts live in a content-addressed store, backup degrades to "sync the new
oids": each object's filename IS its content hash, so the mirror only ever needs
the oids it does not already have — increment + dedup + checksum in one). Explicit
not-copy red line (INDEX §7): NO remote backend, NO `dvc push`, NO git — a plain
local mirror of the two-level shard tree plus an append-only MANIFEST ledger.

Contract:
  * READ-ONLY on the source store: it only stats and copies out, never mutates.
  * IDEMPOTENT: a second run finds every source oid already mirrored -> zero new
    objects, zero new MANIFEST lines.
  * INCREMENTAL: only oids absent from the destination are copied.
  * --dry-run is the DEFAULT: it reports what would be mirrored; pass --apply to
    actually copy.

MANIFEST increment line (INDEX §6: "oid, original-path list, bytes"): one JSON
object per newly-mirrored oid, carrying the oid, its byte size, and the workdir
paths that reference it (resolved by inode against ``--runs-root`` — a deduped
workdir file hardlinks the store object, so a shared inode identifies referrers).

Usage:
    python scripts/backup_incremental.py                 # dry-run, default paths
    python scripts/backup_incremental.py --apply         # actually mirror
    python scripts/backup_incremental.py --source runs/.cas --dest /path/mirror \\
        --runs-root runs --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Make `expos` importable when run as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from expos.adapters import content_store as store_ca  # noqa: E402

DEFAULT_DEST = "/Data1/ericyang/expos_backup_20260713/cas_mirror/"
_MANIFEST = "MANIFEST.jsonl"


def _dest_has(dest: Path, oid: str) -> bool:
    return (dest / oid[:2] / oid[2:]).exists()


def _build_referrer_index(runs_root: Path, source: Path) -> dict[str, list[str]]:
    """Map oid -> [workdir paths] by inode.

    A deduped workdir file is a hardlink to its store object, so
    ``(st_dev, st_ino)`` of a workdir file equal to a store object's identifies
    which paths reference that oid. Best-effort: files that were never deduped
    (independent inode) contribute no referrer. Cheap: one stat per file."""
    if not runs_root.exists():
        return {}
    # (dev, ino) -> oid, for every store object.
    ino_to_oid: dict[tuple[int, int], str] = {}
    for oid in store_ca.iter_oids(root=source):
        try:
            st = (source / oid[:2] / oid[2:]).stat()
        except OSError:
            continue
        ino_to_oid[(st.st_dev, st.st_ino)] = oid
    refs: dict[str, list[str]] = {}
    for dirpath, _dirs, files in os.walk(runs_root):
        for fn in files:
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            oid = ino_to_oid.get((st.st_dev, st.st_ino))
            if oid is not None:
                refs.setdefault(oid, []).append(str(p))
    return refs


def run_backup(
    source: Path,
    dest: Path,
    *,
    runs_root: Path | None,
    dry_run: bool,
) -> dict:
    """Mirror source-store oids missing from dest. Returns a summary dict."""
    if not source.exists():
        raise SystemExit(f"source store does not exist: {source}")

    refs = _build_referrer_index(runs_root, source) if runs_root else {}

    new_oids: list[str] = []
    new_bytes = 0
    scanned = 0
    manifest_lines: list[str] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    for oid in store_ca.iter_oids(root=source):
        scanned += 1
        if _dest_has(dest, oid):
            continue
        src_obj = source / oid[:2] / oid[2:]
        try:
            size = src_obj.stat().st_size
        except OSError:
            continue
        new_oids.append(oid)
        new_bytes += size
        line = json.dumps(
            {
                "oid": oid,
                "bytes": size,
                "paths": refs.get(oid, []),
                "mirrored_at": ts,
            },
            sort_keys=True,
        )
        manifest_lines.append(line)
        if not dry_run:
            dst_obj = dest / oid[:2] / oid[2:]
            dst_obj.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst_obj.parent / (dst_obj.name + f".tmp-{os.getpid()}")
            shutil.copyfile(src_obj, tmp)  # read-only on source
            try:
                os.chmod(tmp, store_ca.OBJECT_MODE)
            except OSError:
                pass
            os.replace(tmp, dst_obj)

    if not dry_run and manifest_lines:
        dest.mkdir(parents=True, exist_ok=True)
        with open(dest / _MANIFEST, "a", encoding="utf-8") as mf:
            for line in manifest_lines:
                mf.write(line + "\n")

    return {
        "source": str(source),
        "dest": str(dest),
        "dry_run": dry_run,
        "scanned_oids": scanned,
        "new_oids": len(new_oids),
        "new_bytes": new_bytes,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--source",
        default=str(store_ca.default_root()),
        help="CAS store root to back up (default: EXPOS_CAS_ROOT or runs/.cas)",
    )
    ap.add_argument("--dest", default=DEFAULT_DEST, help="mirror directory")
    ap.add_argument(
        "--runs-root",
        default="runs",
        help="run tree to resolve oid->referrer paths by inode (default: runs)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually mirror (default is dry-run: report only)",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    runs_root = Path(args.runs_root) if args.runs_root else None
    summary = run_backup(
        Path(args.source),
        Path(args.dest),
        runs_root=runs_root,
        dry_run=not args.apply,
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        mode = "DRY-RUN" if summary["dry_run"] else "APPLIED"
        print(
            f"[{mode}] source={summary['source']} dest={summary['dest']}\n"
            f"  scanned oids : {summary['scanned_oids']}\n"
            f"  new oids     : {summary['new_oids']}\n"
            f"  new bytes    : {summary['new_bytes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
