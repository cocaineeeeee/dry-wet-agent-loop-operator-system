"""Protocol spec + chain-of-custody model (stdlib only, no opentrons / expos dep).

The ``solvent_screen`` wet leg prepares a *liquid gradient* on a 96-well plate: a
low-polarity stock (A) and a high-polarity stock (B) are mixed per well to hit a
target solvent polarity, keeping the total volume constant. The plate is then read
by the plate-reader simulator, whose hidden truth surface responds to polarity.

Chain of custody (G3, four segments, all traceable from a single ``sample_id``).
Each transfer stamps WHO performed it (``actor``, self-reported) and WHEN
(``at_utc``, ISO-8601) into an append-only ``custody_log`` -- the senaite-inspired
who/when audit dimension:

    1. protocol   -- SolventSample: sample_id born here (actor: protocol_compiler)
    2. deck slot  -- WellPlan: sample_id -> plate slot + well_id (protocol_compiler)
    3. measurement-- reader echoes sample_id; WetDriver records it (actor: wet_driver)
    4. raw record -- WetDriver stamps sample_id onto each raw reading (wet_driver)

A reading whose sample_id was never issued by the protocol (forged) or is missing
(null), OR whose custody record carries no actor (unattested/forged provenance),
MUST be rejected -- enforced in driver.WetDriver._ingest_reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from .labware import load_labware


# 96-well plate row/col helpers (data-driven from the externalised labware) -----
#
# The plate geometry is no longer an 8x12 assumption baked into code: it is loaded
# from ``labware/plate96.json`` (Opentrons paradigm, Q4 of INDEX_M16). The default
# definition encodes the exact column-major A1,B1,..,H1,A2,.. order the previous
# hard-coded helpers produced, so every downstream output is bit-for-bit unchanged.


def all_wells() -> list[str]:
    """Canonical A1..H12 well order, sourced from the external labware ``ordering``."""
    return load_labware().all_wells()


def well_rowcol(well_id: str) -> tuple[int, int]:
    """('B3') -> (row_index=1, col_index=2). Raises on malformed ids."""
    return load_labware().rowcol(well_id)


def is_edge(well_id: str) -> bool:
    return load_labware().is_edge(well_id)


# Protocol spec -----------------------------------------------------------------

@dataclass(frozen=True)
class Stock:
    """A stock solvent living in a reservoir well, with a known polarity."""

    name: str
    reservoir_well: str  # e.g. "A1" on the reservoir labware
    polarity: float      # intrinsic polarity of this stock (0..1)


@dataclass(frozen=True)
class SolventSample:
    """One candidate solvent to prepare -- custody segment 1 (protocol).

    ``replicate`` is the M17 K-F multi-replicate-substrate hook. It is None for a
    single-well sample (the pre-K-F default -- ``sample_id`` is then bit-for-bit
    unchanged, the hard regression gate) and an integer 0..n-1 when a candidate is
    laid out across n replicate wells. Replicates of one candidate share
    ``cand_id`` (so the round aggregator groups them into the SAME contrast arm)
    but get DISTINCT ``sample_id``s (so each replicate owns an independent
    four-segment chain of custody)."""

    cand_id: str
    target_polarity: float          # design variable to realise by mixing A+B
    is_control: bool = False
    control_id: str | None = None
    replicate: int | None = None

    @property
    def sample_id(self) -> str:
        """Deterministic, stable custody key. Born at the protocol layer. A
        replicated sample carries an ``-r{k}`` suffix so its custody chain is
        independent of its siblings; a non-replicated sample (``replicate is
        None``) keeps the exact pre-K-F id (regression-frozen)."""
        base = self.control_id if self.is_control else self.cand_id
        kind = "CTL" if self.is_control else "CND"
        suffix = "" if self.replicate is None else f"-r{self.replicate}"
        return f"SMP-{kind}-{base}{suffix}"


@dataclass
class ProtocolSpec:
    """Declarative liquid-gradient protocol for the solvent_screen domain.

    Labware / pipette names are real opentrons load-names so the official
    simulator can gate-keep them. ``compile_and_validate`` turns this into an
    :class:`~wet.ot_protocol.OTProtocol`.
    """

    samples: list[SolventSample]
    stock_low: Stock = field(
        default_factory=lambda: Stock("stockA_lowpol", "A1", 0.10)
    )
    stock_high: Stock = field(
        default_factory=lambda: Stock("stockB_highpol", "A2", 0.90)
    )
    total_volume_ul: float = 150.0
    plate_labware: str = "corning_96_wellplate_360ul_flat"
    reservoir_labware: str = "nest_12_reservoir_15ml"
    tiprack_labware: str = "opentrons_96_tiprack_300ul"
    pipette: str = "p300_single_gen2"
    mount: str = "right"
    plate_slot: int = 1
    reservoir_slot: int = 2
    tiprack_slot: int = 3
    #: pipette usable range (µL); p300 single-channel gen2 = 20..300
    pipette_min_ul: float = 20.0
    pipette_max_ul: float = 300.0
    #: well working capacity (µL); sourced from the external labware definition
    #: (``parameters.wellCapacityUl``); corning flat 96 = 360 nominal, keep headroom
    well_capacity_ul: float = field(
        default_factory=lambda: load_labware().well_capacity_ul
    )

    def well_for(self, index: int) -> str:
        wells = all_wells()
        if index >= len(wells):
            raise ValueError(
                f"sample index {index} exceeds 96-well plate capacity"
            )
        return wells[index]

    def mix_volumes(self, target_polarity: float) -> tuple[float, float]:
        """Return (vol_low, vol_high) µL to realise ``target_polarity`` by mixing.

        frac_high = (p - pA) / (pB - pA); volumes sum to ``total_volume_ul``.
        Volume-boundary validation happens in compile_and_validate, not here --
        this is pure accounting.
        """
        pa, pb = self.stock_low.polarity, self.stock_high.polarity
        span = pb - pa
        frac_high = (target_polarity - pa) / span
        vol_high = frac_high * self.total_volume_ul
        vol_low = self.total_volume_ul - vol_high
        return vol_low, vol_high


# Chain of custody --------------------------------------------------------------

@dataclass
class CustodyRecord:
    """The four-segment trace of one sample, assembled as the run proceeds."""

    sample_id: str
    cand_id: str | None = None
    control_id: str | None = None
    # segment 2 -- deck position
    plate_slot: int | None = None
    well_id: str | None = None
    target_polarity: float | None = None
    vol_low_ul: float | None = None
    vol_high_ul: float | None = None
    # segment 3 -- measurement (filled by driver from reader reply)
    measured: bool = False
    reading_value: float | None = None
    reading_seq: int | None = None
    # segment 4 -- raw record id (filled by driver on ingest)
    raw_record_id: str | None = None
    # --- audit dims (senaite-inspired who/when trail; additive to the segments) ---
    # AUDIT dimensions, NOT a security claim: ``actor`` is SELF-REPORTED by the
    # construct that performs each custody transfer. Within this single process
    # there is no adversarial semantics -- a construct could name any actor; the
    # value documents provenance, it does not authenticate it. (senaite.core
    # snapshot.py:275-341 append-only actor/timestamp/action audit, minus the
    # cross-user security_seal a single-process OS cannot honestly claim.)
    actor: str = ""          # latest custodian: protocol_compiler|wet_driver|reader|bridge
    at_utc: str = ""         # ISO-8601 UTC timestamp of the latest custody transfer
    #: append-only per-segment trail: one {action, actor, at_utc} snapshot per transfer.
    custody_log: list[dict[str, str]] = field(default_factory=list)

    def stamp(self, action: str, actor: str) -> None:
        """Record WHO took custody at ``action`` and WHEN (append-only).

        Sets the ``actor``/``at_utc`` scalars to the LATEST transfer and appends an
        immutable snapshot to ``custody_log`` (never rewrites a prior entry). The
        actor is self-reported by the caller -- see the class docstring; no
        cross-construct authentication is implied.
        """
        at = datetime.now(timezone.utc).isoformat()
        self.actor = actor
        self.at_utc = at
        self.custody_log.append({"action": action, "actor": actor, "at_utc": at})

    def attested(self) -> bool:
        """True iff a construct has recorded custody (an actor was stamped).

        A record that never went through the audited :meth:`CustodyChain.issue`
        path carries no actor -- treated as forged/unaudited provenance on ingest.
        """
        return bool(self.actor)

    def segments_complete(self) -> dict[str, bool]:
        return {
            "protocol": self.cand_id is not None or self.control_id is not None,
            "deck": self.well_id is not None and self.plate_slot is not None,
            "measurement": self.measured,
            "raw": self.raw_record_id is not None,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CustodyChain:
    """A queryable custody ledger keyed by sample_id (one command -> full trace)."""

    def __init__(self) -> None:
        self._records: dict[str, CustodyRecord] = {}

    def issue(
        self, sample: SolventSample, *, actor: str = "protocol_compiler"
    ) -> CustodyRecord:
        """Segment 1: mint a custody record when the protocol issues a sample."""
        rec = CustodyRecord(
            sample_id=sample.sample_id,
            cand_id=None if sample.is_control else sample.cand_id,
            control_id=sample.control_id if sample.is_control else None,
        )
        rec.stamp("protocol", actor)
        self._records[sample.sample_id] = rec
        return rec

    def bind_deck(
        self, sample_id: str, plate_slot: int, well_id: str,
        target_polarity: float, vol_low: float, vol_high: float,
        *, actor: str = "protocol_compiler",
    ) -> None:
        rec = self._require(sample_id)
        rec.plate_slot = plate_slot
        rec.well_id = well_id
        rec.target_polarity = target_polarity
        rec.vol_low_ul = vol_low
        rec.vol_high_ul = vol_high
        rec.stamp("deck", actor)

    def record_measurement(
        self, sample_id: str, value: float | None, seq: int,
        *, actor: str = "wet_driver",
    ) -> None:
        rec = self._require(sample_id)
        rec.measured = True
        rec.reading_value = value
        rec.reading_seq = seq
        rec.stamp("measurement", actor)

    def record_raw(
        self, sample_id: str, raw_record_id: str, *, actor: str = "wet_driver"
    ) -> None:
        rec = self._require(sample_id)
        rec.raw_record_id = raw_record_id
        rec.stamp("raw", actor)

    def known(self, sample_id: str) -> bool:
        return sample_id in self._records

    def attested(self, sample_id: str) -> bool:
        """True iff ``sample_id`` is known AND a construct has stamped an actor.

        A record injected without going through :meth:`issue` carries no actor --
        unattested/forged provenance, rejected on ingest like an unknown id."""
        rec = self._records.get(sample_id)
        return rec is not None and rec.attested()

    def trace(self, sample_id: str) -> CustodyRecord:
        """Chain-of-custody query: return the full four-segment trace."""
        return self._require(sample_id)

    def all_records(self) -> list[CustodyRecord]:
        return list(self._records.values())

    def _require(self, sample_id: str) -> CustodyRecord:
        rec = self._records.get(sample_id)
        if rec is None:
            raise KeyError(
                f"unknown sample_id {sample_id!r} (never issued by protocol)"
            )
        return rec


def make_gradient_spec(
    n_samples: int = 8,
    n_controls: int = 2,
    p_lo: float = 0.30,
    p_hi: float = 0.75,
    total_volume_ul: float = 200.0,
) -> ProtocolSpec:
    """Build a default linear polarity-gradient spec for the solvent_screen domain.

    ``n_samples`` candidate solvents spanning [p_lo, p_hi] plus ``n_controls``
    reference wells fixed at mid polarity (custody + calibration sentinels).
    """
    samples: list[SolventSample] = []
    if n_samples == 1:
        polarities = [(p_lo + p_hi) / 2]
    else:
        step = (p_hi - p_lo) / (n_samples - 1)
        polarities = [p_lo + i * step for i in range(n_samples)]
    for i, p in enumerate(polarities):
        samples.append(SolventSample(cand_id=f"c{i:02d}", target_polarity=p))
    mid = (p_lo + p_hi) / 2
    for j in range(n_controls):
        samples.append(
            SolventSample(
                cand_id=f"ctl{j}", target_polarity=mid,
                is_control=True, control_id=f"ctl{j}",
            )
        )
    return ProtocolSpec(samples=samples, total_volume_ul=total_volume_ul)
