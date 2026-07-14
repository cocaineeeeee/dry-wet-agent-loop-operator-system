"""expos.scheduler — minimal coordination-plane primitives for M16 (W1).

Two modules, both living *outside* the kernel on purpose: leases and job
handles are coordination-plane artifacts, not sourced truth. Per
RESEARCH_OS_VNEXT §8 (federation): the coordination plane stores only
*indices and leases*; truth always lives in each run's append-only event
log. A lease therefore NEVER enters any run's events.jsonl — it is filesystem
state under ``<root>/_scheduler/`` reconstructible/discardable at will.

- ``leases``: LeaseManager — daemonless filesystem leases with TTL + stale
  reclamation (the anti-double-start primitive, R4-E structural fix).
- ``jobs``: JobHandle abstraction over three interchangeable backends
  (Subprocess / Ssh / Sbatch); backend choice is explicit, never auto-probed.
"""

from __future__ import annotations

from expos.scheduler.jobs import (
    JobBackend,
    JobHandle,
    JobState,
    JobStatus,
    SbatchBackend,
    SshBackend,
    SubprocessBackend,
)
from expos.scheduler.leases import (
    Lease,
    LeaseError,
    LeaseManager,
    ResourceObject,
)

__all__ = [
    "Lease",
    "LeaseError",
    "LeaseManager",
    "ResourceObject",
    "JobBackend",
    "JobHandle",
    "JobState",
    "JobStatus",
    "SubprocessBackend",
    "SshBackend",
    "SbatchBackend",
]
