"""content-addressed store (expos/adapters/content_store.py) + backup tool.

Covers the INDEX_M19_DATAVER borrow points:
  * put/get round-trip with sha256 verification (content address == bytes hash)
  * same content -> one object (dedup is the by-product of content addressing)
  * degradation chain: copy branch + hardlink branch (inode identity) + a forced
    reflink-failure that degrades to the next method
  * 0o444 protection: a stored object cannot be opened for write
  * gc: dry-run deletes nothing / keep-set really deletes the unreachable /
    refuses to run without an explicit keep set (double gate)
  * dedupe_run: byte-equivalence preserved + workdir file & store object share
    one inode
  * backup_incremental: idempotent (second run mirrors zero new oids)
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from expos.adapters import content_store as store_ca

# Load the script module (scripts/ is not a package).
_BACKUP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backup_incremental.py"
_spec = importlib.util.spec_from_file_location("backup_incremental", _BACKUP_PATH)
backup_incremental = importlib.util.module_from_spec(_spec)
sys.modules["backup_incremental"] = backup_incremental
_spec.loader.exec_module(backup_incremental)


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- #
# put / get round-trip + sha verification
# --------------------------------------------------------------------------- #
def test_put_get_roundtrip_sha(tmp_path):
    root = tmp_path / "cas"
    src = _write(tmp_path / "a.json", b'{"hello":"world"}')
    import hashlib

    expected = hashlib.sha256(src.read_bytes()).hexdigest()

    oid = store_ca.put(src, root=root)
    assert oid == expected
    got = store_ca.get(oid, root=root)
    assert got.read_bytes() == b'{"hello":"world"}'
    # sharded path shape <root>/<oid[:2]>/<oid[2:]>
    assert got == root / oid[:2] / oid[2:]
    assert store_ca.oid_of(got) == oid


def test_get_missing_raises(tmp_path):
    with pytest.raises(KeyError):
        store_ca.get("0" * 64, root=tmp_path / "cas")


def test_same_content_stored_once(tmp_path):
    root = tmp_path / "cas"
    a = _write(tmp_path / "a", b"identical-bytes")
    b = _write(tmp_path / "b", b"identical-bytes")
    oid_a = store_ca.put(a, root=root)
    oid_b = store_ca.put(b, root=root)
    assert oid_a == oid_b
    # exactly one object across the whole store
    assert list(store_ca.iter_oids(root=root)) == [oid_a]


def test_different_content_two_objects(tmp_path):
    root = tmp_path / "cas"
    store_ca.put(_write(tmp_path / "a", b"aaa"), root=root)
    store_ca.put(_write(tmp_path / "b", b"bbb"), root=root)
    assert len(list(store_ca.iter_oids(root=root))) == 2


# --------------------------------------------------------------------------- #
# degradation chain: copy / hardlink / forced-degrade
# --------------------------------------------------------------------------- #
def test_transfer_copy_branch(tmp_path):
    src = _write(tmp_path / "s", b"payload")
    dst = tmp_path / "d"
    used = store_ca.transfer(src, dst, links=("copy",))
    assert used == "copy"
    assert dst.read_bytes() == b"payload"
    # copy => independent inode
    assert os.stat(src).st_ino != os.stat(dst).st_ino


def test_transfer_hardlink_branch(tmp_path):
    src = _write(tmp_path / "s", b"payload")
    dst = tmp_path / "d"
    used = store_ca.transfer(src, dst, links=("hardlink",))
    assert used == "hardlink"
    # hardlink => shared inode
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_transfer_degrades_on_reflink_failure(tmp_path, monkeypatch):
    """Force reflink to raise EXDEV; the chain must fall through to hardlink."""

    def _boom(src, dst):
        raise OSError(18, "Invalid cross-device link")  # EXDEV

    monkeypatch.setitem(store_ca._METHODS, "reflink", _boom)
    src = _write(tmp_path / "s", b"payload")
    dst = tmp_path / "d"
    used = store_ca.transfer(src, dst, links=("reflink", "hardlink", "copy"))
    assert used == "hardlink"
    assert dst.read_bytes() == b"payload"
    # no partial dst left by the failed reflink attempt
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_transfer_all_fail_raises(tmp_path):
    src = _write(tmp_path / "s", b"x")
    with pytest.raises(ValueError):
        store_ca.transfer(src, tmp_path / "d", links=())


# --------------------------------------------------------------------------- #
# 0o444 protection
# --------------------------------------------------------------------------- #
def test_stored_object_is_readonly(tmp_path):
    root = tmp_path / "cas"
    oid = store_ca.put(_write(tmp_path / "a", b"immutable"), root=root)
    obj = store_ca.get(oid, root=root)
    assert (obj.stat().st_mode & 0o777) == store_ca.OBJECT_MODE
    with pytest.raises(PermissionError):
        with open(obj, "wb") as f:  # writing an existing object must fail first
            f.write(b"tamper")


# --------------------------------------------------------------------------- #
# gc: dry-run / real delete / refuse without keep set
# --------------------------------------------------------------------------- #
def test_gc_refuses_without_keep_set(tmp_path):
    with pytest.raises(ValueError):
        store_ca.gc(None, root=tmp_path / "cas")


def test_gc_dry_run_deletes_nothing(tmp_path):
    root = tmp_path / "cas"
    oid = store_ca.put(_write(tmp_path / "a", b"data"), root=root)
    rep = store_ca.gc(set(), root=root)  # empty keep set, but dry-run default
    assert rep.dry_run is True
    assert oid in rep.swept_oids
    assert store_ca.has(oid, root=root)  # still there — nothing deleted


def test_gc_deletes_unreachable_keeps_reachable(tmp_path):
    root = tmp_path / "cas"
    keep = store_ca.put(_write(tmp_path / "keep", b"reachable"), root=root)
    drop = store_ca.put(_write(tmp_path / "drop", b"orphan"), root=root)
    rep = store_ca.gc({keep}, root=root, dry_run=False)
    assert rep.dry_run is False
    assert drop in rep.swept_oids and keep not in rep.swept_oids
    assert store_ca.has(keep, root=root)
    assert not store_ca.has(drop, root=root)


# --------------------------------------------------------------------------- #
# dedupe_run: byte-equivalence + inode union
# --------------------------------------------------------------------------- #
def _fake_run(tmp_path: Path, payloads: dict[str, bytes]) -> Path:
    """Build a minimal run tree: <run>/_dry_jobs/<job>/result.json."""
    run = tmp_path / "run"
    jobs = run / "_dry_jobs"
    for job, data in payloads.items():
        _write(jobs / job / "result.json", data)
    return run


def test_dedupe_run_dry_run_touches_nothing(tmp_path):
    root = tmp_path / "cas"
    run = _fake_run(tmp_path, {"j0": b"same", "j1": b"same", "j2": b"other"})
    inos_before = {p: p.stat().st_ino for p in run.rglob("result.json")}
    rep = store_ca.dedupe_run(run, root=root, dry_run=True)
    assert rep.dry_run is True
    assert rep.total_files == 3
    assert rep.distinct_oids == 2  # two "same" collapse
    assert all(r.action == "would-link" for r in rep.receipts)
    # nothing put, nothing relinked
    assert list(store_ca.iter_oids(root=root)) == []
    assert {p: p.stat().st_ino for p in run.rglob("result.json")} == inos_before
    assert rep.reclaimable_bytes == len(b"same")  # one duplicate copy freed


def test_dedupe_run_byte_equiv_and_inode_union(tmp_path):
    root = tmp_path / "cas"
    run = _fake_run(tmp_path, {"j0": b"same-bytes", "j1": b"same-bytes"})
    files = sorted(run.rglob("result.json"))
    original = files[0].read_bytes()

    rep = store_ca.dedupe_run(run, root=root, dry_run=False)
    assert rep.distinct_oids == 1
    # bytes preserved exactly (lossless)
    for f in files:
        assert f.read_bytes() == original
    # both workdir files + the store object share ONE inode
    oid = rep.receipts[0].oid
    obj = store_ca.get(oid, root=root)
    inodes = {f.stat().st_ino for f in files} | {obj.stat().st_ino}
    assert len(inodes) == 1


def test_dedupe_run_idempotent(tmp_path):
    root = tmp_path / "cas"
    run = _fake_run(tmp_path, {"j0": b"same-bytes", "j1": b"same-bytes"})
    store_ca.dedupe_run(run, root=root, dry_run=False)
    rep2 = store_ca.dedupe_run(run, root=root, dry_run=False)
    assert all(r.action == "already-linked" for r in rep2.receipts)


# --------------------------------------------------------------------------- #
# backup_incremental: idempotency
# --------------------------------------------------------------------------- #
def test_backup_incremental_idempotent(tmp_path):
    root = tmp_path / "cas"
    dest = tmp_path / "mirror"
    for i, data in enumerate([b"one", b"two", b"three"]):
        store_ca.put(_write(tmp_path / f"f{i}", data), root=root)

    first = backup_incremental.run_backup(root, dest, runs_root=None, dry_run=False)
    assert first["new_oids"] == 3
    assert first["scanned_oids"] == 3

    # second run: everything already mirrored -> zero new
    second = backup_incremental.run_backup(root, dest, runs_root=None, dry_run=False)
    assert second["new_oids"] == 0
    assert second["scanned_oids"] == 3

    # MANIFEST holds exactly the 3 first-run lines (no dup appends)
    manifest = (dest / "MANIFEST.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 3


def test_backup_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "cas"
    dest = tmp_path / "mirror"
    store_ca.put(_write(tmp_path / "f", b"data"), root=root)
    summary = backup_incremental.run_backup(root, dest, runs_root=None, dry_run=True)
    assert summary["new_oids"] == 1
    assert not dest.exists()  # dry-run created no mirror


def test_backup_referrer_index_by_inode(tmp_path):
    """A deduped workdir file (hardlink to a store object) is discovered as a
    referrer of its oid via inode match."""
    root = tmp_path / "cas"
    run = _fake_run(tmp_path, {"j0": b"same", "j1": b"same"})
    store_ca.dedupe_run(run, root=root, dry_run=False)
    refs = backup_incremental._build_referrer_index(run, root)
    # one oid, referenced by both deduped workdir files
    assert len(refs) == 1
    (paths,) = refs.values()
    assert len(paths) == 2
