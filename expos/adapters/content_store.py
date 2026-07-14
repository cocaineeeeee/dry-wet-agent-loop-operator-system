"""content_store — single-machine content-addressed store for run artifacts.

Lives in the adapters layer (infrastructure below the kernel): the
kernel/planner/qc never import downward into adapters, so a content store here
cannot enter the kernel's import-reachable surface (layering red line).

Design lineage: /Data1/ericyang/r4_os_references/INDEX_M19_DATAVER.md (DVC
content-store borrow, §1/§4/§5/§6). Explicit not-copy red line (INDEX §7): NO
daemon, NO database, NO git artifact tracking, NO remote backend, NO `dvc`
dependency. This is a pure-function + file-state store: an object's address IS
the sha256 of its bytes, its on-disk path is a two-level shard of that address,
and de-duplication is the *by-product* of content addressing (same bytes ->
same oid -> one object), never a separate step.

What this module is and is NOT:

  * IS   a byte-exact content store: ``put`` hashes the file's bytes. Two files
         with identical bytes collapse to one object; two files that differ by a
         single bit are two objects. This never loses or rewrites data.
  * NOT  a semantic de-duplicator. expos ``result.json`` carries full-precision
         floats that jitter sub-ULP between reruns of the same ``spec_sha`` (the
         jitter-absorbing digest is the ``result_sha`` *field*, not the file's
         bytes). So byte-addressing collapses only truly identical files; on a
         corpus of freshly-computed results the byte-dedup ratio is ~1:1. The
         win materialises for genuinely repeated bytes (re-copied runs, stable
         raw sidecars, incremental backup of an unchanged store).

Boundaries (INDEX §7 not-copy): this module NEVER touches the event stream, the
decision face, the kernel/planner/qc, or the dry adapter's reuse judgement. It
is opt-in: nothing calls it unless a caller (``dedupe_run`` post-processing, the
``scripts/backup_incremental.py`` tool, or a future gc CLI) asks it to.

Transfer degradation chain (INDEX §1, borrowed from DVC ``_try_links``):
``reflink -> hardlink -> copy``. reflink (CoW, independent + space-saving) is
tried first; on a filesystem that cannot reflink (EXDEV / ENOTSUP / ...) it
degrades to hardlink (space-saving, shared inode), then to copy (always works).
This is what lets the store run on any filesystem.

Read-only protection (INDEX §1, DVC ``CACHE_MODE=0o444``): every stored object
is chmod 0o444 after import, so a caller that shares an object's inode (hardlink)
cannot corrupt the shared bytes by writing through the workdir copy. Objects are
write-once by contract; the read-only bit is the physical backstop.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: FICLONE ioctl request (Linux, common archs) — reflink/CoW clone of a whole
#: file. Not defined on non-Linux; the reflink attempt then fails and the chain
#: degrades to hardlink/copy exactly as on a non-reflink filesystem.
_FICLONE = 0x40049409

#: Default degradation chain: independent-CoW first, shared-inode next, real
#: copy last (INDEX §1).
DEFAULT_LINKS: tuple[str, ...] = ("reflink", "hardlink", "copy")

#: Read-only mode stamped on every stored object (DVC CACHE_MODE parity).
OBJECT_MODE = 0o444

#: Chunk size for streaming sha256 (avoid loading large artifacts whole).
_CHUNK = 1 << 20

_ENV_ROOT = "EXPOS_CAS_ROOT"
_DEFAULT_ROOT = "runs/.cas"


# --------------------------------------------------------------------------- #
# store root + address arithmetic
# --------------------------------------------------------------------------- #
def default_root() -> Path:
    """Store root: ``$EXPOS_CAS_ROOT`` if set, else ``runs/.cas`` (cwd-relative,
    matching the run-tree layout). Not created here — ``put`` creates on demand."""
    return Path(os.environ.get(_ENV_ROOT, _DEFAULT_ROOT))


def _root(root: str | Path | None) -> Path:
    return Path(root) if root is not None else default_root()


def oid_of(path: str | Path) -> str:
    """Content address of a file = sha256 hex of its bytes (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def oid_to_path(oid: str, *, root: str | Path | None = None) -> Path:
    """Two-level shard path ``<root>/<oid[:2]>/<oid[2:]>`` (DVC ``_oid_parts``).
    The 256-way prefix fan-out keeps any single directory from exploding."""
    if len(oid) < 3:
        raise ValueError(f"not a valid oid: {oid!r}")
    return _root(root) / oid[:2] / oid[2:]


def has(oid: str, *, root: str | Path | None = None) -> bool:
    """True iff the object is already in the store (dedup no-op check)."""
    return oid_to_path(oid, root=root).exists()


def get(oid: str, *, root: str | Path | None = None) -> Path:
    """Resolve an oid to its stored object path. Raises KeyError if absent."""
    p = oid_to_path(oid, root=root)
    if not p.exists():
        raise KeyError(f"oid not in store: {oid}")
    return p


# --------------------------------------------------------------------------- #
# transfer degradation chain
# --------------------------------------------------------------------------- #
def _reflink(src: str | Path, dst: str | Path) -> None:
    """CoW clone via FICLONE. Cleans up a partial dst on failure and re-raises
    OSError so ``transfer`` can degrade to the next method."""
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        try:
            fcntl.ioctl(fdst.fileno(), _FICLONE, fsrc.fileno())
        except OSError:
            fdst.close()
            try:
                os.unlink(dst)
            except OSError as cleanup_exc:
                # Best-effort removal of the empty dst opened above; a failure
                # here is non-fatal (the reflink OSError below is the real one),
                # but must be recorded rather than silently swallowed.
                logger.debug("reflink dst cleanup failed for %s: %s", dst, cleanup_exc)
            raise


def _hardlink(src: str | Path, dst: str | Path) -> None:
    os.link(src, dst)


def _copy(src: str | Path, dst: str | Path) -> None:
    shutil.copyfile(src, dst)


_METHODS = {"reflink": _reflink, "hardlink": _hardlink, "copy": _copy}


def transfer(
    src: str | Path,
    dst: str | Path,
    links: tuple[str, ...] = DEFAULT_LINKS,
) -> str:
    """Materialise ``src`` at ``dst`` trying each method in ``links`` in order,
    degrading on OSError (EXDEV cross-device, ENOTSUP/EOPNOTSUPP no reflink,
    EPERM, ...). Returns the method that succeeded. Raises the last OSError if
    every method fails. ``dst`` must not pre-exist (caller guarantees)."""
    if not links:
        raise ValueError("links chain is empty")
    last: OSError | None = None
    for method in links:
        fn = _METHODS.get(method)
        if fn is None:
            raise ValueError(f"unknown link method: {method!r}")
        try:
            fn(src, dst)
            return method
        except OSError as exc:
            # Legitimate degradation (INDEX §1): this link method is unavailable
            # on this filesystem (EXDEV / ENOTSUP / EPERM / ...) — record the
            # errno and fall through to the next method, never silently.
            logger.debug(
                "transfer method %r failed (errno=%s: %s); degrading",
                method,
                exc.errno,
                exc,
            )
            last = exc
            # partial dst from a failed non-reflink attempt (hardlink/copy leave
            # nothing, but be defensive) — remove before the next try.
            if os.path.lexists(dst):
                try:
                    os.unlink(dst)
                except OSError as cleanup_exc:
                    logger.debug("dst cleanup failed for %s: %s", dst, cleanup_exc)
            continue
    assert last is not None
    raise last


# --------------------------------------------------------------------------- #
# put / link_into
# --------------------------------------------------------------------------- #
def put(
    path: str | Path,
    *,
    root: str | Path | None = None,
    links: tuple[str, ...] = DEFAULT_LINKS,
) -> str:
    """Import a file into the store; return its oid.

    Idempotent dedup: if the oid already exists the call is a no-op (DVC
    ``check_exists``) — same bytes are stored exactly once. On first import the
    bytes are transferred through the degradation chain into a temp sibling,
    stamped 0o444, then atomically ``os.replace``-d into the shard path.

    Note: with the default chain, a filesystem without reflink support imports
    via hardlink, which makes ``path`` share the object's inode and therefore
    become 0o444 too — deliberate (write-once artifacts) and exactly the
    behaviour ``dedupe_run`` wants."""
    oid = oid_of(path)
    dst = oid_to_path(oid, root=root)
    if dst.exists():
        return oid  # already stored — dedup no-op
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / (dst.name + f".tmp-{os.getpid()}-{id(path) & 0xFFFF:x}")
    if os.path.lexists(tmp):
        os.unlink(tmp)
    transfer(path, tmp, links)
    try:
        os.chmod(tmp, OBJECT_MODE)
    except OSError as exc:
        # Degrade, don't fail: the object is stored and correct even if the
        # read-only bit could not be set (e.g. a filesystem that ignores chmod).
        # Warn, because losing 0o444 weakens the tamper backstop (INDEX §1).
        logger.warning("could not set 0o444 on stored object %s: %s", tmp, exc)
    # A concurrent put may have landed the same oid first; os.replace onto an
    # existing (read-only) target is fine — rename ignores target perms.
    os.replace(tmp, dst)
    return oid


def link_into(
    oid: str,
    dest: str | Path,
    *,
    root: str | Path | None = None,
    links: tuple[str, ...] = DEFAULT_LINKS,
) -> str:
    """Materialise stored object ``oid`` at ``dest`` (degradation chain).
    Overwrites ``dest`` atomically. Returns the method used. With hardlink the
    0o444 object bit propagates to ``dest`` (shared inode)."""
    src = get(oid, root=root)
    dest = Path(dest)
    # Already a link to this object? os.replace between two hardlinks of the same
    # inode is a POSIX no-op that would leave the temp behind — short-circuit.
    if dest.exists() and _same_inode(dest, src):
        return "already"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + f".calink-{os.getpid()}")
    if os.path.lexists(tmp):
        os.unlink(tmp)
    method = transfer(src, tmp, links)
    os.replace(tmp, dest)
    return method


# --------------------------------------------------------------------------- #
# gc — full set minus reachable set (INDEX §4)
# --------------------------------------------------------------------------- #
@dataclass
class GcReport:
    """Outcome of a gc pass. ``dry_run`` True => nothing was deleted."""

    dry_run: bool
    kept_oids: int
    swept_oids: list[str] = field(default_factory=list)
    swept_bytes: int = 0
    scanned_oids: int = 0


def iter_oids(*, root: str | Path | None = None):
    """Yield every oid currently in the store (walk the two-level shards)."""
    base = _root(root)
    if not base.exists():
        return
    for shard in sorted(base.iterdir()):
        if not shard.is_dir() or len(shard.name) != 2:
            continue
        for obj in sorted(shard.iterdir()):
            if obj.is_file() and not obj.name.startswith("."):
                yield shard.name + obj.name


def gc(
    keep_oids,
    *,
    root: str | Path | None = None,
    dry_run: bool = True,
) -> GcReport:
    """Reclaim objects NOT in the keep set: ``sweep = all - reachable`` (DVC
    ``hashfile/gc``).

    Two hard safety gates (INDEX §4/§125, DVC ``_validate_args`` + ``dry=True``):
      1. ``keep_oids`` MUST be provided (an explicit reachable set). Passing
         ``None`` raises — "no keep set" must never mean "delete everything".
      2. ``dry_run`` defaults True: the first call only reports what *would* be
         swept. A caller must opt into deletion with ``dry_run=False``.

    Deleting an object only breaks that object's file entry; any workdir hardlink
    to it keeps the inode alive until it too is removed — the content store makes
    gc reference-count-safe (INDEX §126)."""
    if keep_oids is None:
        raise ValueError(
            "gc refuses to run without an explicit keep set: pass keep_oids "
            "(possibly empty set()) to confirm the reachable set"
        )
    keep = set(keep_oids)
    rep = GcReport(dry_run=dry_run, kept_oids=len(keep))
    for oid in iter_oids(root=root):
        rep.scanned_oids += 1
        if oid in keep:
            continue
        p = oid_to_path(oid, root=root)
        try:
            size = p.stat().st_size
        except OSError as exc:
            # stat raced with removal — count it as swept but with unknown size.
            logger.debug("gc could not stat %s: %s", p, exc)
            size = 0
        rep.swept_oids.append(oid)
        rep.swept_bytes += size
        if not dry_run:
            try:
                os.unlink(p)
            except OSError as exc:
                # Already gone (concurrent gc) or undeletable — report, don't
                # abort the sweep over the remaining objects.
                logger.warning("gc could not unlink %s: %s", p, exc)
    return rep


# --------------------------------------------------------------------------- #
# opt-in wiring: explicit post-run de-duplication (INDEX §1/§3)
# --------------------------------------------------------------------------- #
#: Artifact filenames a run's ``_dry_jobs`` workdirs may contribute to the store.
#: ``result.json`` is the OS-visible measurement + its raw sidecar reference.
DEDUPE_ARTIFACTS = ("result.json",)


@dataclass
class DedupeFileReceipt:
    path: str
    oid: str
    bytes: int
    action: str  # "would-link" | "linked" | "already-linked" | "skipped"


@dataclass
class DedupeReport:
    run_dir: str
    dry_run: bool
    receipts: list[DedupeFileReceipt] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.receipts)

    @property
    def distinct_oids(self) -> int:
        return len({r.oid for r in self.receipts if r.oid})

    @property
    def stored_bytes(self) -> int:
        seen: set[str] = set()
        total = 0
        for r in self.receipts:
            if r.oid and r.oid not in seen:
                seen.add(r.oid)
                total += r.bytes
        return total

    @property
    def scanned_bytes(self) -> int:
        return sum(r.bytes for r in self.receipts)

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes freed by collapsing duplicates = scanned - one-copy-per-oid."""
        return self.scanned_bytes - self.stored_bytes


def _same_inode(a: str | Path, b: str | Path) -> bool:
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return sa.st_ino == sb.st_ino and sa.st_dev == sb.st_dev


def dedupe_run(
    run_dir: str | Path,
    *,
    root: str | Path | None = None,
    dry_run: bool = True,
    artifacts: tuple[str, ...] = DEDUPE_ARTIFACTS,
    links: tuple[str, ...] = DEFAULT_LINKS,
) -> DedupeReport:
    """Explicit post-run de-duplication of a completed run's ``_dry_jobs``
    artifacts (INDEX §1/§3): put each artifact into the store and replace the
    workdir file with a hardlink to the stored object, so identical bytes across
    jobs share one inode. This is an *explicit operation*, never loop magic —
    the decision face never calls it (decision/execution separation).

    Byte-exact and lossless: the workdir file's bytes are unchanged (a hardlink
    to the same-bytes object). ``dry_run`` default reports per-file receipts and
    reclaimable bytes without touching anything. Returns a per-file receipt log."""
    run_dir = Path(run_dir)
    jobs_root = run_dir / "_dry_jobs"
    rep = DedupeReport(run_dir=str(run_dir), dry_run=dry_run)
    if not jobs_root.exists():
        return rep

    for job in sorted(p for p in jobs_root.iterdir() if p.is_dir()):
        for name in artifacts:
            f = job / name
            if not f.is_file():
                continue
            size = f.stat().st_size
            oid = oid_of(f)
            already = has(oid, root=root) and _same_inode(
                f, oid_to_path(oid, root=root)
            )
            if dry_run:
                action = "would-link"
            elif already:
                action = "already-linked"  # idempotent: re-running is a no-op
            else:
                put(f, root=root, links=links)
                link_into(oid, f, root=root, links=("hardlink", "copy"))
                action = "linked"
            rep.receipts.append(
                DedupeFileReceipt(path=str(f), oid=oid, bytes=size, action=action)
            )
    return rep
