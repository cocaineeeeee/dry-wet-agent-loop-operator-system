"""Minimum Complete Loop (MCL) orchestrator — M16 W9 wiring.

``run_mcl_loop`` drives the two-legged Dry-Wet-Agent loop end to end for
``docs/M16_MIN_LOOP.md`` (the G1-G5 acceptance gates). It is a SEPARATE driver
from :func:`expos.loop.run_loop`: ``run_loop`` orchestrates a single execution
leg through one ``ExecutionAdapter``; the MCL is a *dual-leg* pipeline (a dry
PySCF screening leg feeding a Dry->Wet promotion gate feeding a wet
plate-reader leg) that never goes through ``build_adapter`` at all — the
``pyscf_dry`` adapter is async-job-shaped and the wet leg is an out-of-band
socket device, so both legs are driven through the W5 domain glue directly
(see /tmp/claude-1128/dimw5_handoff.md §1). What the MCL DOES reuse from
``run_loop`` is the run-level discipline: the single-writer ``writer.lock``,
the terminal-state event contract (``run_start`` .. ``run_stop{exit_status}``,
absence == crash), per-round checkpoints, and the ``derive_seed`` seed lineage.

Each round runs one pass of the pipeline (M16 §1):

  1. knowledge compile — :func:`expos.kernel.knowledge.compile_knowledge` over
     the (claims, hypotheses) substrate -> ``emit_knowledge_updated``.
  2. agent proposal — a DETERMINISTIC template agent reads the compiled
     KnowledgeView and emits an ordered candidate proposal (PRIOR_PROPOSAL
     decision). Determinism is the G1 substrate: freeze the knowledge and the
     proposal is bit-identical round to round; flip a referenced claim and the
     proposal ordering (hence the promoted set) changes predictably.
  3. dry leg — build a dry ``ExperimentObject`` (metric ``polarity_proxy``),
     run one out-of-process PySCF job per candidate under a ``compute`` lease,
     ingest + adjudicate through the SAME QC/trust path as the wet leg.
  4. promotion gate — :class:`EvidenceGatedPromotion` decides which converged,
     in-window candidates earn a wet well (conjunctive four-channel gate) and
     records WHY every denied candidate was denied -> ``emit_promotion_decision``.
     A zero-promotion round still emits the event (legal-quiet is loud).
  5. wet leg — the promoted candidates go through the W5 glue (compile ->
     validated OTProtocol -> WetDriver) under an ``instrument`` lease; wet
     observations (metric ``solvent_response``) ingest + adjudicate.
  6. round checkpoint.

After ``rounds`` passes the run stops with ``run_stop{exit_status="success"}``.

Determinism (G5): with ``noise_sd=0`` on the reader and a fixed knowledge
substrate, two runs with the same seed produce the same event *sequence* on the
load-bearing fields (kinds, knowledge fingerprints, promoted/denied cand_ids and
reasons, proposal ordering) — event ``ts``/``seq`` differ, decisions/fingerprints
do not.

Red lines honoured: knowledge is a COMPILED product (never hand-authored on the
consuming side); the agent has no adjudication power (QC/trust owns every
verdict); the promotion decision is recorded evidence, not an implicit edge; the
reader's hidden truth never touches the OS path (it is harvested off-band via
``sim_reader.harvest_truth`` and persisted opaquely via ``store.save_truth``).
"""

from __future__ import annotations

import functools
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from expos.adapters.domain_provider import (
    INPUT_KIND_MOLECULAR_GEOMETRY,
    INPUT_KIND_SEQUENCE_CONSTRUCT,
    INPUT_KIND_SEQUENCE_FEATURES,
    adapter_accepts_capability,
)
from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.catalysts import catalyst_params
from expos.adapters.dry.ingest import dry_raw_to_observations
from expos.adapters.dry.sequence_adapter import SequenceProxyAdapter
from expos.adapters.ingest import raw_to_observations
from expos.adapters.wet import sim_reader
from expos.adapters.wet.bio_readout import (
    baselines_from_controls,
    percent_of_control,
)
from expos.adapters.wet.sim_reader import DEFAULT_TRUTH_PROFILE
from expos.adapters.wet.screen import (
    DRY_METRIC,
    P_TARGET_HI,
    P_TARGET_LO,
    SOLVENT_POLARITY,
    compile_wet,
    layout_from_protocol,
    run_wet_leg,
)
from expos.adapters.wet.sim_reader import harvest_truth
# M23 Phase 4-B physical-dispatch wiring (session B). mcl consumes the ORCHESTRATION
# facade + the ledger value types; it imports NEITHER fake_physical NOR any concrete
# backend -- the SensedState backend arrives by injection (``physical_backend``), so
# "simulation is the upper bound" stays a code-sharing guarantee, not an mcl dependency
# (AST guard in tests/test_phase4_wiring.py pins the no-fake_physical-import invariant).
from expos.adapters.wet.action_ledger import (
    ActionLedger,
    PlannedAction,
    SensedState,
    VolumeLedger,
)
from expos.adapters.wet.orchestration import dispatch_round, resume_round
from expos.agent.backend_select import resolve_agent_backend
from expos.domain import (
    DomainConfig,
    check_unit_consistency,
    config_fingerprint,
    load_domain,
)
from expos.errors import ExposError
from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimRecord,
    EvidenceStrength,
    Ledger,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    apply_claim_deltas,
    emit_claim_decision,
    ledger_to_claim_dicts,
)
from expos.kernel.knowledge import (
    KnowledgeView,
    compile_knowledge,
    emit_knowledge_updated,
)
from expos.kernel.lifecycle import TrustPolicy
from expos.kernel.objects import (
    Actor,
    Budget,
    Candidate,
    Control,
    DecisionKind,
    DecisionRecord,
    DesignProvenance,
    ExecutionReq,
    ExperimentObject,
    HypothesisObject,
    HypothesisStatus,
    LayoutAssignment,
    Objective,
    TrustLevel,
    WellAssignment,
)
from expos.kernel.store import RunStore
from expos.loop import derive_seed
from expos.planner.certification import CertificationPolicy, NullCertification
from expos.planner.promotion import (
    DryCandidateView,
    EvidenceGatedPromotion,
    PromotionBudget,
    WetCostEstimate,
    emit_promotion_decision,
)
from expos.qc.checks import run_qc
from expos.qc.policy import QCPolicy
from expos.scheduler import LeaseManager, ResourceObject, SubprocessBackend

_log = logging.getLogger("expos.mcl")


class MCLError(ExposError):
    pass


class ForkedResumeError(MCLError):
    """Resume refused: the event log diverged from the checkpoint it was written against
    (Phase 4 item #1). The event at the checkpoint's ``last_event_seq`` is absent or no longer
    hashes to ``last_event_sha256`` — a rewritten/truncated/forked history. Refuse loudly
    rather than silently adopt a branch (litestream fork-detection precedent, INDEX_REF_X
    §Convergence c). user_facing=False: an integrity break, keep the traceback."""

    user_facing = False


class WetReplayError(MCLError):
    """Resume refused: round N's wet leg was already ISSUED but its persisted results are
    incomplete (Phase 4 item #1). Already-issued wet commands MUST NOT be replayed (blue_to_red
    092: the wet leg is a rewindable(False) segment, a persisted invariant not merely runtime
    protection) — and the logged results cannot be consumed because they are missing. Refuse
    loudly (a real lab cannot un-dispense; for the simulated leg this guards tamper/partial
    loss). user_facing=False: an integrity break, keep the traceback."""

    user_facing = False


class _SimulatedCrash(BaseException):
    """Crash-injection vehicle for the interruption matrix (tests/test_phase4_interruption.py).
    Raised ONLY by an injected ``interrupt_hook`` to simulate a HARD crash at a pinned killpoint:
    ``run_mcl_loop`` re-raises it WITHOUT emitting a terminal ``run_stop`` (absence == crash), so
    resume faces the exact torn state a ``kill -9`` would leave. A default run never raises this
    (no hook = no-op). It subclasses BaseException so a bare ``except Exception`` in the round
    body cannot swallow the injected crash."""


# ---------------------------------------------------------------- constants

# ============================================================================
# LEGACY-FALLBACK domain constants (M20 domain-swappability).
#
# These are the solvent_screen literals the loop used to hardcode. They are now
# consumed ONLY by ``_domain_bindings`` as the fallback for a domain that carries
# NO ``descriptors``/``acquisition`` block, so solvent_screen stays byte-identical.
# A domain yaml that declares descriptors + an acquisition block drives the loop
# from config instead (EXP011 spirit: the loop knows no domain literals).
# TODO(M20+ later batch): once every shipped domain yaml carries its own
# descriptors/acquisition, delete this block and the fallback branch.
# ============================================================================

#: The fixed candidate pool the deterministic template agent screens each round.
#: The KNOWLEDGE decides their ORDER (acquisition), not their membership — so a
#: frozen knowledge state yields a bit-identical proposal while a flipped claim
#: re-orders it (the G1 discriminator). Membership spans the mixable window
#: (ethanol/acetonitrile/acetone in [0.30, 0.75]) and one out-of-window solvent
#: (hexane) so the promotion gate exercises a real ``gate_window`` denial.
_CANDIDATE_POOL: tuple[str, ...] = ("ethanol", "acetonitrile", "acetone", "hexane")

#: Recorded (but truth-flat in M16) protocol conditions carried on every
#: candidate so the wet protocol has a full point to actuate/validate.
_FIXED_CONDITIONS: dict[str, float] = {
    "concentration": 5.0,
    "temperature": 25.0,
    "incubation_time": 30.0,
}

#: Dry ``polarity_proxy`` (dipole, Debye) QC range — the wet range lives on the
#: domain (``cfg.metric_range``), but the dry channel is a different metric so it
#: carries its own range (mirrors tests/test_w8_domain_e2e's dry QC range).
_DRY_METRIC_RANGE: tuple[float, float] = (0.0, 10.0)

#: QC range for the SEQUENCE dry leg's expression proxy (a small non-negative scalar,
#: floored at 0 by ``sequences.expression_features``). Capability-scoped, NOT a domain
#: literal; wide enough to admit the whole proxy band so a healthy proxy is TRUSTED.
_SEQ_DRY_METRIC_RANGE: tuple[float, float] = (0.0, 10.0)

#: The dry-leg ``adapter_capability`` values that route to the SYNCHRONOUS in-process
#: :class:`SequenceProxyAdapter` (as opposed to the async-job PySCF leg). These are
#: Contract-v3 ``input_kind`` capabilities, NOT domain names — the dry-leg dispatch keys
#: on capability only (scripts/expos_lint.py EXP-neutrality: mcl hardcodes no domain).
_SEQUENCE_CAPABILITIES: tuple[str, ...] = (
    INPUT_KIND_SEQUENCE_CONSTRUCT,
    INPUT_KIND_SEQUENCE_FEATURES,
)

#: Candidate ``params`` key carrying a construct's DESIGN lineage (M24 item #4). A NEW,
#: dedicated field — deliberately NOT ``Candidate.parent_obs_id`` (whose semantics are
#: replicate/observation provenance; overloading it double-pollutes the reverse ledger).
#: v1 is STORE-ONLY: the lineage is recorded on the candidate but does NOT drive the
#: proposal/acquisition (lineage-driven acquisition is a recorded deferred abstraction
#: gap, docs/M24 §debt). ``params`` is a kernel-open dict, so this is kernel-clean.
_LINEAGE_PARAMS_KEY = "design_lineage"

#: The Contract-v3 ``sequence_construct`` payload keys that carry construct lineage (the
#: rest of the payload — sequence/promoter/rbs/cds — is the dry-adapter input). Capability
#: payload-schema keys, not domain names.
_LINEAGE_PAYLOAD_KEYS: tuple[str, ...] = ("parent_construct", "sequence_version")

#: Domain-control ROLE -> kernel ``Control.kind`` (M24 item #2 / bio ruling ①, ZERO kernel
#: change). negative/positive are native kernel kinds; ``reference`` has no kernel kind, so
#: it rides ``kind=sentinel`` plus a ``params.semantic_role`` marker (see below). Keyed on
#: the neutral role vocabulary, not on any domain.
_CONTROL_ROLE_TO_KIND: dict[str, str] = {
    "negative": "negative",
    "positive": "positive",
    "reference": "sentinel",
}

#: ``params.semantic_role`` value stamped on a ``reference`` control so the readout
#: normalization layer (percent-of-control) can distinguish a calibration reference from a
#: bare sentinel without a new kernel ``Control.kind``.
_REFERENCE_SEMANTIC_ROLE = "reference"

#: QC range for percent-of-control-NORMALIZED wet readings (M24 item #4). Raw fluorescence
#: a.u. live in ``cfg.metric_range`` (~[0, 1.2]); after percent-of-control the positive
#: control maps to ~100 and the negative background to ~0, so a normalized reading spans a
#: percent scale — a construct above the positive reference exceeds 100. Generous upper
#: bound so a genuinely strong candidate is not spuriously range-flagged; ``clip_negative``
#: floors sub-background readings at 0. Engaged ONLY on the normalized (controls) path.
_NORMALIZED_METRIC_RANGE: tuple[float, float] = (0.0, 200.0)

#: Per-candidate wet-cost ESTIMATE fed to the promotion budget (the promotion
#: decision precedes wet execution, so this is an estimate, not the realised
#: ledger). One tip per bracketing stock (2 transfers), fixed deck time.
_WET_COST_ESTIMATE = WetCostEstimate(n_transfers=2, duration_s=10.0)

#: Promotion budget: at most ``top_k`` in-window converged candidates per round.
_PROMOTION_TOP_K = 2

#: Claim ids the seeded hypotheses reference — the knowledge substrate. Frozen
#: across the run (G1: freeze knowledge -> identical round-2 proposal). The G1
#: discriminator injects a contrary claim by flipping one of these statuses via
#: the ``claims`` override parameter.
_CLAIM_POLAR_HIGHER = "c_polar_responds_higher"
_CLAIM_NONPOLAR_HIGHER = "c_nonpolar_responds_higher"


def _default_hypotheses() -> list[HypothesisObject]:
    """The posed hypotheses of the solvent-screen knowledge face. Their effective
    status is COMPILED from the claim ledger (not the stored status), so a
    contrary claim re-steers them."""
    return [
        HypothesisObject(
            hypothesis_id="hyp_polar_higher",
            statement="polar solvents give a higher plate-reader response",
            evidence_refs=[_CLAIM_POLAR_HIGHER],
        ),
        HypothesisObject(
            hypothesis_id="hyp_nonpolar_higher",
            statement="nonpolar solvents give a higher plate-reader response",
            evidence_refs=[_CLAIM_NONPOLAR_HIGHER],
        ),
    ]


def _default_claims() -> list[dict[str, Any]]:
    """The seeded claim ledger (ledger shape: claim_id + status). The baseline
    MCL asserts 'polar responds higher' and refutes its converse — a coherent,
    frozen knowledge state. The G1 discriminator overrides this list."""
    return [
        {"claim_id": _CLAIM_POLAR_HIGHER, "status": "supported"},
        {"claim_id": _CLAIM_NONPOLAR_HIGHER, "status": "rejected"},
    ]


# ---------------------------------------------------------------- domain bindings (M20)

#: Deterministic hypothesis id derived from a domain-declared seed claim, so a
#: ``seed_claims`` yaml block yields both the claim ledger AND the posed hypotheses
#: that reference it (the loop holds no domain-specific hypothesis literal).
def _hyp_id_for(claim_id: str) -> str:
    return f"hyp_{claim_id}"


#: Default coordinate-axis key inside a per-level descriptor map
#: ({level: {``coord``: value}}) — matches ``screen.target_coord``'s default and
#: ``catalysts.CATALYST_DESCRIPTORS``.
_COORD_NAME = "coord"


@dataclass(frozen=True)
class _DomainBindings:
    """Everything the deterministic template agent needs to screen a domain WITHOUT
    knowing its literals (M20 domain-swappability, EXP011 spirit). Resolved ONCE at
    run start by :func:`_domain_bindings`: from the design-space variable's
    ``descriptors`` map when a categorical variable carries one, else from the
    LEGACY-FALLBACK solvent constants (byte-identical to pre-M20).

      * ``variable``            — the categorical screening var driving both legs
      * ``candidate_pool``      — the levels screened each round
      * ``coords``              — level -> acquisition coordinate (ALL levels, so an
                                  llm-proposed level can be membership-checked)
      * ``descriptors``         — the raw {level: {coord: value}} map threaded to the
                                  generic wet leg (``None`` => legacy solvent path)
      * ``coord_name``          — the coordinate-axis key inside a level's map
      * ``window``              — (lo, hi) in-window range for the promotion gate
      * ``prefer_higher_default`` — base preference when knowledge is uninformative
      * ``higher_hyp_ids`` / ``lower_hyp_ids`` — hypotheses asserting "prefer higher"
                                  / "prefer lower" coord (drive the direction flip)
      * ``fixed_conditions``    — legacy recorded protocol conditions (solvent path
                                  only; the descriptor path expands params via
                                  ``catalysts.catalyst_params``)
      * ``params_kind``         — ``"solvent"`` (legacy) | ``"descriptor"`` (generic
                                  geometry) | ``"sequence"`` (bio construct): selects how a
                                  level's Candidate ``params`` are built
      * ``dry_capability``      — the Contract-v3 ``adapter_capability`` the domain's
                                  compute targets require (``molecular_geometry`` for
                                  chemistry, ``sequence_construct`` for biology); the
                                  dry-leg dispatch selects its adapter from THIS, never a
                                  domain name (M24 item #1)
      * ``compute_targets``     — the provider's ``compute_targets()`` map (``None`` for a
                                  provider-less legacy domain); the SEQUENCE params path
                                  forwards a level's neutral ComputeTarget payload as the
                                  Candidate ``params`` (dry-adapter input + lineage)
    """

    variable: str
    candidate_pool: tuple[str, ...]
    coords: dict[str, float]
    descriptors: dict[str, dict[str, float]] | None
    coord_name: str
    window: tuple[float, float]
    prefer_higher_default: bool
    higher_hyp_ids: tuple[str, ...]
    lower_hyp_ids: tuple[str, ...]
    fixed_conditions: dict[str, float]
    params_kind: str
    dry_capability: str
    compute_targets: Mapping[str, Any] | None


def _screen_variable(cfg: DomainConfig):
    """The categorical design-space variable carrying a ``descriptors`` map (the
    generic screening dim), or ``None`` if no variable declares one (legacy solvent
    path). Minimal form: the first such variable; a domain has exactly one."""
    for v in cfg.design_space.variables:
        if v.kind == "categorical" and v.descriptors:
            return v
    return None


def _dry_capability(cfg: DomainConfig) -> str:
    """The single Contract-v3 ``adapter_capability`` this domain's dry leg needs, read
    from the provider's ``compute_targets()`` (M24 item #1). A provider-less legacy domain
    has no compute targets, so it defaults to ``molecular_geometry`` — the chemistry PySCF
    anchor (byte-identical). A domain whose targets declare MIXED capabilities is refused
    LOUDLY: one dry leg drives one adapter, so a mixed-capability domain has no single
    dispatch. Domain-neutral: it reads the declared capability, never a domain name."""
    provider = getattr(cfg, "_provider", None)
    if provider is None:
        return INPUT_KIND_MOLECULAR_GEOMETRY
    caps = {t.adapter_capability for t in provider.compute_targets().values()}
    if not caps:
        return INPUT_KIND_MOLECULAR_GEOMETRY
    if len(caps) != 1:
        raise MCLError(
            f"domain {cfg.name!r}: compute_targets declare mixed adapter capabilities "
            f"{sorted(caps)}; one dry leg drives exactly one adapter capability"
        )
    return next(iter(caps))


def _domain_bindings(cfg: DomainConfig) -> _DomainBindings:
    """Resolve the per-run domain bindings. Generic path when a categorical design
    variable carries a ``descriptors`` map (candidate pool = its choices, acquisition
    coordinate = the descriptor coord, candidate params expand via
    ``catalysts.catalyst_params`` so geometry reaches the unchanged dry adapter);
    otherwise the LEGACY-FALLBACK path reproduces the exact solvent_screen constants
    byte-for-byte (the regression anchor)."""
    capability = _dry_capability(cfg)
    var = _screen_variable(cfg)
    if var is not None:
        levels = var.descriptors  # {level: {coord: value}}
        coords = {level: cmap[_COORD_NAME] for level, cmap in levels.items()}
        pool = tuple(var.choices)  # screen every declared level
        seeds = cfg.seed_claims or []
        higher = tuple(_hyp_id_for(c.claim_id) for c in seeds if c.direction == "higher")
        lower = tuple(_hyp_id_for(c.claim_id) for c in seeds if c.direction == "lower")
        # A geometry-free (sequence) domain builds its candidate params from the
        # provider's ComputeTarget payload (sequence + components), not a geometry table;
        # gate on the CAPABILITY (never the domain name). molecular_geometry keeps the
        # existing descriptor/geometry path byte-identical.
        is_sequence = capability in _SEQUENCE_CAPABILITIES
        provider = getattr(cfg, "_provider", None)
        return _DomainBindings(
            variable=var.name,
            candidate_pool=pool,
            coords=coords,
            descriptors=dict(levels),
            coord_name=_COORD_NAME,
            # Coords are normalized to [0, 1] (see catalysts.CATALYST_DESCRIPTORS);
            # the minimal form screens the full coordinate range (no explicit window
            # knob yet — a later batch may add one).
            window=(0.0, 1.0),
            # A domain without a seed_claims block cannot re-steer direction from
            # knowledge (no higher/lower hyp ids), so the base preference is "higher"
            # (the catalyst_high face). A seed_claims block supplies the flip.
            prefer_higher_default=True,
            higher_hyp_ids=higher,
            lower_hyp_ids=lower,
            fixed_conditions={},
            params_kind="sequence" if is_sequence else "descriptor",
            dry_capability=capability,
            compute_targets=(
                dict(provider.compute_targets()) if is_sequence and provider else None
            ),
        )
    # ---- LEGACY FALLBACK (solvent_screen byte-identical; see constants block above).
    return _DomainBindings(
        variable="solvent",
        candidate_pool=_CANDIDATE_POOL,
        coords=dict(SOLVENT_POLARITY),
        descriptors=None,
        coord_name=_COORD_NAME,
        window=(P_TARGET_LO, P_TARGET_HI),
        prefer_higher_default=True,
        higher_hyp_ids=("hyp_polar_higher",),
        lower_hyp_ids=("hyp_nonpolar_higher",),
        fixed_conditions=dict(_FIXED_CONDITIONS),
        params_kind="solvent",
        dry_capability=capability,
        compute_targets=None,
    )


def _candidate_params(level: str, bindings: _DomainBindings) -> dict[str, Any]:
    """Build a Candidate's ``params`` for a screening level. Three paths, selected by
    ``params_kind`` (itself set from the dry CAPABILITY, never a domain name):

      * ``"sequence"`` — a geometry-free construct: forward the level's Contract-v3
        ComputeTarget payload (sequence/promoter/rbs/cds) as the params the SYNC
        ``SequenceProxyAdapter`` reads, stamp the screening-var key, and lift any lineage
        keys (parent_construct/sequence_version) into the dedicated ``_LINEAGE_PARAMS_KEY``
        field (M24 item #4: store-only design lineage, NOT ``parent_obs_id``).
      * ``"descriptor"`` — a geometry domain: expand via ``catalysts.catalyst_params``
        (carries the explicit ``geometry`` the unchanged PySCF adapter reads).
      * legacy solvent — the level + recorded protocol conditions (byte-identical to pre-M20).
    """
    if bindings.params_kind == "sequence":
        return _sequence_candidate_params(level, bindings)
    if bindings.params_kind == "descriptor":
        return catalyst_params(level)
    return {bindings.variable: level, **bindings.fixed_conditions}


def _sequence_candidate_params(level: str, bindings: _DomainBindings) -> dict[str, Any]:
    """Candidate ``params`` for a sequence-construct level (M24 items #1/#4), built from
    the provider's neutral ComputeTarget payload — mcl reads a capability payload, never a
    biology table. The dry-adapter input keys (sequence/promoter/rbs/cds) are forwarded
    verbatim; the screening-var key carries the level (so ``_build_dry_view`` /
    ``_record_proposal`` / the wet ``screen_param`` path read it); construct lineage is
    lifted into the dedicated store-only lineage field."""
    targets = bindings.compute_targets or {}
    target = targets.get(level)
    if target is None:
        raise MCLError(
            f"sequence level {level!r} has no compute target in domain "
            f"{bindings.variable!r}; every screened level must be a declared construct"
        )
    payload = dict(target.payload)
    lineage = {k: payload.pop(k) for k in _LINEAGE_PAYLOAD_KEYS if k in payload}
    params: dict[str, Any] = dict(payload)  # sequence/promoter/rbs/cds -> dry adapter input
    params[bindings.variable] = level
    if lineage:
        params[_LINEAGE_PARAMS_KEY] = lineage
    return params


def _domain_seed_claims(cfg: DomainConfig) -> list[dict[str, Any]]:
    """The run's seed claim dicts: a domain-declared ``seed_claims`` block when
    present (M20 seed-claim neutralization), else the built-in polar family
    (byte-identical fallback)."""
    if cfg.seed_claims:
        return [{"claim_id": c.claim_id, "status": c.status} for c in cfg.seed_claims]
    return _default_claims()


def _domain_hypotheses(cfg: DomainConfig) -> list[HypothesisObject]:
    """The posed hypotheses: derived from a domain-declared ``seed_claims`` block
    (one hypothesis per seed claim, referencing it) when present, else the built-in
    solvent-screen face (byte-identical fallback)."""
    if cfg.seed_claims:
        return [
            HypothesisObject(
                hypothesis_id=_hyp_id_for(c.claim_id),
                statement=c.statement,
                evidence_refs=[c.claim_id],
            )
            for c in cfg.seed_claims
        ]
    return _default_hypotheses()


# ---------------------------------------------------------------- claim ledger bridge

#: decision_fn id stamped on SEED claim records (the run's externally-provided
#: prior). Seed records are the initial ledger, not adjudications, so they are not
#: gated by the online decision_fn registry (only ClaimDeltas are). This id marks
#: their provenance as "seed", distinct from any registered adjudication fn.
_SEED_DECISION_FN_ID = "seed_claim"


def _seed_ledger(claims: list[dict[str, Any]]) -> Ledger:
    """Bridge the run's seed claim dicts (``{claim_id, status}`` — the same shape
    ``compile_knowledge`` consumes) into the online ``Ledger`` the certification
    hook mutates. Each seed claim becomes an immutable version-1 head with a
    synthetic seed provenance and the ``NONE`` evidence band (no strength asserted
    yet — a later, sufficiently-strong ClaimDelta may supersede it).

    ``ledger_to_claim_dicts`` on this ledger reproduces the seed dicts bit-for-bit
    (verified: same knowledge_fingerprint), so a NullCertification run is a
    byte-identical M16 twin."""
    seed_prov = ProvenanceSnapshot(
        usage=ProvenanceUsage(observations=(), consumed_knowledge_fingerprint="seed"),
        activity=ProvenanceActivity(
            decision_fn_id=_SEED_DECISION_FN_ID,
            decision_fn_version="0",
            criterion_version="seed",
        ),
    )
    records = tuple(
        ClaimRecord(
            claim_id=c["claim_id"],
            version=1,
            status=ClaimDecisionStatus(c["status"]),
            statement="",
            evidence_strength=EvidenceStrength.NONE,
            provenance=seed_prov,
        )
        for c in claims
    )
    return Ledger(claims=records)


def _ledger_from_checkpoint(ckpt: dict[str, Any]) -> Ledger:
    """Deterministically reconstruct the claim ledger from a checkpoint snapshot
    (I4 resume discipline): the append-only record set is replayed verbatim, WITHOUT
    re-emitting any ``claim_decision`` event — the events were emitted once when
    the round first ran. Missing snapshot (pre-K-C checkpoint) => empty ledger."""
    snapshot = ckpt.get("claim_ledger") or []
    return Ledger(claims=tuple(ClaimRecord.model_validate(rec) for rec in snapshot))


# ---------------------------------------------------------------- crash/recovery (Phase 4)

def _kill(hook: Callable[[str, int], None] | None, point: str, round_id: int) -> None:
    """Invoke the (optional) interruption hook at a pinned killpoint. Default (no hook) is a
    no-op — a callable parameter, never an env flag (Phase 4 injection discipline). A test hook
    may raise :class:`_SimulatedCrash` to simulate a hard crash at ``point``."""
    if hook is not None:
        hook(point, round_id)


def _verify_not_forked(store: RunStore, ckpt: dict[str, Any]) -> None:
    """Forked-resume detection (Phase 4 item #1, INDEX_REF_X §Convergence c). The event at the
    checkpoint's ``last_event_seq`` must still hash to ``last_event_sha256``; an absent event or
    a hash mismatch means the log was rewritten/truncated/forked underneath the checkpoint ->
    refuse to resume. ADDITIVE-COMPAT: a pre-Phase-4 checkpoint lacking these keys skips the
    check and resumes exactly as before."""
    last_seq = ckpt.get("last_event_seq")
    last_sha = ckpt.get("last_event_sha256")
    if last_seq is None or last_sha is None:
        return  # old checkpoint: no anchor recorded, resume as before (compat)
    match = next((e for e in store.read_events() if e.get("seq") == last_seq), None)
    if match is None:
        raise ForkedResumeError(
            f"checkpoint references event seq {last_seq}, absent from the log — the event log "
            "was truncated or restored from an older point (forked history); refusing to resume."
        )
    actual = store._event_line_sha256(match)
    if actual != last_sha:
        raise ForkedResumeError(
            f"event seq {last_seq} no longer hashes to the checkpoint anchor "
            f"({actual} != {last_sha}) — the event log diverged from the checkpoint "
            "(tampered/forked history); refusing to silently adopt a branch."
        )


def _classify_resume_round(
    store: RunStore, start_round: int
) -> tuple[str, list] | None:
    """Event-log-as-truth reconciliation of the round to resume into (blueprint §Convergence
    a). Inspect the log to decide how round ``start_round`` must be handled:

      * ``("consume_issued", wet_obs)`` — its wet leg already ISSUED + persisted (the I2-I5
        torn window: the round's work is in the log, only the checkpoint lagged). Consume the
        logged wet observations and re-derive ONLY the certification state; do NOT re-issue
        (wet non-replay invariant). Raises :class:`WetReplayError` if issued-but-incomplete.
      * ``("consume_skipped", [])`` — the round legally skipped its wet leg (zero promotion)
        before the crash; re-derive certification state from an empty observation set.
      * ``None`` — no wet marker yet (crash before wet issuance, or a clean-boundary round that
        never ran): the round is re-executed in full.
    """
    events = store.read_events()

    def _for_round(kind: str) -> list[dict[str, Any]]:
        return [e for e in events
                if e["kind"] == kind and (e.get("payload") or {}).get("round_id") == start_round]

    issued = _for_round("wet_leg_issued")
    skipped = _for_round("wet_leg_skipped")
    if not issued and not skipped:
        return None
    if issued:
        n_expected = (issued[-1].get("payload") or {}).get("n_wells")
        wet_obs = [o for o in store.list_observations(round_id=start_round)
                   if o.raw_ref.kind == "wet"]
        if n_expected is not None and len(wet_obs) != n_expected:
            raise WetReplayError(
                f"round {start_round}: wet leg issued (n_wells={n_expected}) but "
                f"{len(wet_obs)} wet observations are persisted — incomplete results, and "
                "already-issued wet commands must not be replayed (persisted non-replay "
                "invariant); refusing to resume."
            )
        return ("consume_issued", wet_obs)
    return ("consume_skipped", [])


# ---------------------------------------------------------------- agent face

def _prefers_higher(view: KnowledgeView, bindings: _DomainBindings) -> bool:
    """Read the compiled knowledge: does the agent currently believe the HIGHER end
    of the acquisition coordinate responds higher? Pure function of the
    KnowledgeView (the ONLY consumption surface — knowledge is a compiled product).
    A "higher" hypothesis effectively SUPPORTED (and no "lower" one) prefers the
    high end; a flipped claim reverses this and re-orders the proposal (G1
    discriminator). When the evidence is symmetric/absent the domain's base
    ``prefer_higher_default`` is the deterministic tie-break.

    For solvent_screen (legacy bindings: higher=hyp_polar_higher,
    lower=hyp_nonpolar_higher, default=True) this is byte-identical to the old
    ``polar or not nonpolar``."""
    eff = {h.hypothesis_id: h.effective_status for h in view.hypotheses}
    higher = any(eff.get(hid) is HypothesisStatus.SUPPORTED for hid in bindings.higher_hyp_ids)
    lower = any(eff.get(hid) is HypothesisStatus.SUPPORTED for hid in bindings.lower_hyp_ids)
    if higher and not lower:
        return True
    if lower and not higher:
        return False
    return bindings.prefer_higher_default


def _acquisition(level: str, prefers_higher: bool, bindings: _DomainBindings) -> float:
    """Deterministic response-surface acquisition proxy, truth-blind: it reads only
    the PUBLIC descriptor coordinate (design knowledge), never the reader's hidden
    response optimum. Higher is better; the knowledge sets the direction. Coords are
    expected normalized to [0, 1] so the ``1 - raw`` inversion is well-defined."""
    raw = bindings.coords[level]
    return raw if prefers_higher else (1.0 - raw)


def _propose_candidates(view: KnowledgeView, bindings: _DomainBindings) -> list[Candidate]:
    """The deterministic template agent's proposal: the domain candidate pool
    ordered by knowledge-driven acquisition (DESC), tie-broken by level name so the
    ordering is total. Returns Candidate objects with stable ids."""
    prefers = _prefers_higher(view, bindings)
    ordered = sorted(
        bindings.candidate_pool,
        key=lambda s: (-_acquisition(s, prefers, bindings), s),
    )
    return [
        Candidate(
            cand_id=f"cand_{s}",
            params=_candidate_params(s, bindings),
            source="mcl_template_agent",
        )
        for s in ordered
    ]


def _record_proposal(
    store: RunStore, round_id: int, view: KnowledgeView, cands: list[Candidate],
    *, bindings: _DomainBindings,
) -> None:
    """Persist the agent proposal as a PRIOR_PROPOSAL decision. ``basis`` carries
    the claim ids the knowledge was compiled from, so a later round's proposal
    provably references the knowledge substrate (G5 basis linkage)."""
    decision_id = f"prop_r{round_id}"
    # Resume idempotency (Phase 4 item #1): a redone round (crash AFTER the proposal was
    # recorded — every killpoint I1-I6 sits past the proposal) must reproduce it silently;
    # re-appending the same decision_id would otherwise trip append_decision's duplicate-id
    # guard (Q-8). Skip if this round's proposal is already in the log.
    if any((e.get("payload") or {}).get("decision_id") == decision_id
           for e in store.read_events("decision")):
        return
    basis = sorted(
        {ref for h in view.hypotheses for ref in _refs_of(h)}
    )
    store.append_decision(
        DecisionRecord(
            decision_id=decision_id,
            round_id=round_id,
            actor=Actor.AGENT,
            kind=DecisionKind.PRIOR_PROPOSAL,
            content={
                "knowledge_fingerprint": view.knowledge_fingerprint,
                "basis": basis,
                "candidates": [c.params[bindings.variable] for c in cands],
                # usage-必键 方案 A (letters 080/086 §3): every agent proposal carries a
                # usage block at the write point. The template agent consults no provider,
                # so the block is empty — a legal degradation (key presence is the contract).
                "usage": {},
            },
        )
    )


def _refs_of(hyp_knowledge: Any) -> list[str]:
    """Claim ids referenced by a compiled hypothesis (its resolved evidence)."""
    return [e.claim_id for e in hyp_knowledge.evidence]


# ---------------------------------------------------------------- agent-backend switch (M18)

def _candidate_from_level(level: str, bindings: _DomainBindings) -> Candidate | None:
    """Map an LLM-proposed level name back into a MCL ``Candidate`` (llm mode). Unknown /
    hallucinated names (absent from the public descriptor coordinate table the dry/wet legs
    read) are dropped by returning ``None`` — a buggy model cannot inject an unrunnable
    candidate."""
    if level not in bindings.coords:
        return None
    return Candidate(
        cand_id=f"cand_{level}",
        params=_candidate_params(level, bindings),
        source="mcl_llm_agent",
    )


# ---------------------------------------------------------------- leg builders

@dataclass(frozen=True)
class _SyncDryProvenance:
    """Minimal provenance shim for the SYNCHRONOUS sequence dry leg (M24 item #1). The
    PySCF path carries a rich ``InstrumentProvenance`` (converged / scf_cycles / ...); a
    deterministic in-process proxy has no SCF, so it exposes only what ``_build_dry_view``
    reads — ``converged`` — which is always True (a proxy that could not compute would have
    raised ``AdapterError`` and aborted the round, never returned an unconverged result)."""

    converged: bool = True


#: Single shared instance — the sync proxy provenance is stateless.
_SYNC_DRY_PROVENANCE = _SyncDryProvenance()


@dataclass(frozen=True)
class _DryLegPlan:
    """Resolved dry-leg dispatch (M24 item #1): WHICH adapter drives the dry leg, and HOW.
    Selected ONCE per run by :func:`_make_dry_leg_plan` from the domain's declared dry
    ``adapter_capability`` — the injection precedent (agent_backend / physical_backend /
    reader), a strategy object handed to the round loop, never a mode/domain string.

      * ``adapter``      — the constructed dry adapter (PySCF or SequenceProxy).
      * ``capability``   — the Contract-v3 capability it was selected for.
      * ``metric``       — the dry objective metric this adapter emits.
      * ``metric_range`` — the dry-channel QC range for that metric.
      * ``kind``         — ``"async_job"`` (PySCF: compute lease + subprocess job) |
                           ``"sync_execute"`` (SequenceProxy: in-process ``execute``, NO
                           lease, NO subprocess).
    """

    adapter: Any
    capability: str
    metric: str
    metric_range: tuple[float, float]
    kind: str


def _make_dry_leg_plan(
    cfg: DomainConfig, bindings: _DomainBindings, out: Path
) -> _DryLegPlan:
    """Select the dry adapter for the domain's declared capability (M24 item #1). The
    dispatch is a small registry keyed on capability: each adapter DECLARES the capabilities
    it consumes (``ACCEPTS_INPUT_KINDS`` / ``accepts_capability``), probed through the
    contract's NEUTRAL reader ``adapter_accepts_capability`` — mcl hardcodes no domain name.
    ``molecular_geometry`` -> PySCF via its CURRENT async-job path (byte-identical chemistry
    anchor); ``sequence_*`` -> the SYNCHRONOUS SequenceProxy. An unroutable capability fails
    LOUDLY (never a silent fallback to PySCF)."""
    cap = bindings.dry_capability
    if adapter_accepts_capability(PySCFDryAdapter, cap):
        return _DryLegPlan(
            adapter=PySCFDryAdapter(jobs_root=out / "_dry_jobs", poll_interval_s=0.1),
            capability=cap,
            metric=DRY_METRIC,
            metric_range=_DRY_METRIC_RANGE,
            kind="async_job",
        )
    if adapter_accepts_capability(SequenceProxyAdapter, cap):
        return _DryLegPlan(
            adapter=SequenceProxyAdapter(),
            capability=cap,
            metric=SequenceProxyAdapter.default_metric,
            metric_range=_SEQ_DRY_METRIC_RANGE,
            kind="sync_execute",
        )
    raise MCLError(
        f"domain {cfg.name!r}: no dry adapter accepts capability {cap!r} "
        f"(compute_targets adapter_capability); register an adapter that declares it"
    )


def _domain_controls(cfg: DomainConfig) -> list[Control]:
    """Lay the domain-declared assay controls into kernel ``Control`` objects (M24 item #2,
    bio ruling ①; ZERO kernel change). Role -> kind via :data:`_CONTROL_ROLE_TO_KIND`:
    negative/positive are native kinds; ``reference`` rides ``kind=sentinel`` plus a
    ``params.semantic_role="reference"`` marker so the readout normalization layer
    (percent-of-control) can find the calibration reference. A domain that declares no
    controls (every chemistry domain) yields ``[]`` — the ``ExperimentObject.controls``
    default, so the wet plate is byte-identical."""
    specs = getattr(cfg, "controls", None) or []
    controls: list[Control] = []
    for spec in specs:
        params = dict(spec.params)
        if spec.role == "reference":
            params["semantic_role"] = _REFERENCE_SEMANTIC_ROLE
        controls.append(
            Control(
                control_id=spec.control_id,
                kind=_CONTROL_ROLE_TO_KIND[spec.role],
                params=params,
            )
        )
    return controls


def _control_roles(cfg: DomainConfig) -> dict[str, str]:
    """``control_id -> role`` for the domain's declared controls (empty for chemistry)."""
    return {spec.control_id: spec.role for spec in (getattr(cfg, "controls", None) or [])}


def _percent_of_control_normalize(
    cfg: DomainConfig, wet_obs: list
) -> tuple[list, tuple[float, float]] | None:
    """Readout-layer percent-of-control normalization (M24 item #4, bio ruling ②), applied
    by mcl in the DOMAIN/READOUT layer BEFORE QC/Trust and certification — the evidence
    compiler stays byte-identical (it reads only ``result.value`` on TRUSTED observations,
    domain-blind). Gated on the domain declaring BOTH a negative and a positive control:
    every chemistry domain declares none, so this returns ``None`` and the wet leg is
    byte-identical.

    The negative/positive control wells set the scale (``bio_readout.baselines_from_controls``
    / ``percent_of_control``); every well's value is re-expressed as a percent of that scale
    (positive -> ~100, negative background -> ~0). ``run_wet_leg`` owns raw->observation
    ingestion internally, so mcl normalizes the ingested observation VALUES here (before any
    adjudication) — behaviourally the readout-before-certification the charter requires.
    Returns ``(normalized_observations, normalized_qc_range)``; the caller QC-judges the
    normalized values against the returned range (percent scale, not the raw a.u. range)."""
    roles = _control_roles(cfg)
    neg = [
        o.result.value for o in wet_obs
        if o.is_control and roles.get(o.control_id) == "negative"
        and o.result.value is not None
    ]
    pos = [
        o.result.value for o in wet_obs
        if o.is_control and roles.get(o.control_id) == "positive"
        and o.result.value is not None
    ]
    if not neg or not pos:
        # No negative+positive calibration pair on the plate -> no percent-of-control scale.
        # (A domain that declares only a reference sentinel keeps the raw readout.)
        return None
    baselines = baselines_from_controls(neg, pos)
    normalized: list = []
    for o in wet_obs:
        if o.result.value is None:  # a dropped well carries no value to normalize
            normalized.append(o)
            continue
        pct = percent_of_control(o.result.value, baselines, clip_negative=True)
        normalized.append(
            o.model_copy(update={"result": o.result.model_copy(update={"value": pct})})
        )
    return normalized, _NORMALIZED_METRIC_RANGE


def _dry_experiment(
    cfg: DomainConfig, round_id: int, cands: list[Candidate], bindings: _DomainBindings,
    dry_plan: _DryLegPlan,
) -> ExperimentObject:
    """Build the dry screening experiment: one well per candidate (replicates=1 to keep the
    minimal loop's job count low). The dry metric + declared adapter come from the resolved
    ``dry_plan`` (chemistry -> ``polarity_proxy`` / ``pyscf_dry``, byte-identical; biology ->
    ``expression_proxy`` / ``sequence_proxy``)."""
    wells = [
        WellAssignment(well_id=f"A{i + 1}", row=0, col=i, cand_id=c.cand_id)
        for i, c in enumerate(cands)
    ]
    layout = LayoutAssignment(rows=1, cols=len(cands), seed=0, wells=wells)
    return ExperimentObject(
        exp_id=f"mcl_dry_r{round_id}",
        round_id=round_id,
        domain=cfg.name,
        objective=Objective(name=dry_plan.metric, metric=dry_plan.metric,
                            direction="maximize"),
        design_space=cfg.design_space,
        active_vars=[bindings.variable],
        candidates=cands,
        layout=layout,
        budget=Budget(**cfg.budget.model_dump()),
        execution_req=ExecutionReq(adapter=dry_plan.adapter.name),
        provenance=DesignProvenance(generator="mcl_template_agent"),
    )


def _wet_experiment(
    cfg: DomainConfig, round_id: int, cands: list[Candidate], bindings: _DomainBindings
) -> ExperimentObject:
    """Build the wet experiment (no layout yet — the compiled protocol supplies
    the deck positions). Metric ``solvent_response`` / ``catalyst_yield`` / bio fluorescence.

    Domain-declared controls (M24 item #2) are laid in here: ``protocol_spec_from_experiment``
    already realises ``exp.controls`` into plate wells, so the negative/positive/reference
    trio a bio domain declares reaches both the layout AND the readout normalization layer.
    A domain with no controls yields ``[]`` — the field default, so the chemistry plate is
    byte-identical."""
    return ExperimentObject(
        exp_id=f"mcl_wet_r{round_id}",
        round_id=round_id,
        domain=cfg.name,
        objective=cfg.objective,  # domain wet metric (solvent_response / catalyst_yield / ...)
        design_space=cfg.design_space,
        active_vars=[bindings.variable],
        candidates=cands,
        controls=_domain_controls(cfg),
        budget=Budget(**cfg.budget.model_dump()),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="mcl_template_agent"),
    )


def _qc_runner(metric_range: tuple[float, float], seed: int):
    """Adapt ``run_qc`` to the QCPolicy contract, scoping history to the same
    experiment so the two channels never cross-contaminate each other's QC."""

    def runner(exp, obs_list, history):
        same = [o for o in (history or []) if o.exp_id == exp.exp_id]
        return run_qc(
            exp, obs_list, same or None,
            seed=derive_seed(seed, "qc", exp.round_id, exp.exp_id),
            metric_range=metric_range,
        )

    return runner


def _in_window(level: str, bindings: _DomainBindings) -> bool:
    """A candidate is in the feasible window iff its public acquisition coordinate
    lies in ``bindings.window``; out-of-window candidates are denied at the
    promotion gate (``gate_window``) and never reach a wet well."""
    lo, hi = bindings.window
    return lo <= bindings.coords[level] <= hi


def _build_dry_view(
    cands: list[Candidate],
    exp: ExperimentObject,
    failures: dict[str, Any],
    provenance: dict[str, Any],
    prefers_higher: bool,
    bindings: _DomainBindings,
) -> list[DryCandidateView]:
    """Assemble one DryCandidateView per proposed candidate from the dry leg
    outcome: convergence off the formal provenance bit, in-window off the public
    polarity, acquisition off the knowledge-driven proxy, cost off the estimate,
    and — for a candidate whose job FAILED/TIMED-OUT — the scheduler failure
    detail (a failed dry leg is not evidence, ``dry_failed``). ``failures`` is the
    adapter's ``job_id -> failure`` map (empty for the sync sequence leg, which has no
    job/subprocess failure surface — a compute error there aborts the round loudly)."""
    well_by_cand = {w.cand_id: w.well_id for w in exp.layout.wells}
    views: list[DryCandidateView] = []
    for c in cands:
        level = c.params[bindings.variable]
        well_id = well_by_cand[c.cand_id]
        job_id = f"{exp.exp_id}:{exp.round_id}:{well_id}"
        failure = failures.get(job_id)
        prov = provenance.get(well_id)
        converged = bool(prov.converged) if prov is not None else False
        views.append(
            DryCandidateView(
                cand_id=c.cand_id,
                converged=converged,
                in_window=_in_window(level, bindings),
                acquisition=_acquisition(level, prefers_higher, bindings),
                wet_cost=_WET_COST_ESTIMATE,
                failure_detail=(failure.model_dump() if failure is not None else None),
            )
        )
    return views


# ---------------------------------------------------------------- reader

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise MCLError(f"sim_reader on {host}:{port} did not come up within {timeout}s")


def _inject_reader_faults(
    host: str, port: int, faults: dict[str, Any], timeout: float = 5.0
) -> None:
    """Send an ``inject`` admin command to the sim reader (M24 item #3). EVALUATION-HARNESS
    only — the peer of ``sim_reader.harvest_truth``: it engages the reader's device-fault
    model (plate_offsets / dropout / drift ...) and is NEVER part of the OS decision path.
    Truth isolation is preserved by the reader itself (the OS reading carries no fault-truth
    field; the reader-side truth sidecar records the offset). Loud on a reader-rejected
    injection (never a silent no-op)."""
    req = {"cmd": "inject", **faults}
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise MCLError("reader closed the connection without an inject reply")
            buf += chunk
    reply = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    if not reply.get("ok"):
        raise MCLError(f"reader refused fault injection {faults!r}: {reply}")


# ---------------------------------------------------------------- driver

def run_mcl_loop(
    domain_path: str | Path,
    rounds: int = 2,
    seed: int = 7,
    out_dir: str | Path = "runs/mcl",
    *,
    mode: str = "os",
    claims: list[dict[str, Any]] | None = None,
    hypotheses: list[HypothesisObject] | None = None,
    certification: CertificationPolicy | None = None,
    truth_profile: str | None = None,
    resume: bool = False,
    reader_host: str = "127.0.0.1",
    agent_backend: dict[str, Any] | None = None,
    interrupt_hook: Callable[[str, int], None] | None = None,
    physical_backend: SensedState | None = None,
    reader_faults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the two-legged MCL for ``rounds`` rounds. Returns a summary dict.

    ``claims`` / ``hypotheses`` default to the seeded knowledge substrate; the G1
    discriminator overrides ``claims`` to inject a contrary claim and assert the
    proposal/promotion changes. The reader is started in-process (deterministic,
    ``noise_sd=0``) and torn down on every exit path.

    ``certification`` is the SEVENTH planner-injection element (M17 K-C): the
    round-end hook feeds it the round's adjudicated wet observations + the live
    claim ledger, applies the ClaimDeltas it produces, emits one ``claim_decision``
    event per delta, and hands the UPDATED ledger to the next round's
    ``compile_knowledge`` (control loop -> scientific knowledge loop). It defaults
    to ``NullCertification`` — zero deltas, zero events, the ledger frozen, so an
    M16 run is byte-for-byte unchanged.

    ``truth_profile`` (letter 075 ruling) is an EVALUATION-HARNESS surface: it
    selects the reader's hidden truth face and is threaded to ``sim_reader.serve``
    only — it never enters DomainConfig / domain YAML (truth stays off the OS
    path). ``None`` => the default face (byte-identical to M16).

    ``resume`` reconstructs the claim ledger from the persisted checkpoint snapshot
    and continues from the last completed round WITHOUT re-emitting the prior
    rounds' ``claim_decision`` events (I4 discipline). It treats the EVENT LOG as truth
    (Phase 4 item #1): a forked/tampered log is refused (:class:`ForkedResumeError`), and a
    round whose events are complete but whose checkpoint lagged (the I2-I5 torn window) is
    NOT re-executed — it re-derives its certification state from the persisted results and
    does not re-issue its already-issued wet leg (:class:`WetReplayError` if incomplete).
    Decision-face re-emission on any redone round is guarded exactly-once by the store.

    ``interrupt_hook(killpoint, round_id)`` is a test-only crash-injection seam (default
    ``None`` = no-op): the interruption matrix passes a hook that raises :class:`_SimulatedCrash`
    at one of the six pinned killpoints I1..I6 to exercise crash/recovery. Never an env flag.

    ``physical_backend`` (M23 Phase 4-B, default ``None``) is the injected :class:`SensedState`
    physical backend (the fake backend today, a real instrument later -- mcl imports NEITHER, the
    backend is dependency-injected). ``None`` engages NO physical path: byte-identical to
    pre-Phase-4. A supplied backend routes the wet leg's deck transfers through the physical-action
    transaction ledger (``<run>/physical/action_ledger.jsonl``), narrows observations to COMMITTED
    wells (the commit-before-observation gate), logs ``physical_action_transition`` events, and
    unit-checks committed observations -- all BEFORE the unchanged QC/Trust adjudication.

    ``reader_faults`` (M24 item #3, default ``None``) is an EVALUATION-HARNESS surface, the
    exact peer of ``truth_profile``: an ``inject``-command payload (e.g.
    ``{"plate_offsets": {"": 0.08}}``) threaded to the reader AFTER it comes up and NEVER
    onto the OS path. It engages the reader's fault model (the plate-level additive
    ``plate_offset`` is a board constant, distinct from the per-well monotone
    ``calibration_drift``); truth isolation is unchanged — the OS-visible reading carries the
    corrupted value but NO fault-truth field, and only the reader-side truth sidecar records
    the injected ``plate_offset``. ``None`` injects nothing: byte-identical to pre-M24.
    """
    if rounds < 1:
        raise MCLError("rounds must be >= 1")
    cfg = load_domain(domain_path)
    # M20: the wet objective metric is now domain-parameterized (solvent_response for
    # solvent_screen, catalyst_yield for catalyst_screen, ...). The wet leg validates
    # it against ``cfg.objective.metric`` via ``run_wet_leg(wet_metric=...)`` — no
    # hardcoded solvent constant blocks a second domain here.
    # M20 domain-swappability: resolve the domain bindings ONCE here (candidate pool /
    # fixed conditions / acquisition coordinate / preference direction). Config-driven
    # when the yaml carries descriptors+acquisition; else the legacy solvent fallback
    # (byte-identical). Seed claims + posed hypotheses likewise default domain-aware:
    # a yaml ``seed_claims`` block replaces the built-in polar family (still overridable
    # by the explicit ``claims``/``hypotheses`` params — the G1 discriminator path).
    bindings = _domain_bindings(cfg)
    claims = _domain_seed_claims(cfg) if claims is None else claims
    hypotheses = _domain_hypotheses(cfg) if hypotheses is None else hypotheses
    certification = NullCertification() if certification is None else certification

    # Resolve the agent backend ONCE, here at construction (EXP002 injection discipline —
    # the round loop is handed a resolved strategy object, never a mode string). Invalid mode
    # / invalid provider route form loud-fails NOW, before the reader thread or any round runs
    # (letters 086 §2, 088 §3). ``None`` / template => the byte-identical regression twin.
    # The domain bindings are bound into the proposal/candidate hooks so both stay
    # domain-agnostic at the strategy layer.
    agent_strategy = resolve_agent_backend(
        agent_backend,
        record_proposal=functools.partial(_record_proposal, bindings=bindings),
        make_candidate=lambda level: _candidate_from_level(level, bindings),
    )

    out = Path(out_dir)
    if not resume and (out / "checkpoint.json").exists():
        raise MCLError(
            f"{out} already holds a run checkpoint — MCL runs fresh, use a new dir "
            "(pass resume=True to continue an interrupted run)"
        )
    store = RunStore(out, lock=True, cache_observations=True)

    # In-process plate-reader (deterministic truth surface for G5 replay). The
    # truth_profile is an evaluation-harness selector threaded ONLY to serve() —
    # None reproduces the M16 default face byte-for-byte (mcl.py is not an
    # EXP001-guarded package; the reader's hidden face never reaches the OS path).
    port = _free_port()
    srv = sim_reader.serve(
        reader_host, port, seed=derive_seed(seed, "reader"), noise_sd=0.0,
        truth_profile=(DEFAULT_TRUTH_PROFILE if truth_profile is None else truth_profile),
    )
    reader_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    reader_thread.start()

    _terminal_emitted = False
    try:
        _wait_port(reader_host, port)
        # M24 item #3: engage the reader's fault model (harness surface, off the OS path —
        # the same discipline as truth_profile above). ``None`` injects nothing.
        if reader_faults:
            _inject_reader_faults(reader_host, port, reader_faults)

        resume_consume: tuple[str, list] | None = None
        if resume:
            ckpt = store.read_checkpoint()
            if ckpt is None:
                raise MCLError(f"resume=True but {out} has no checkpoint.json")
            start_round = int(ckpt["completed_rounds"])
            # ---- I4 resume seam: rebuild the ledger from the persisted snapshot,
            # NOT by re-running decide/apply — a deterministic reconstruction that
            # re-emits nothing (the round-N claim_decision events were emitted once
            # when round N first ran). Mirrors the loop.py resume-rebuild that
            # deliberately does NOT re-emit learning_weight_assigned.
            ledger = _ledger_from_checkpoint(ckpt)
            # I4: restore the certification cross-round state (per-claim RoundState
            # accumulators) from the SAME checkpoint snapshot, so the accumulated
            # e-process survives resume bitwise and no evidence is re-folded. Missing
            # key (pre-K-F checkpoint, or a NullCertification run) => None (fresh).
            cross_round_state = ckpt.get("certification_state")
            # ---- Phase 4 item #1: treat the EVENT LOG as truth. (1) Refuse a forked/
            # tampered log. (2) Classify round start_round: if its wet leg already issued but
            # the checkpoint lagged (I2-I5 torn window), it is re-derived from logged results,
            # not re-executed (and its already-issued wet leg is NOT replayed). (3) A round
            # that only partially ran before wet issuance (crash before wet) has its
            # materialized-view orphans reconciled before full re-execution.
            _verify_not_forked(store, ckpt)
            resume_consume = _classify_resume_round(store, start_round)
            if resume_consume is None:
                round_started = any(
                    (e.get("payload") or {}).get("round_id") == start_round
                    for e in store.read_events("knowledge_updated")
                )
                if round_started:
                    store.reconcile_redo_rounds(start_round)
            attempt = sum(1 for _ in store.read_events("resume")) + 1
            store.append_event(
                "resume", {"from_round": start_round, "attempt": attempt}
            )
        else:
            start_round = 0
            ledger = _seed_ledger(claims)
            cross_round_state = None
            store.save_config({
                "domain": cfg.name, "mode": mode, "seed": seed, "loop": "mcl",
                "domain_config": cfg.model_dump(mode="json"),
                "config_fingerprint": config_fingerprint(cfg),
            })
            store.append_event("run_start", {
                "domain": cfg.name, "mode": mode, "seed": seed,
                "loop": "mcl", "rounds_target": rounds,
            })

        # M24 item #1: resolve the dry-leg dispatch ONCE from the domain's declared
        # capability (the injection precedent). Chemistry -> PySCF async job path
        # (byte-identical anchor); biology -> the synchronous SequenceProxy leg.
        dry_plan = _make_dry_leg_plan(cfg, bindings, out)
        leases = LeaseManager(out / "_leases")
        promotion = EvidenceGatedPromotion()
        promo_budget = PromotionBudget(
            top_k=_PROMOTION_TOP_K,
            max_transfers_total=cfg.plate.rows * cfg.plate.cols,
            risk_threshold=1.0,
        )

        for round_id in range(start_round, rounds):
            # The resume-consume classification applies ONLY to the first resumed round
            # (start_round); every later round runs fresh.
            round_consume = resume_consume if round_id == start_round else None
            ledger, cross_round_state = _run_round(
                cfg, store, round_id, seed, ledger, hypotheses,
                dry_plan, leases, promotion, promo_budget,
                certification, cross_round_state, reader_host, port,
                agent_strategy, bindings, resume_consume=round_consume,
                interrupt_hook=interrupt_hook, physical_backend=physical_backend,
            )
            store.write_checkpoint({
                "completed_rounds": round_id + 1, "domain": cfg.name,
                "mode": mode, "seed": seed, "loop": "mcl",
                "budget": Budget(**cfg.budget.model_dump()).model_dump(),
                # ledger snapshot: the I4 resume-rebuild source (deterministic
                # reconstruction, no event re-emission).
                "claim_ledger": [r.model_dump(mode="json") for r in ledger.claims],
                # certification cross-round state: the per-claim RoundState
                # accumulators (already JSON-shaped from decide). Persisted alongside
                # the ledger so a resume restores the e-process bitwise (I4).
                "certification_state": cross_round_state,
            })
            # I6 killpoint: checkpoint written, before the next round's knowledge recompile.
            # The normal resume path (rebuild-not-reemit) already covers this window.
            _kill(interrupt_hook, "I6", round_id)

        store.append_event("run_stop", {
            "exit_status": "success", "completed_rounds": rounds,
            "n_events_hint": None,
        })
        _terminal_emitted = True
        return _summarize(store, cfg, rounds)
    except _SimulatedCrash:
        # Injected crash simulation (interruption matrix): a HARD crash leaves NO run_stop
        # (absence == crash). Re-raise WITHOUT emitting a terminal event so resume faces the
        # exact torn state a kill -9 would leave. The finally block still tears the reader down
        # and releases the writer lock (a real crash frees both via process exit).
        raise
    except BaseException as exc:
        # Terminal-state semantics mirror run_loop: abort on interrupt, fail on
        # any other exception; a hard crash leaves no run_stop (absence == crash).
        if _terminal_emitted:
            raise
        status = "abort" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "fail"
        try:
            store.append_event("run_stop", {
                "exit_status": status,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
                "completed_rounds": None, "n_events_hint": None,
            })
        except (OSError, ExposError) as emit_err:  # best-effort; original wins
            # A broken store (disk/lock/serialization) must never MASK the
            # original error; narrow to the append_event failure modes so a
            # real bug here still surfaces rather than being swallowed.
            _log.warning("run_stop(%s) emission failed: %s (original re-raised)",
                         status, emit_err)
        raise
    finally:
        srv.shutdown()
        srv.server_close()
        store.release_writer_lock()


# ---------------------------------------------------------------- physical wet wrap (M23 Phase 4-B)

#: The off-plate stock reservoir id the physical wrap sources every deck transfer from (a
#: single well-mixed stock this phase; the low/high-polarity split is a later refinement --
#: PHASE4_WIRING_SPEC §2 "or total_ul if modeled one-transfer-per-well"). Seeded with an ample
#: volume so the VolumeLedger's source-remaining precheck never gates the simulated wet path.
_PHYSICAL_SOURCE_WELL = "RSV"
_PHYSICAL_SOURCE_SEED_UL = 1e9


def _planned_transfers(otp: Any, round_id: int, exp_id: str) -> list[PlannedAction]:
    """One :class:`PlannedAction` per deck well (the single-transfer-per-well model,
    PHASE4_WIRING_SPEC §2). ``action_id`` is the deterministic, resume-reproducible idempotency
    key ``derive_action_id(round_id, exp_id, well_idx)``; the destination is the plate well, the
    source the stock reservoir, the requested volume the well's total. ``expected_pre_state`` is
    left empty (the optimistic-concurrency snapshot is reserved -- a run-wide single source
    depletes across wells, so a per-well STATIC pre-state would be stale; the ledger's
    source-remaining precheck already guards under-draw)."""
    return [
        PlannedAction(
            action_id=ActionLedger.derive_action_id(round_id, exp_id, well_idx),
            round_id=round_id,
            spec_fingerprint=exp_id,
            source_well=_PHYSICAL_SOURCE_WELL,
            destination_well=wp.well_id,
            requested_volume_ul=wp.total_ul,
            backend_id="fake-0",
            expected_pre_state={},
            expected_post_state={},
        )
        for well_idx, wp in enumerate(otp.wells)
    ]


def _physical_ledger(store: RunStore) -> ActionLedger:
    """The run-wide append-only physical-action ledger at ``<run>/physical/action_ledger.jsonl``
    (PHASE4_WIRING_SPEC §2). A SEPARATE hash-chained file from the kernel event log -- the
    append-only TRUTH for physical state; the kernel log carries only a mirror (see
    :func:`_mirror_physical_events`). Constructing it over an existing dir replays + verifies the
    chain (a truncated/tampered ledger fails closed on resume -- LedgerCorruptError/TamperError)."""
    return ActionLedger(
        store.root / "physical",
        volume=VolumeLedger(
            capacities={_PHYSICAL_SOURCE_WELL: _PHYSICAL_SOURCE_SEED_UL},
            initial={_PHYSICAL_SOURCE_WELL: _PHYSICAL_SOURCE_SEED_UL},
        ),
    )


def _mirror_physical_events(store: RunStore, ledger: ActionLedger, round_id: int) -> None:
    """Thread THIS call's physical transitions into the kernel event log as
    ``physical_action_transition`` events, PRESERVING the ledger's append order
    (PHASE4_WIRING_SPEC §3: PENDING before I/O, COMMITTED before results). ``ledger.events`` holds
    only the events appended in THIS process lifetime (a replay on resume does not repopulate it),
    so a resumed round mirrors only its NEW transitions -- no duplicate mirroring. Each payload
    carries the registered required keys {action_id, round_id, to}; the physical ledger file
    remains the source of truth, the kernel log a lagging mirror."""
    for ev in ledger.events:
        payload = {
            k: v for k, v in ev.items()
            if k not in ("seq", "prev_sha", "ts", "kind", "line_sha")
        }
        payload["round_id"] = round_id
        store.append_event("physical_action_transition", payload)


def _ingest_units(cfg: DomainConfig, observations: list, metric: str) -> None:
    """The Phase 0 unit-ingest one-liner (PHASE4_WIRING_SPEC §4), applied to the COMMITTED
    observation set: compare each observation's carried ``MeasuredResult.unit`` against the
    domain's declared canonical unit for ``metric``. LOUD on a dimension mismatch (T2 --
    Mars-Climate-Orbiter guard); a NO-OP when the domain declares no unit for the metric
    (``cfg.metric_units is None`` / metric absent => legacy identical). Never converts (the
    Celsius offset trap): :func:`check_unit_consistency` is strict equality only."""
    declared = cfg.metric_units.get(metric) if cfg.metric_units else None
    for obs in observations:
        check_unit_consistency(obs.result.unit, declared, metric=metric)


def _physical_wet_wrap(
    cfg: DomainConfig,
    store: RunStore,
    round_id: int,
    wet_exp: ExperimentObject,
    otp: Any,
    physical_backend: SensedState,
    wet_obs: list,
) -> list:
    """mcl-side realization of the PHASE4_WIRING_SPEC §1 wrap (the screen.py:399-409 transition
    segment is an A-domain file -- left untouched). The deck transfers are routed through the
    transaction ledger; the COMMIT gate then NARROWS the observation set: only wells whose
    transfer COMMITTED survive (a non-committed well yields no observation -- the structural
    commit-before-observation gate, realized on the OUTPUT set since the construction side is A's,
    behaviourally identical to narrowing the input set). Physical transitions are mirrored into
    the run log; the committed survivors pass the unit-ingest check. The returned list is what
    QC/Trust then adjudicates -- the QC route (the ``QCPolicy(...).judge`` call below) still runs
    in SERIES after this gate (a CommittedResult is necessary but NOT sufficient, §5)."""
    actions = _planned_transfers(otp, round_id, wet_exp.exp_id)
    ledger = _physical_ledger(store)
    # dispatch_round for a fresh round; resume_round when THIS round's actions are already on the
    # ledger (a crash-resumed wet leg) -- resume_round applies the trichotomy (COMMITTED skip /
    # PENDING re-sense, never re-dispatch / PLANNED re-dispatch) idempotently and re-sends nothing.
    known = {r.action_id for r in ledger.records()}
    is_resume = any(a.action_id in known for a in actions)
    result = (resume_round if is_resume else dispatch_round)(
        actions, physical_backend, ledger)
    _mirror_physical_events(store, ledger, round_id)

    committed_wells = set(result.committed_by_well())
    kept = [o for o in wet_obs if o.layout_meta.well_id in committed_wells]
    _ingest_units(cfg, kept, cfg.objective.metric)
    return kept


def _run_round(
    cfg: DomainConfig,
    store: RunStore,
    round_id: int,
    seed: int,
    ledger: Ledger,
    hypotheses: list[HypothesisObject],
    dry_plan: _DryLegPlan,
    leases: LeaseManager,
    promotion: EvidenceGatedPromotion,
    promo_budget: PromotionBudget,
    certification: CertificationPolicy,
    cross_round_state: dict[str, Any] | None,
    reader_host: str,
    port: int,
    agent_strategy: Any,
    bindings: _DomainBindings,
    resume_consume: tuple[str, list] | None = None,
    interrupt_hook: Callable[[str, int], None] | None = None,
    physical_backend: SensedState | None = None,
) -> tuple[Ledger, dict[str, Any] | None]:
    """One full pipeline pass (knowledge -> agent -> dry -> promotion -> wet ->
    certification). Consumes the current claim ``ledger`` for knowledge compile and
    the ``cross_round_state`` (per-claim aggregation accumulators) for certification,
    and returns the ``(ledger, cross_round_state)`` the round-end certification hook
    produced (both frozen under NullCertification), which the NEXT round consumes.

    ``resume_consume`` (Phase 4 item #1): when this round is a resumed torn round whose wet
    leg already issued/completed (or legally skipped), it is ``("consume_issued", wet_obs)`` /
    ``("consume_skipped", [])`` and the round re-derives ONLY its certification state from the
    persisted results — no proposal re-record, no dry/wet re-issue (the events are already in
    the log; decision-face re-emission would dedup-skip anyway, and re-issuing wet violates the
    non-replay invariant). ``None`` => full execution. ``interrupt_hook`` fires the killpoints.

    ``physical_backend`` (M23 Phase 4-B, injection-gated): when a :class:`SensedState` backend is
    supplied, the wet leg routes its deck transfers through the physical-action transaction ledger
    (:func:`_physical_wet_wrap`) -- observations are narrowed to COMMITTED wells, transitions are
    logged as ``physical_action_transition`` events, and committed observations pass the unit
    check before QC/Trust. ``None`` (the DEFAULT) engages NO physical path: the wet leg is
    byte-identical to pre-Phase-4 (the solvent/catalyst regression anchor)."""
    # 1. knowledge compile + emit (the agent's ONLY knowledge surface). The claim
    #    substrate is the LIVE ledger projection: with a non-null certification
    #    policy whose deltas landed last round, this fingerprint moves (K2).
    view = compile_knowledge(ledger_to_claim_dicts(ledger), hypotheses)
    emit_knowledge_updated(store, view, round_id=round_id)

    # Phase 4 item #1 event-log-as-truth: a resumed torn round (its work is already in the
    # log) re-derives ONLY the certification state from the persisted (or empty) wet
    # observations. emit_knowledge_updated above dedup-skips (the round already logged it);
    # the proposal / dry / promotion / wet events likewise stay in the log untouched.
    if resume_consume is not None:
        _, consumed_obs = resume_consume
        return _certify_round(
            store, round_id, certification, consumed_obs, ledger, cross_round_state,
            view.knowledge_fingerprint, interrupt_hook=interrupt_hook,
        )

    # 2. agent proposal (recorded as a PRIOR_PROPOSAL decision). The deterministic template
    #    proposal is ALWAYS computed; the resolved backend strategy decides what drives the
    #    round: template mode records it unchanged (byte-identical); shadow mode records it
    #    unchanged AND emits a parallel LLM audit event; llm mode may replace it with the LLM
    #    proposal or, on reask-exhaustion / provider death, return an empty legal-quiet list.
    template_cands = _propose_candidates(view, bindings)
    cands = agent_strategy.decide(store, round_id, view, template_cands)
    prefers_higher = _prefers_higher(view, bindings)

    # llm-mode legal-quiet: the agent proposed nothing runnable this round. There is no dry/
    # wet work to do — close the round loudly (no silent edge) and let certification see an
    # empty observation set. Template/shadow always propose the full pool, so this guard never
    # fires for them (the byte-identical path is untouched).
    if not cands:
        store.append_event(
            "wet_leg_skipped", {"round_id": round_id, "reason": "no_candidate_proposed"}
        )
        return _certify_round(
            store, round_id, certification, [], ledger, cross_round_state,
            view.knowledge_fingerprint,
        )

    # 3. dry leg — dispatched by the resolved dry_plan (M24 item #1). molecular_geometry
    #    runs the out-of-process PySCF job per candidate under a compute lease (the async
    #    path, byte-identical chemistry anchor); a sequence capability runs the SYNCHRONOUS
    #    in-process SequenceProxy leg — NO compute lease, NO subprocess. Both ingest +
    #    adjudicate through the SAME QC/trust path, and both hit the I1 killpoint at the same
    #    seam (dry results produced, before ingest).
    dry_exp = _dry_experiment(cfg, round_id, cands, bindings, dry_plan)
    store.save_experiment(dry_exp)
    if dry_plan.kind == "async_job":
        compute_res = ResourceObject(f"pyscf-compute-r{round_id}", "compute")
        compute_lease = leases.acquire(compute_res.resource_id, ttl_s=300.0, tag="dry")
        if compute_lease is None:
            raise MCLError(f"round {round_id}: could not acquire compute lease")
        try:
            dry_result = dry_plan.adapter.run(dry_exp, backend=SubprocessBackend())
        finally:
            leases.release(compute_lease)
        # I1 killpoint: dry job submitted+run, before its results are ingested. Resume must
        # not ingest a half-written dry result — the redo reconciles orphans and re-executes.
        _kill(interrupt_hook, "I1", round_id)
        dry_obs, provenance = dry_raw_to_observations(dry_exp, dry_result.dry_raws)
        dry_failures: dict[str, Any] = dry_result.failures
    else:  # sync_execute: the sequence dry leg is deterministic + in-process (no lease/job).
        rng = np.random.default_rng(derive_seed(seed, "dry", round_id))
        exec_result = dry_plan.adapter.execute(dry_exp, rng)
        _kill(interrupt_hook, "I1", round_id)
        dry_obs = raw_to_observations(dry_exp, exec_result.raw_results, raw_kind="dry")
        provenance = {r.well_id: _SYNC_DRY_PROVENANCE for r in exec_result.raw_results}
        dry_failures = {}
    QCPolicy(_qc_runner(dry_plan.metric_range, seed),
             TrustPolicy(cfg.trust.suspect_high, cfg.trust.quarantine_low)).judge(
        store, dry_obs, dry_exp)

    # 4. Dry->Wet promotion gate — recorded evidence decision (who/why).
    dry_view = _build_dry_view(cands, dry_exp, dry_failures, provenance, prefers_higher, bindings)
    decision = promotion.decide(dry_view, None, view.knowledge_fingerprint,
                                promo_budget)
    emit_promotion_decision(store, round_id, decision)

    # 5. wet leg — promoted candidates through the W5 glue under an instrument
    #    lease. A zero-promotion round is loudly recorded and skips the wet leg
    #    (leaving no adjudicated observations for the certification hook).
    promoted_ids = [p.cand_id for p in decision.promoted]
    wet_obs: list = []
    if not promoted_ids:
        store.append_event("wet_leg_skipped", {
            "round_id": round_id, "reason": "no_candidate_promoted",
        })
    else:
        cand_by_id = {c.cand_id: c for c in cands}
        wet_cands = [cand_by_id[cid] for cid in promoted_ids]
        wet_exp = _wet_experiment(cfg, round_id, wet_cands, bindings)
        # Multi-replicate substrate (M17 K-F): expansion has exactly ONE owner,
        # compile_wet (it owns physical measurement order -> capture_index and
        # per-replicate custody). layout_from_protocol stays at defaults -- it
        # mirrors the already-replicated deck 1:1; passing n_replicates there
        # too would double-expand. interleave=True balances plate order
        # (corr(capture_index, arm) ~ 0), the letter-075 confound guard's
        # design-side counterpart.
        # M20: a descriptor-driven domain threads its per-variable {level: {coord}}
        # map + the screening param through the generic wet path; the solvent path
        # passes NEITHER kwarg (byte-identical to pre-M20).
        if bindings.descriptors is None:
            otp = compile_wet(wet_exp, n_replicates=cfg.replicates, interleave=True)
        else:
            otp = compile_wet(
                wet_exp, n_replicates=cfg.replicates, interleave=True,
                descriptors=bindings.descriptors, screen_param=bindings.variable,
                coord_name=bindings.coord_name,
            )
        wet_exp = wet_exp.model_copy(update={"layout": layout_from_protocol(otp)})
        store.save_experiment(wet_exp)

        instr_res = ResourceObject(f"plate-reader-r{round_id}", "instrument")
        instr_lease = leases.acquire(instr_res.resource_id, ttl_s=300.0, tag="wet")
        if instr_lease is None:
            raise MCLError(f"round {round_id}: could not acquire instrument lease")
        # Wet non-replay marker (Phase 4 item #1 / blue_to_red 092): a PERSISTED record that
        # round N's wet leg was issued, carrying the well count so resume can prove the wet
        # results are complete before consuming them (and refuse to replay an incomplete
        # issuance). The wet leg is a rewindable(False) segment.
        store.append_event("wet_leg_issued", {
            "round_id": round_id, "exp_id": wet_exp.exp_id,
            "n_wells": len(wet_exp.layout.wells),
        })
        try:
            wet_obs, _wet_result = run_wet_leg(
                wet_exp, otp, host=reader_host, port=port,
                wet_metric=cfg.objective.metric,
                # M23 Phase 4 closeout (letter 132): stamp the domain-declared unit
                # onto wet observations so the T4 ingest gate is live end-to-end.
                # Domains without metric_units yield "" -- byte-identical legacy.
                wet_unit=(cfg.metric_units or {}).get(cfg.objective.metric, ""),
            )
        finally:
            leases.release(instr_lease)
        # M23 Phase 4-B: when a physical backend is injected, wrap the transfers through the
        # transaction ledger and narrow wet_obs to COMMITTED wells (structural gate) BEFORE
        # QC/Trust. The DEFAULT (physical_backend is None) skips this entirely -- wet_obs is the
        # full run_wet_leg output judged unchanged (byte-identical regression anchor).
        if physical_backend is not None:
            wet_obs = _physical_wet_wrap(
                cfg, store, round_id, wet_exp, otp, physical_backend, wet_obs)
        # M24 item #4: readout-layer percent-of-control normalization (bio only; gated on a
        # declared negative+positive control pair). None => the raw readout is judged
        # against cfg.metric_range (chemistry byte-identical); a normalized readout is judged
        # against the percent range.
        wet_metric_range = cfg.metric_range
        _normalized = _percent_of_control_normalize(cfg, wet_obs)
        if _normalized is not None:
            wet_obs, wet_metric_range = _normalized
        QCPolicy(_qc_runner(wet_metric_range, seed),
                 TrustPolicy(cfg.trust.suspect_high, cfg.trust.quarantine_low)).judge(
            store, wet_obs, wet_exp)

        # Truth harvested OFF the OS path (scoring only) and persisted opaquely.
        try:
            truth = harvest_truth(host=reader_host, port=port)
            if truth:
                store.save_truth(round_id, truth)
        except (OSError, ConnectionError) as exc:
            # Truth harvest is a scoring-side convenience, never a decision input;
            # its failure must not fail the OS round, but it is logged (not silent).
            _log.warning("round %d: truth harvest failed (non-fatal): %s", round_id, exc)

    # I2 killpoint: wet observations landed + QC-judged (persisted), before certification.
    # Resume finds the wet leg complete and re-derives certification state from the persisted
    # observations (consume path) rather than re-issuing the wet leg.
    _kill(interrupt_hook, "I2", round_id)

    # 6. certification hook (M17 K-C, the seventh element): turn this round's
    #    ADJUDICATED wet observations into ClaimDeltas, land them on the ledger and
    #    emit one claim_decision per delta. The returned ledger re-steers round N+1.
    #    NullCertification -> [] deltas -> ledger frozen, zero events (M16 twin).
    return _certify_round(
        store, round_id, certification, wet_obs, ledger, cross_round_state,
        view.knowledge_fingerprint, interrupt_hook=interrupt_hook,
    )


def _certify_round(
    store: RunStore,
    round_id: int,
    certification: CertificationPolicy,
    adjudicated_observations: list,
    ledger: Ledger,
    cross_round_state: dict[str, Any] | None,
    knowledge_fingerprint: str,
    interrupt_hook: Callable[[str, int], None] | None = None,
) -> tuple[Ledger, dict[str, Any] | None]:
    """Round-end evidence -> claim step. PURE-decider + apply + emit, in that order:

      1. ``certification.decide(...)`` (pure, no store) -> ``(deltas, new_state)``:
         the ClaimDeltas AND the updated cross-round state (per-claim RoundState
         accumulators — the K-F seam; NullCertification returns ``([], state)``);
      2. ``apply_claim_deltas`` lands the deltas under the K-A governance red lines
         and returns the updated ledger + a per-delta ApplyReport;
      3. ONE ``claim_decision`` event per delta — landed, degraded OR denied (the
         deny_reason travels), mirroring the promotion "no silent edge" discipline.

    Returns the updated ``(ledger, cross_round_state)``; the caller checkpoints BOTH
    (the ledger snapshot AND the certification state) so a resume restores the
    accumulated e-process bitwise (I4). The emission POINT lives here (not in the
    pure decider), symmetric with ``emit_promotion_decision`` /
    ``emit_knowledge_updated``. Resume never reaches this path for rounds BEFORE
    start_round (rebuilt from the checkpoint snapshot). For the ONE resumed torn round it IS
    re-entered to re-derive state (Phase 4 item #1 consume path), but every claim_decision
    re-emission is dedup-guarded exactly-once by the store, so no duplicate lands."""
    deltas, new_state = certification.decide(
        adjudicated_observations, ledger, cross_round_state, round_id,
        knowledge_fingerprint,
    )
    # I3 killpoint: statistical evidence decided (pure, NO persistent effect yet), before
    # apply. Resume re-runs decide bitwise (K5) from the same pre-round ledger + state, so no
    # double effect can arise here.
    _kill(interrupt_hook, "I3", round_id)
    if not deltas:
        # NullCertification / legal-quiet: no deltas -> ledger unchanged, zero
        # events (the surface-absent discipline — the base policy set nothing). The
        # state still flows through (a stateful policy may have carried it forward).
        return ledger, new_state

    # One delta per target claim in K-C (unique targets); pair each ApplyReport
    # outcome back to its delta by claim id for the provenance-carrying emit.
    by_target = {d.target_claim_id: d for d in deltas}
    if len(by_target) != len(deltas):
        raise MCLError(
            f"round {round_id}: certification produced multiple deltas for one "
            "claim id — the K-C emit pairing assumes one delta per target claim"
        )
    new_ledger, report = apply_claim_deltas(ledger, deltas)
    # I4 killpoint: ClaimDeltas applied to the (in-memory) ledger, before the claim_decision
    # events emit. The apply is pure and lands on the pre-round ledger rebuilt from the
    # checkpoint, so a resumed re-run re-derives the SAME post-round ledger + e-product exactly
    # once (Phase 4 item #2) — no double-multiplication, no double-applied delta.
    _kill(interrupt_hook, "I4", round_id)
    for outcome in report.outcomes:
        emit_claim_decision(
            store,
            round_id=round_id,
            delta=by_target[outcome.target_claim_id],
            final_status=outcome.final_status,
            landed_version=outcome.landed_record_version,
            deny_reason=outcome.deny_reason,
        )
    # I5 killpoint (the double-emission bullseye): claim_decision events emitted, before the
    # round checkpoint is written. A resumed re-run re-emits them — the store's decision-face
    # dedup guard skips each (same key + same provenance fingerprint), so no duplicate lands.
    _kill(interrupt_hook, "I5", round_id)
    return new_ledger, new_state


def _summarize(store: RunStore, cfg: DomainConfig, rounds: int) -> dict[str, Any]:
    all_obs = store.list_observations()
    return {
        "domain": cfg.name,
        "loop": "mcl",
        "rounds_completed": (store.read_checkpoint() or {}).get("completed_rounds", 0),
        "rounds_target": rounds,
        "n_observations": len(all_obs),
        "n_dry": sum(1 for o in all_obs if o.raw_ref.kind == "dry"),
        "n_wet": sum(1 for o in all_obs if o.raw_ref.kind == "wet"),
        "n_trusted": len(store.list_observations(trust=TrustLevel.TRUSTED)),
        "n_promotion_decisions": len(store.read_events("promotion_decision")),
        "n_knowledge_updates": len(store.read_events("knowledge_updated")),
        "n_claim_decisions": len(store.read_events("claim_decision")),
    }
