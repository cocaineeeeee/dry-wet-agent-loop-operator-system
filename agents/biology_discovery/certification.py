"""``DiscoveryCertification`` — the mcl integration seam for M28 (v0.1).

B's ``expos.mcl._certify_round`` calls a ``CertificationPolicy`` (the SEVENTH planner-
injection element, ``expos.planner.certification.CertificationPolicy``):

    certification.decide(adjudicated_observations, ledger, cross_round_state,
                         round_id, knowledge_fingerprint) -> (list[ClaimDelta], state)

and then OWNS the single ``apply_claim_deltas`` mutation + the ``claim_decision`` emit.
``DiscoveryCertification`` is a drop-in object that CONFORMS to that protocol so B injects
M28 discovery into the existing mcl loop with **zero edits to mcl / planner / kernel** — the
same way ``NullCertification`` / ``RegisteredFnCertification`` plug in.

**Late id binding (the M28 head-from-exp ruling, blue_to_red/154).** Candidate / control ids
are MINTED PER RUN (``expos.kernel.objects.Candidate.cand_id`` defaults to ``new_id("cand")``),
so a claim head whose arms are prebuilt OUTSIDE the run can never bind to this run's
experiment — the arms simply do not match. The fix is structural, not a workaround:

  * a ``DiscoveryVerdict`` carries **no id** — only the domain-invariant part of the
    proposal: the hypothesis (statement + claim id + FAVOURABLE DIRECTION semantics), the
    evidence (or the recipe to derive it), and a domain-agnostic :class:`ArmSelector` that
    names each arm by SEMANTICS (role + a params subset match), never by id;
  * the id binding is DEFERRED to the moment the policy sees this round's REAL arms — the
    compiled ``ExperimentObject`` (when the caller has one) or the round's adjudicated
    observations, whose public arm keys (``cand_id`` / ``control_id``) ARE the experiment's
    minted ids. ``decide`` resolves the selectors there and builds a ``ClaimHead`` bound to
    THIS run's ids (:meth:`DiscoveryCertification.build_heads`), the same head shape K-B's
    aggregator joins arms on.

So a per-run random id is absorbed by construction, and B's side needs ZERO changes: the
staged verdicts are id-free, ``_certify_round`` keeps calling ``decide -> apply_claim_deltas
-> emit_claim_decision`` exactly as today.

THE MOAT, unchanged: this adapter produces ClaimDelta **proposals** only (via
``ledger_bridge.build_round_deltas`` → ``build_delta``); the kernel gate inside B's
``_certify_round`` (``apply_claim_deltas``) is still the SOLE mutator. Agents/LLM never touch
the ledger. The registered decision_fn ``m28_discovery_verdict`` (imported here → registered)
is biology-agnostic, so B's governance gate accepts these deltas like any other.

Trust discipline (bio_seams/M28.md seam #1): the decisive band is fed ONLY by trust the QC
stream adjudicated.

  * ARMS-BOUND path (no prebuilt evidence): only ``TrustLevel.TRUSTED`` observations of the
    bound arms contribute a replicate; a contrast that cannot be formed from TRUSTED
    observations yields untrusted evidence → INSUFFICIENT (non-mutating, K3). Absence of
    ANY observation on both arms emits NO delta at all (absence of a measurement is not
    evidence — the same honest rule ``AggregatedCertification`` follows).
  * PREBUILT-evidence path: ``EvidenceObservation.trusted`` as B set it, CROSS-CHECKED
    against the round's ``adjudicated_observations`` — an observation the QC stream saw and
    did NOT mark TRUSTED is forced ``trusted=False``. An observation absent from the stream
    keeps B's flag (the domain-local demo passes an empty stream).

Either way the certified stream can only ever REMOVE trust, never manufacture it — untrusted
evidence never mutates a claim (kernel red line K3, enforced agent-side before the gate too).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

# READ-ONLY kernel/planner/qc imports (called, never edited). TrustLevel lets the adapter
# read the QC verdict on an adjudicated observation; ``ClaimHead`` is the qc-owned head shape
# (claim + direction + the two arms) K-B's aggregator joins on — reused verbatim so a head
# this adapter binds is interchangeable with the one ``AggregatedCertification`` wants;
# ``CertificationError`` keeps the fail-loud-at-wiring error family shared with the planner.
# The ``CertificationPolicy`` Protocol needs no base class (structural typing).
from expos.kernel.claims import ClaimDelta, Ledger
from expos.kernel.objects import TrustLevel
from expos.planner.certification import CertificationError
from expos.qc.certification_stats import ClaimHead

from analysis_backends.deterministic import DeterministicAnalysisBackend
from analysis_backends.objects import AssayDataset, EvidenceObservation, ReplicateMeasurement
from hypotheses.objects import Hypothesis

from agents.biology_discovery import ledger_bridge

#: CrossRoundState shape mirrors ``expos.planner.certification.CrossRoundState``
#: (JSON-serializable ``{claim_id: ...}`` or None). M28 v0.1 is a stateless criterion, so it
#: threads the state through untouched (the honest surface-absent discipline).
CrossRoundState = dict[str, Any] | None

#: Arm roles an :class:`ArmSelector` may name. Domain-agnostic: the kernel's two-arm
#: vocabulary (a candidate arm vs a control arm), never a biological word.
ARM_ROLES = ("candidate", "control")


def _group_key(obs: Any) -> str | None:
    """The PUBLIC arm key of an observation (control id for a control, else its candidate
    id) — the SAME join key K-B's aggregator and the mcl legs use. No truth field is read.
    Defined locally (the planner's twin is private) so this adapter reaches into nothing."""
    return obs.control_id if getattr(obs, "is_control", False) else getattr(obs, "cand_id", None)


@dataclass(frozen=True)
class ArmSelector:
    """A domain-agnostic, ID-FREE way to name one arm of a contrast.

    An arm is selected by SEMANTICS the staging side knows BEFORE the run mints any id:
      * ``role`` — ``"candidate"`` or ``"control"`` (the kernel's own two-arm vocabulary);
      * ``params_match`` — a SUBSET match on the arm's public ``params`` map (e.g.
        ``{"construct": "sfGFP"}``); ``{}`` (default) matches every arm of the role;
      * ``control_kind`` — for ``role="control"``, the control's declared kind
        (``sentinel`` / ``negative`` / ``positive``); ``None`` matches any.

    ``params_match`` is a semantic (construct/params) predicate, so it resolves against the
    compiled ``ExperimentObject``'s arms. Observations carry only arm KEYS (no params), so a
    params/kind-matching selector REQUIRES the experiment (fail-loud, never a silent
    mis-bind); a role-only selector resolves from the round's observations alone."""

    role: str = "candidate"
    params_match: Mapping[str, Any] = field(default_factory=dict)
    control_kind: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ARM_ROLES:
            raise CertificationError(
                f"ArmSelector role={self.role!r} must be one of {list(ARM_ROLES)}"
            )
        if self.control_kind is not None and self.role != "control":
            raise CertificationError(
                f"ArmSelector control_kind={self.control_kind!r} is only meaningful for "
                f'role="control" (got role={self.role!r})'
            )

    @property
    def is_control(self) -> bool:
        return self.role == "control"

    @property
    def needs_experiment(self) -> bool:
        """True when the selector matches on arm SEMANTICS (params / control kind), which
        only the compiled experiment carries — observations expose ids alone."""
        return bool(self.params_match) or self.control_kind is not None

    def _params_ok(self, params: Mapping[str, Any] | None) -> bool:
        p = params or {}
        return all(p.get(k) == v for k, v in self.params_match.items())

    def resolve_from_experiment(self, exp: Any) -> tuple[str, ...]:
        """This run's REAL arm ids for this selector, read off the compiled experiment
        (``exp.candidates`` / ``exp.controls``). Deterministic (sorted) — the ids are minted
        per run, which is exactly why this must happen here and not at staging time."""
        if self.is_control:
            keys = [
                c.control_id
                for c in getattr(exp, "controls", ())
                if (self.control_kind is None or getattr(c, "kind", None) == self.control_kind)
                and self._params_ok(getattr(c, "params", None))
            ]
        else:
            keys = [
                c.cand_id
                for c in getattr(exp, "candidates", ())
                if self._params_ok(getattr(c, "params", None))
            ]
        return tuple(sorted(keys))

    def resolve_from_observations(self, observations: Sequence[Any]) -> tuple[str, ...]:
        """This round's REAL arm ids for a ROLE-ONLY selector, read off the round's
        observations' public arm keys (which ARE the experiment's minted ids). A semantic
        selector cannot be resolved this way — that fails loud rather than mis-binding."""
        if self.needs_experiment:
            raise CertificationError(
                f"ArmSelector(role={self.role!r}, params_match={dict(self.params_match)!r}, "
                f"control_kind={self.control_kind!r}) matches on arm SEMANTICS, which only "
                "the compiled ExperimentObject carries; construct DiscoveryCertification "
                "with experiment=<the round's compiled exp> (observations expose ids only)"
            )
        keys = {
            _group_key(o)
            for o in observations
            if bool(getattr(o, "is_control", False)) is self.is_control
        }
        return tuple(sorted(k for k in keys if k is not None))


@dataclass(frozen=True)
class DiscoveryVerdict:
    """One analysis verdict staged for certification — **id-free by construction**.

    It carries only what is knowable BEFORE the run mints any id:
      * ``hypothesis`` — the competing hypothesis under test: its ledger ``claim_id``,
        ``statement`` and ``direction`` (the FAVOURABLE-DIRECTION semantics the head needs);
      * ``observation`` — OPTIONAL prebuilt evidence (the ``AnalysisAgent``'s
        ``EvidenceObservation``, e.g. the domain-local runnable / a retrospective dataset).
        ``None`` selects the ARMS-BOUND path: the evidence is derived from THIS round's
        adjudicated observations on the bound arms;
      * ``focal`` / ``reference`` — :class:`ArmSelector`s naming the two arms SEMANTICALLY.
        Defaults: every candidate arm vs every control arm (the canonical contrast);
      * ``replicate_kind`` — how the arms-bound replicates are counted (M24-B ruling ③):
        ``"biological"`` (independent evidence, the default) or ``"technical"`` (correlated
        re-reads → the band caps at weak, they never masquerade as independent);
      * ``obs_join_id`` — legacy/optional, PREBUILT path only: the id of the adjudicated
        kernel observation the prebuilt evidence came from, used to join the trust
        cross-check (defaults to the evidence's own ``observation_id``). Unused on the
        arms-bound path, where trust is read from the contributing observations directly.
    """

    hypothesis: Hypothesis
    observation: EvidenceObservation | None = None
    obs_join_id: str | None = None
    focal: ArmSelector = field(default_factory=lambda: ArmSelector(role="candidate"))
    reference: ArmSelector = field(default_factory=lambda: ArmSelector(role="control"))
    replicate_kind: str = "biological"

    def __post_init__(self) -> None:
        if self.replicate_kind not in ("biological", "technical"):
            raise CertificationError(
                f"DiscoveryVerdict replicate_kind={self.replicate_kind!r} must be "
                '"biological" or "technical"'
            )

    def join_id(self) -> str | None:
        if self.obs_join_id is not None:
            return self.obs_join_id
        return None if self.observation is None else self.observation.observation_id

    @property
    def favorable_direction(self) -> str:
        """The head's stated direction, derived from the hypothesis' predicted sign — a
        DOMAIN-INVARIANT semantic (no id, no biology word) in K-B's own vocabulary."""
        return "higher" if self.hypothesis.direction > 0 else "lower"


class DiscoveryCertification:
    """``CertificationPolicy`` adapter — the mcl entry point for M28 discovery.

    Construct it with the round's staged ``DiscoveryVerdict``s (or plain
    ``(Hypothesis, EvidenceObservation)`` tuples). ``decide`` binds each verdict's arms to
    THIS run's real ids, builds one ``ClaimDelta`` per verdict via the bridge — chaining
    provenance to the run's REAL compiled-knowledge fingerprint (the ``knowledge_fingerprint``
    mcl passes) — and returns ``(deltas, cross_round_state)``; B's ``_certify_round`` lands them.

    Parameters:
      * ``verdicts`` — the staged analysis verdicts for this round (id-free).
      * ``experiment`` — OPTIONAL compiled ``ExperimentObject``. When the caller already has
        it (the M29 physical leg compiles before wiring the seventh element), arm selectors
        resolve against its REAL ``candidates`` / ``controls`` — including semantic
        (params / control-kind) selectors. When absent (``run_mcl_loop`` injects the policy
        BEFORE any experiment exists), role-only selectors resolve from the round's
        observations' arm keys, which are the same minted ids. Either way the binding happens
        per-round, inside ``decide`` — never at staging time.
      * ``run_fingerprint`` — recorded on each delta's provenance activity (K4 side-info).
      * ``validation_level`` / ``is_wet_observation`` — the HONEST label stamped on evidence
        derived from the round's observations. Defaults ``"simulation"`` / ``False``: an M29
        fake-plate-reader round is simulated physics. A caller with a real instrument sets
        them; nothing in this adapter ever infers "wet" from the data.
      * ``enforce_adjudicated_trust`` — PREBUILT path only: when True (default), cross-check
        each verdict's ``join_id`` against the round's ``adjudicated_observations``: a
        QC-non-TRUSTED observation forces ``trusted=False``. Pass False when the id join is
        not wired to honor ``EvidenceObservation.trusted`` as B set it (seam #1). The
        arms-bound path always reads trust from the contributing observations."""

    name = "discovery_certification"

    def __init__(
        self,
        verdicts: Sequence["DiscoveryVerdict | tuple[Hypothesis, EvidenceObservation]"],
        *,
        experiment: Any | None = None,
        run_fingerprint: str = "m28-mcl",
        validation_level: str = "simulation",
        is_wet_observation: bool = False,
        enforce_adjudicated_trust: bool = True,
    ) -> None:
        self._verdicts: list[DiscoveryVerdict] = [
            v if isinstance(v, DiscoveryVerdict) else DiscoveryVerdict(v[0], v[1])
            for v in verdicts
        ]
        self._experiment = experiment
        self._run_fingerprint = run_fingerprint
        self._validation_level = validation_level
        self._is_wet_observation = is_wet_observation
        self._enforce_adjudicated_trust = enforce_adjudicated_trust
        self._backend = DeterministicAnalysisBackend()

    # -- head binding -----------------------------------------------------------------
    def build_heads(self, adjudicated_observations: Sequence[Any]) -> list[ClaimHead]:
        """One ``ClaimHead`` per staged verdict, its arms BOUND TO THIS RUN's minted ids.

        The head is built HERE (not by the caller) because ``cand_id`` / ``control_id`` are
        minted per run: the compiled experiment (``experiment=``) is the primary source; the
        round's observations' public arm keys are the fallback. The head shape is qc's own
        (``expos.qc.certification_stats.ClaimHead``), so it is interchangeable with the head
        ``AggregatedCertification`` binds — same claim id, same direction semantics, same
        arm-key join. Public so B (or an audit) can read exactly which ids were bound."""
        return [self._head(v, adjudicated_observations) for v in self._verdicts]

    def _head(self, verdict: DiscoveryVerdict, observations: Sequence[Any]) -> ClaimHead:
        return ClaimHead(
            claim_id=verdict.hypothesis.claim_id,
            statement=verdict.hypothesis.statement,
            favorable_direction=verdict.favorable_direction,
            focal_group=self._bind(verdict.focal, observations),
            reference_group=self._bind(verdict.reference, observations),
        )

    def _bind(self, selector: ArmSelector, observations: Sequence[Any]) -> tuple[str, ...]:
        if self._experiment is not None:
            return selector.resolve_from_experiment(self._experiment)
        return selector.resolve_from_observations(observations)

    def decide(
        self,
        adjudicated_observations: Sequence[Any],
        ledger: Ledger,
        cross_round_state: CrossRoundState,
        round_id: int,
        knowledge_fingerprint: str,
    ) -> tuple[list[ClaimDelta], CrossRoundState]:
        """Turn this round's staged verdicts into ClaimDelta PROPOSALS (no mutation).

        Matches the ``CertificationPolicy.decide`` signature exactly, so ``mcl._certify_
        round`` calls it and then owns ``apply_claim_deltas``. ``knowledge_fingerprint`` is
        threaded into the provenance as ``consumed_knowledge_fingerprint`` (K4 chain closes
        against B's real ledger). One delta per verdict (a competing pair → two deltas → the
        kernel gate certifies one SUPPORTED and REJECTS the other).

        This is where the id binding happens: each verdict's arm selectors are resolved
        against THIS round's real arms (:meth:`build_heads`) before any evidence is joined."""
        items: list[tuple[Hypothesis, EvidenceObservation]] = []
        for verdict in self._verdicts:
            if verdict.observation is not None:
                # PREBUILT evidence (domain-local / retrospective): no arm join is needed —
                # the statistic already exists; the trust cross-check joins by obs id.
                items.append(
                    (verdict.hypothesis, self._trust_gated(verdict, adjudicated_observations))
                )
                continue
            # ARMS-BOUND: bind the head to THIS round's real arm ids, here and now.
            head = self._head(verdict, adjudicated_observations)
            evidence = self._evidence_from_arms(verdict, head, adjudicated_observations)
            if evidence is None:
                # Neither arm carries ANY observation this round (e.g. a wet-skipped round):
                # absence of a measurement is not evidence (K3) — emit no delta at all, the
                # same honest rule ``AggregatedCertification`` follows for an empty contrast.
                continue
            items.append((verdict.hypothesis, evidence))
        deltas = ledger_bridge.build_round_deltas(
            ledger,
            items,
            run_fingerprint=self._run_fingerprint,
            consumed_knowledge_fingerprint=knowledge_fingerprint,
        )
        # Stateless criterion: pass the cross-round state through unchanged (surface-absent).
        return deltas, cross_round_state

    # -- arms-bound evidence ----------------------------------------------------------
    def _evidence_from_arms(
        self,
        verdict: DiscoveryVerdict,
        head: ClaimHead,
        observations: Sequence[Any],
    ) -> EvidenceObservation | None:
        """Derive ONE ``EvidenceObservation`` for a verdict from the round's observations on
        the head's BOUND arms (focal vs reference). Returns ``None`` when both arms are
        unpopulated (no delta — absence is not evidence).

        Only ``TrustLevel.TRUSTED`` observations contribute a replicate (the QC stream is the
        sole source of trust). If a two-arm contrast cannot be formed from TRUSTED
        observations, the evidence comes back ``trusted=False`` → the band collapses to
        ``none`` → INSUFFICIENT → non-mutating (K3). Pure + deterministic (obs sorted by
        ``obs_id``; no clock, no randomness — gate K5)."""
        focal_keys, reference_keys = set(head.focal_group), set(head.reference_group)
        in_arms = [
            o
            for o in sorted(observations, key=lambda o: getattr(o, "obs_id", ""))
            if _group_key(o) in focal_keys or _group_key(o) in reference_keys
        ]
        if not in_arms:
            return None
        # A read contributes iff the QC stream marked it TRUSTED and it carries a value (a
        # measured-but-value-less observation is not a measurement).
        trusted_obs = [
            o
            for o in in_arms
            if getattr(o, "trust", None) is TrustLevel.TRUSTED
            and getattr(getattr(o, "result", None), "value", None) is not None
        ]
        focal = [o for o in trusted_obs if _group_key(o) in focal_keys]
        reference = [o for o in trusted_obs if _group_key(o) in reference_keys]
        focal_vals = [o.result.value for o in focal]
        reference_vals = [o.result.value for o in reference]

        # A contrast needs BOTH arms measured + trusted. Anything less is honestly untrusted
        # evidence (INSUFFICIENT), never a silently-dropped delta and never a manufactured one.
        contrast = bool(focal_vals) and bool(reference_vals)
        control_mean = (
            sum(reference_vals) / len(reference_vals) if reference_vals else 0.0
        )
        reps = self._focal_replicates(verdict, focal, focal_vals)
        dataset = AssayDataset(
            perturbation=verdict.hypothesis.perturbation,
            axis=verdict.hypothesis.axis,
            control_mean=control_mean,
            replicates=reps,
            validation_level=self._validation_level,
            is_wet_observation=self._is_wet_observation,
            source=f"mcl-round-arms:{head.claim_id}",
        )
        # The analysis backend (evidence only — it has no ledger handle) computes the
        # statistic; ``force_trusted`` carries the QC stream's verdict, NOT the data's
        # provenance: a simulated-but-QC-TRUSTED read is trusted evidence about a simulated
        # world, and stays labelled ``validation_level=simulation`` all the way to the delta.
        evidence = self._backend.analyse(
            verdict.hypothesis, dataset, force_trusted=contrast
        )
        n_dropped = len(in_arms) - len(trusted_obs)
        note = (
            f"{evidence.note} [arms-bound: focal={len(focal_vals)} "
            f"reference={len(reference_vals)} qc_untrusted_dropped={n_dropped}]"
        )
        return replace(evidence, note=note)

    def _focal_replicates(
        self,
        verdict: DiscoveryVerdict,
        focal: Sequence[Any],
        focal_vals: Sequence[float],
    ) -> tuple[ReplicateMeasurement, ...]:
        """The focal arm's reads as replicate measurements.

        ``replicate_kind="biological"`` (default): each read is an INDEPENDENT biological
        replicate. ``"technical"`` (M24-B ruling ③): the reads are correlated re-measures of
        ONE biological unit, so they collapse to a single biological replicate (their mean)
        plus the remaining reads recorded as technical — N technical reads never buy
        independent evidence, and the band caps at weak no matter how large |z| grows.
        ``replicate_id`` carries the REAL per-run ids (arm key + obs id), so the dataset
        fingerprint records what was actually read this round."""
        perturbation, axis = verdict.hypothesis.perturbation, verdict.hypothesis.axis

        def _rep(value: float, rid: str, kind: str) -> ReplicateMeasurement:
            return ReplicateMeasurement(
                perturbation=perturbation, axis=axis, value=value,
                replicate_id=rid, replicate_kind=kind,
            )

        ids = [f"{_group_key(o)}::{getattr(o, 'obs_id', '')}" for o in focal]
        if verdict.replicate_kind == "technical":
            if not focal_vals:
                return ()
            mean = sum(focal_vals) / len(focal_vals)
            return (_rep(mean, f"collapsed::{ids[0]}", "biological"),) + tuple(
                _rep(v, rid, "technical") for v, rid in zip(focal_vals[1:], ids[1:])
            )
        return tuple(
            _rep(v, rid, "biological") for v, rid in zip(focal_vals, ids)
        )

    # -- trust cross-check (prebuilt-evidence path) -----------------------------------
    def _trust_gated(
        self, verdict: DiscoveryVerdict, adjudicated_observations: Sequence[Any]
    ) -> EvidenceObservation:
        """Downgrade a verdict to untrusted iff the QC stream SAW its source observation and
        did NOT mark it TRUSTED. The certified stream can only remove trust, never add it."""
        obs = verdict.observation
        assert obs is not None  # prebuilt path only (decide routes arms-bound elsewhere)
        if not self._enforce_adjudicated_trust or not obs.trusted:
            return obs
        seen = {getattr(o, "obs_id", None): o for o in adjudicated_observations}
        source = seen.get(verdict.join_id())
        if source is None:
            # QC stream did not include this observation → honor B's flag (seam #1).
            return obs
        if getattr(source, "trust", None) is TrustLevel.TRUSTED:
            return obs
        # Seen and NOT trusted → force untrusted → the bridge bands it NONE → INSUFFICIENT.
        return replace(obs, trusted=False)
