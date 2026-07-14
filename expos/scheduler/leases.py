"""Daemonless filesystem leases (coordination plane, NOT kernel truth).

A lease is a mutual-exclusion token over a ResourceObject (an instrument or a
compute slot). It is a single JSON file created with ``O_CREAT | O_EXCL`` so
that, among any number of concurrent contenders, exactly one wins the create
race — this is the anti-double-start primitive (RESEARCH_OS_VNEXT §8, R4-E
structural fix: sharded workers take a lease before starting).

Discipline borrowed from ``kernel/store.py`` writer.lock stale handling: a
lease held by a dead process (pid no longer alive) or one whose TTL has
elapsed is *stale* and may be reclaimed by the next contender. Every
reclamation is logged loudly (``logging.warning``) — never silent.

Federation invariant: leases live under ``<root>/_scheduler/leases/`` and are
pure coordination state. They are reconstructible from nothing and are
**never** written into any run's ``events.jsonl`` — truth lives only in run
logs, leases are disposable index/coordination state.

There is no background thread: expiry is reaped by explicit ``sweep()`` or
lazily during the next ``acquire()`` of the same resource.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger("expos.scheduler.leases")

# Bounded reclaim/create retries so a pathological reclaim storm cannot spin
# forever; on exhaustion acquire() reports contention rather than looping.
_MAX_ACQUIRE_ATTEMPTS = 8

_VALID_KINDS = ("instrument", "compute")


class LeaseError(Exception):
    """Raised on lease operations that cannot honor their contract loudly
    (e.g. renewing a lease the caller no longer holds)."""


@dataclass(frozen=True)
class ResourceObject:
    """Minimal leasable resource. M16 does not model capacity > 1."""

    resource_id: str
    kind: str  # one of _VALID_KINDS
    capacity: int = 1

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"ResourceObject.kind must be one of {_VALID_KINDS}, got {self.kind!r}"
            )
        if self.capacity != 1:
            # M16 scope: single-holder resources only. Fail loud rather than
            # silently pretend to support concurrency we have not built.
            raise ValueError(
                f"ResourceObject.capacity>1 is out of M16 scope (got {self.capacity})"
            )
        _validate_resource_id(self.resource_id)


@dataclass(frozen=True)
class Lease:
    """A granted lease. Carries exactly the on-disk payload plus the resource
    id so the manager can locate and verify ownership on release/renew."""

    resource_id: str
    holder_pid: int
    holder_tag: str
    acquired_utc: str  # ISO-8601 UTC
    ttl_s: float


def _validate_resource_id(resource_id: str) -> None:
    if not resource_id:
        raise ValueError("resource_id must be non-empty")
    if os.sep in resource_id or (os.altsep and os.altsep in resource_id):
        raise ValueError(
            f"resource_id must not contain path separators: {resource_id!r}"
        )
    if resource_id in (".", ".."):
        raise ValueError(f"resource_id must not be a path component: {resource_id!r}")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _pid_alive(pid: int) -> bool:
    """True iff a process with ``pid`` currently exists.

    ``os.kill(pid, 0)`` sends no signal but performs the permission/existence
    probe: ProcessLookupError => gone; PermissionError => exists but owned by
    another user (still alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LeaseManager:
    """Filesystem lease manager rooted at ``<root>/_scheduler/leases/``."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.leases_dir = self.root / "_scheduler" / "leases"
        self.leases_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _lease_path(self, resource_id: str) -> Path:
        _validate_resource_id(resource_id)
        return self.leases_dir / f"{resource_id}.lease"

    # ------------------------------------------------------------------ read
    def _read_lease(self, path: Path) -> dict | None:
        """Return the parsed lease payload, or None if the file is absent,
        unreadable, or corrupt (a corrupt lease is treated as reclaimable)."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            _LOG.warning("lease file %s unreadable (%s) — treating as stale", path, exc)
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            _LOG.warning("lease file %s is corrupt JSON (%s) — treating as stale",
                         path, exc)
            return None
        if not isinstance(data, dict):
            _LOG.warning("lease file %s payload is not an object — treating as stale",
                         path)
            return None
        return data

    def _is_stale(self, data: dict) -> bool:
        pid = data.get("holder_pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            return True
        return self._is_expired(data)

    def _is_expired(self, data: dict) -> bool:
        acquired = data.get("acquired_utc")
        ttl = data.get("ttl_s")
        if not isinstance(acquired, str) or not isinstance(ttl, (int, float)):
            return True  # malformed timing => reclaimable
        try:
            acquired_dt = datetime.fromisoformat(acquired)
        except ValueError:
            return True
        age_s = (_now_utc() - acquired_dt).total_seconds()
        return age_s > float(ttl)

    def _unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            _LOG.debug("lease %s already removed by a concurrent reclaimer", path)

    # ------------------------------------------------------------------ acquire
    def acquire(self, resource_id: str, ttl_s: float, tag: str) -> Lease | None:
        """Atomically acquire the lease for ``resource_id``.

        Returns a Lease on success, or None if the resource is held by a live,
        unexpired holder. Stale leases (dead holder pid or elapsed TTL) are
        reclaimed (logged) and re-contended for. Uses ``O_CREAT | O_EXCL`` so
        exactly one of N concurrent creators wins — removing that flag would
        let multiple contenders "succeed" simultaneously (see the kill test)."""
        path = self._lease_path(resource_id)
        acquired_utc = _now_utc().isoformat()
        payload = {
            "holder_pid": os.getpid(),
            "holder_tag": tag,
            "acquired_utc": acquired_utc,
            "ttl_s": float(ttl_s),
        }
        payload_bytes = json.dumps(payload).encode("utf-8")

        # Atomic publish (letter 047 TOCTOU fix): the payload is materialized in
        # a unique tmp file FIRST, then published via os.link(tmp, path) -- link
        # fails atomically with EEXIST on an existing target, and at the instant
        # of publication the payload is already complete. The former
        # create-then-write order left a milliseconds-wide empty-file window in
        # which a racing acquirer read "corrupt JSON", judged the lease stale,
        # reclaimed it and produced a second winner (16-process cold-start storm
        # reproduced 1-3 winners). The window is now physically gone.
        tmp = path.parent / f".{path.name}.{os.getpid()}.{id(self):x}.tmp"
        tmp.write_bytes(payload_bytes)
        try:
            return self._contend(path, tmp, resource_id, payload)
        finally:
            # tmp already consumed/vanished is fine -- suppress is the explicit,
            # lint-clean way to say "this specific absence is expected".
            import contextlib
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()

    def _contend(self, path, tmp, resource_id, payload):
        for _ in range(_MAX_ACQUIRE_ATTEMPTS):
            try:
                os.link(str(tmp), str(path))
            except FileExistsError:
                existing = self._read_lease(path)
                if existing is None or self._is_stale(existing):
                    _LOG.warning(
                        "reclaiming stale lease on %s (prior holder pid=%s tag=%s)",
                        resource_id,
                        existing.get("holder_pid") if existing else None,
                        existing.get("holder_tag") if existing else None,
                    )
                    self._unlink(path)
                    continue  # re-contend for the freed slot
                return None  # held by a live, unexpired holder
            else:
                # link succeeded: the fully-written payload IS the published
                # lease -- nothing left to write, no partial-state window.
                return Lease(
                    resource_id=resource_id,
                    holder_pid=payload["holder_pid"],
                    holder_tag=payload["holder_tag"],
                    acquired_utc=payload["acquired_utc"],
                    ttl_s=float(payload["ttl_s"]),
                )
        _LOG.warning("acquire(%s) lost all %d reclaim races — reporting contention",
                     resource_id, _MAX_ACQUIRE_ATTEMPTS)
        return None

    # ------------------------------------------------------------------ release
    def release(self, lease: Lease) -> None:
        """Release a lease. Idempotent: a no-op if the on-disk lease is absent
        or is a *different* lease (already reclaimed and re-granted to someone
        else) — we must never delete a holder that supplanted us."""
        path = self._lease_path(lease.resource_id)
        existing = self._read_lease(path)
        if existing is None:
            return  # already gone — idempotent
        if (existing.get("holder_pid") == lease.holder_pid
                and existing.get("acquired_utc") == lease.acquired_utc):
            self._unlink(path)
        else:
            _LOG.debug("release(%s): on-disk lease differs from ours — leaving intact",
                       lease.resource_id)

    # ------------------------------------------------------------------ renew
    def renew(self, lease: Lease, ttl_s: float) -> Lease:
        """Extend a held lease with a fresh acquisition time and new TTL.

        Raises LeaseError if we no longer hold it (dead-mans-switch: renewing a
        stolen lease must fail loudly, not silently resurrect ownership)."""
        path = self._lease_path(lease.resource_id)
        existing = self._read_lease(path)
        if existing is None or not (
            existing.get("holder_pid") == lease.holder_pid
            and existing.get("acquired_utc") == lease.acquired_utc
        ):
            raise LeaseError(
                f"cannot renew lease on {lease.resource_id}: no longer held by us "
                f"(pid={lease.holder_pid}, acquired={lease.acquired_utc})"
            )
        acquired_utc = _now_utc().isoformat()
        payload = {
            "holder_pid": lease.holder_pid,
            "holder_tag": lease.holder_tag,
            "acquired_utc": acquired_utc,
            "ttl_s": float(ttl_s),
        }
        # Atomic replace via a pid-suffixed temp file (same discipline as the
        # store's atomic writes): write fully, then os.replace over the target.
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return Lease(
            resource_id=lease.resource_id,
            holder_pid=lease.holder_pid,
            holder_tag=lease.holder_tag,
            acquired_utc=acquired_utc,
            ttl_s=float(ttl_s),
        )

    # ------------------------------------------------------------------ sweep
    def sweep(self) -> list[str]:
        """Explicitly reap every stale lease. Returns the reclaimed resource
        ids. This is the only expiry mechanism besides lazy reclamation in
        acquire() — there is no background reaper thread."""
        reclaimed: list[str] = []
        for path in sorted(self.leases_dir.glob("*.lease")):
            data = self._read_lease(path)
            if data is None or self._is_stale(data):
                _LOG.warning("sweep reclaiming stale lease %s (holder pid=%s)",
                             path.name, data.get("holder_pid") if data else None)
                self._unlink(path)
                reclaimed.append(path.stem)
        return reclaimed
