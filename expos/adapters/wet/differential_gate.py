"""sim-real differential acceptance gate (M23 Phase 3, A domain).

**Ahead of precedent (must be recorded honestly).** An EXPLICIT sim-real numerical
differential gate exists in NONE of the three industry precedents surveyed: pyvisa-sim has
inheritance but no cross-backend differential; renode makes the tested binary identical but
has no numerical-envelope gate; PLR has inheritance-based equivalence but HWSEAM already
判缺 the differential gate. This module's EXISTENCE is M23 Phase 3's original contribution
-- it is "ahead of precedent", not a copy (INDEX_REF_F §Convergence(c) / F-6 lead item).

Gate semantics (INDEX_REF_P3): NOT "the two backends' outputs are bit-equal", but
**"the real result lies inside the simulator-DECLARED envelope"** (real ⊆ sim envelope) --
the confluence of renode's "same tested thing ⇒ equivalence" and HWSEAM's "shared
validation code ⇒ upper bound". The gate only does a CONTAINMENT test; it never recomputes
the envelope (the envelope is DECLARED data: :mod:`tolerances_vendor_placeholder`).

Six comparison facets (user-钦定):

  1. action-sequence identity   -- the two ledgers dispatch the SAME action_ids in order.
  2. labware / well identity     -- same source_well / destination_well per action.
  3. requested volume            -- same requested_volume_ul per action (the PLAN is one).
  4. device tolerance            -- real observed_volume within the DECLARED band of the
                                    requested volume (the only quantity allowed to differ).
  5. terminal state              -- same terminal transaction state per action.
  6. observation-channel schema  -- the real backend's declared channels are COMPATIBLE
                                    with the sim's (no undeclared field -> fail-closed).

Three acceptance modes (user-钦定):

  * EXACT              -- facets 1/2/3/5 (sequence, labware/well, requested, terminal
                          state) must match EXACTLY (semantic identity).
  * TOLERANCE-BOUNDED  -- facet 4: |observed - requested| within the volume-band envelope
                          (INDEX_REF_P3: percent bound OR uL floor, the WIDER wins;
                          systematic/accuracy channel per action -- the random/CV channel
                          needs N>=10 replicates and is reserved).
  * FAIL-CLOSED        -- a MISSING or EXTRA action is RED (a real backend doing more or
                          fewer actions than the sim declared is never "close enough").

Output is a machine-readable :class:`DiffReport` (pure function; both inputs are the two
sides' ``action_ledger.jsonl`` files + the declared observation schemas)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_TOLERANCE = Path(__file__).with_name("tolerances_vendor_placeholder.json")
_EPS = 1e-6


# --- declared tolerance envelope (DATA, never recomputed) -----------------------

@dataclass(frozen=True)
class ToleranceBand:
    max_ul: float
    max_systematic_pct: float
    max_cv_pct: float
    floor_systematic_ul: float
    floor_random_ul: float
    source: str


class ToleranceEnvelope:
    """The DECLARED volume-tolerance envelope, keyed by volume band (loaded from
    :mod:`tolerances_vendor_placeholder`). The gate only reads it -- never tightens or
    recomputes it (INDEX_REF_P3 'never tighter than vendor spec')."""

    def __init__(self, bands: list[ToleranceBand], meta: dict[str, Any]) -> None:
        self._bands = sorted(bands, key=lambda b: b.max_ul)
        self.meta = meta

    @classmethod
    def load(cls, path: str | Path = _DEFAULT_TOLERANCE) -> "ToleranceEnvelope":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        bands = [ToleranceBand(**b) for b in doc["bands"]]
        return cls(bands, doc.get("_meta", {}))

    def band_for(self, volume_ul: float) -> ToleranceBand | None:
        for b in self._bands:
            if volume_ul <= b.max_ul + _EPS:
                return b
        return None  # above the largest declared band -> no envelope -> fail-closed

    def systematic_allowance(self, requested_ul: float, *, channels: int = 1) -> float | None:
        """The absolute-uL systematic (accuracy) allowance for a requested volume: the
        WIDER of the percent-derived bound and the uL floor (dual representation), times
        the multi-channel doubling (ISO 8655). ``None`` if the volume is out of envelope."""
        band = self.band_for(requested_ul)
        if band is None:
            return None
        pct_ul = requested_ul * band.max_systematic_pct / 100.0
        allowance = max(pct_ul, band.floor_systematic_ul)
        if channels > 1:
            allowance *= 2.0  # multi-channel MPE doubling (INDEX_REF_P3 signal 1.3)
        return allowance


# --- machine-readable report ----------------------------------------------------

@dataclass(frozen=True)
class DiffFinding:
    """One red finding. ``facet`` names the comparison facet; ``kind`` the discriminator."""

    facet: str
    kind: str
    action_id: str | None
    detail: str
    severity: str = "red"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiffReport:
    passed: bool
    findings: list[DiffFinding] = field(default_factory=list)
    facet_status: dict[str, bool] = field(default_factory=dict)
    sim_sequence: list[str] = field(default_factory=list)
    real_sequence: list[str] = field(default_factory=list)
    tolerance_source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [f.as_dict() for f in self.findings],
            "facet_status": dict(self.facet_status),
            "sim_sequence": list(self.sim_sequence),
            "real_sequence": list(self.real_sequence),
            "tolerance_source": self.tolerance_source,
        }


# --- ledger loading (pure) ------------------------------------------------------

def load_final_records(path: str | Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Parse an ``action_ledger.jsonl`` into (first-seen action_id order, final snapshot
    per action_id). The latest line per action_id is its authoritative final record (each
    line carries a full snapshot). Pure -- reads the file, no side effects."""
    order: list[str] = []
    final: dict[str, dict[str, Any]] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        rec = json.loads(raw)
        aid = rec.get("action_id")
        if aid is None:
            continue
        if aid not in final:
            order.append(aid)
        final[aid] = rec
    return order, final


# --- the gate (pure function) ---------------------------------------------------

def run_differential_gate(
    sim_ledger_path: str | Path,
    real_ledger_path: str | Path,
    *,
    tolerance: ToleranceEnvelope | str | Path | None = None,
    sim_obs_schema: dict[str, str] | None = None,
    real_obs_schema: dict[str, str] | None = None,
    channels: int = 1,
) -> DiffReport:
    """Compare a sim-side and a real-side ledger across the six facets. Pure function:
    inputs are the two ledger FILES + the declared observation schemas + the declared
    tolerance envelope. Returns a machine-readable :class:`DiffReport`.

    ``passed`` is True iff every facet holds. Any missing/extra action, any labware/well
    or requested-volume or terminal-state divergence, any observed volume outside the
    declared band, and any undeclared observation channel is a RED finding (fail-closed).
    """
    if isinstance(tolerance, ToleranceEnvelope):
        env = tolerance
    else:
        env = ToleranceEnvelope.load(tolerance) if tolerance is not None \
            else ToleranceEnvelope.load()

    sim_order, sim = load_final_records(sim_ledger_path)
    real_order, real = load_final_records(real_ledger_path)

    findings: list[DiffFinding] = []
    facet_status: dict[str, bool] = {
        "action_sequence_identity": True, "labware_well_identity": True,
        "requested_volume": True, "device_tolerance": True,
        "terminal_state": True, "observation_schema": True,
    }

    def fail(facet: str, finding: DiffFinding) -> None:
        facet_status[facet] = False
        findings.append(finding)

    # -- facet 1: action-sequence identity (fail-closed on missing/extra) -----
    sim_ids, real_ids = set(sim), set(real)
    for aid in sim_order:
        if aid not in real_ids:
            fail("action_sequence_identity", DiffFinding(
                "action_sequence_identity", "action_missing", aid,
                "action present in sim but MISSING from real (fail-closed)"))
    for aid in real_order:
        if aid not in sim_ids:
            fail("action_sequence_identity", DiffFinding(
                "action_sequence_identity", "action_extra", aid,
                "action present in real but EXTRA vs sim (fail-closed)"))
    if sim_order != real_order and sim_ids == real_ids:
        fail("action_sequence_identity", DiffFinding(
            "action_sequence_identity", "order_divergence", None,
            f"same actions, different dispatch order: sim={sim_order} real={real_order}"))

    # -- facets 2/3/4/5: per shared action -------------------------------------
    for aid in sim_order:
        if aid not in real:
            continue
        s, r = sim[aid], real[aid]

        # facet 2: labware / well identity
        if s.get("source_well") != r.get("source_well") or \
                s.get("destination_well") != r.get("destination_well"):
            fail("labware_well_identity", DiffFinding(
                "labware_well_identity", "well_mismatch", aid,
                f"sim {s.get('source_well')}->{s.get('destination_well')} != "
                f"real {r.get('source_well')}->{r.get('destination_well')}"))

        # facet 3: requested volume (exact)
        if abs(float(s.get("requested_volume_ul", 0.0)) -
               float(r.get("requested_volume_ul", 0.0))) > _EPS:
            fail("requested_volume", DiffFinding(
                "requested_volume", "requested_volume_mismatch", aid,
                f"sim requested {s.get('requested_volume_ul')} != "
                f"real requested {r.get('requested_volume_ul')}"))

        # facet 5: terminal state (exact)
        if s.get("state") != r.get("state"):
            fail("terminal_state", DiffFinding(
                "terminal_state", "terminal_state_mismatch", aid,
                f"sim state {s.get('state')} != real state {r.get('state')}"))

        # facet 4: device tolerance (real observed within the DECLARED band)
        r_obs = r.get("observed_volume_ul")
        req = float(r.get("requested_volume_ul", s.get("requested_volume_ul", 0.0)))
        if r_obs is not None:
            allowance = env.systematic_allowance(req, channels=channels)
            if allowance is None:
                fail("device_tolerance", DiffFinding(
                    "device_tolerance", "volume_out_of_envelope", aid,
                    f"requested {req} uL is above the largest declared tolerance band "
                    "(fail-closed: real produced a quantity sim never declared)"))
            else:
                deviation = abs(float(r_obs) - req)
                if deviation > allowance + _EPS:
                    fail("device_tolerance", DiffFinding(
                        "device_tolerance", "volume_tolerance_exceeded", aid,
                        f"observed {r_obs} uL deviates {deviation:.4g} uL from requested "
                        f"{req} uL > allowance {allowance:.4g} uL (declared envelope)"))

    # -- facet 6: observation-channel schema compatibility ---------------------
    if sim_obs_schema is not None and real_obs_schema is not None:
        for chan in real_obs_schema:
            if chan not in sim_obs_schema:
                fail("observation_schema", DiffFinding(
                    "observation_schema", "undeclared_channel", None,
                    f"real declares observation channel {chan!r} the sim never declared "
                    "(fail-closed)"))
        for chan, typ in sim_obs_schema.items():
            if chan in real_obs_schema and real_obs_schema[chan] != typ:
                fail("observation_schema", DiffFinding(
                    "observation_schema", "channel_type_mismatch", None,
                    f"channel {chan!r}: sim type {typ!r} != real type "
                    f"{real_obs_schema[chan]!r}"))

    return DiffReport(
        passed=all(facet_status.values()), findings=findings,
        facet_status=facet_status, sim_sequence=sim_order, real_sequence=real_order,
        tolerance_source=str(env.meta.get("source", "")))
