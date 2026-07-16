"""``PerturbationGateCertification`` — the mcl integration seam for M27 (v0.1).

**What it lands.** The M27 baseline-gate's NEGATIVE CLAIMS: an expensive proposer that did
not significantly-and-calibratedly beat the mandatory baselines is a FIRST-CLASS negative
result (charter §4), not a leaderboard row. This adapter routes each round's
``CompetitionRoundResult.negative_claims`` (and their admitted mirrors) into the real claim
ledger through the ONE existing path.

**Zero mcl change**, by construction. ``expos.mcl._certify_round`` already calls the SEVENTH
planner-injection element (``expos.planner.certification.CertificationPolicy``):

    certification.decide(adjudicated_observations, ledger, cross_round_state,
                         round_id, knowledge_fingerprint) -> (list[ClaimDelta], state)

and then OWNS the single ``apply_claim_deltas`` mutation + the ``claim_decision`` emit. This
class CONFORMS to that protocol structurally, exactly as ``NullCertification`` /
``RegisteredFnCertification`` / M28's ``DiscoveryCertification`` do, so B injects it as
``certification=`` and nothing in mcl/planner/kernel is touched.

Because ``decide`` is the only callback mcl gives a policy, the policy — not mcl — is what
runs the round's competition: it calls the provider's neutral ``round_batches(round_id)``
hook (docs/bio_seams/M27.md SEAM 2) and the dry leg's ``compete_round``, both DETERMINISTIC
in ``round_id``, so the verdicts it certifies are bitwise the same ones mcl's ``batch_compete``
dry leg computes for that round. A caller that already has the round's result can hand it
over via ``results_by_round=`` and no competition is re-run.

**Late id binding (the M28 head-from-exp ruling, blue_to_red/154; B's 158 general rule "a
random id may only be bound after the id exists").** NOTHING is bound at construction:

  * the policy is constructed with NO claim id, NO backend list, NO round data — only a
    SEMANTIC :class:`ProposerSelector` (which proposers to certify, named by backend
    semantics + admission role, never by an id) and the provider/adapter to resolve against;
  * the round's REAL roster only exists once ``round_batches(round_id)`` + ``compete_round``
    have run — i.e. inside ``decide``. The claim id (:func:`gate_claim_id`), the head
    statement, the baseline the contrast is against, and the evidence id are all MINTED
    THERE, from the round's real verdicts. Swap the roster (a different ``backend_factory``)
    or the data regime and the same policy object certifies whatever the round actually ran.

**THE MOAT, unchanged.** This adapter produces ClaimDelta **proposals** only. The kernel gate
inside ``_certify_round`` (``apply_claim_deltas``) stays the SOLE mutator; the backends
themselves only ever emit predictions/scores (they hold no ledger handle) and this policy
only ever targets ``m27_gate_*`` claim ids — it can never touch a BIOLOGICAL claim (the
provider's ``p_*`` seed family). Certifying "knockout X moves axis A" still requires a
trusted observation on the real wet/sim path (``domains/perturbation/causal.py``, SEAM 3).

**What the claim IS, stated honestly (charter §4).** The head is a claim about a MODEL, not
about biology: "backend B beats the best baseline at predicting the cell-state response on
the retrospective held-out split". Retrospective benchmark data (``is_wet_observation=False``,
``role='benchmark_calibration'``) is the DIRECT and appropriate evidence for that claim — it
is never laundered into evidence about a cell: every head statement carries its scope + the
non-wet label, and the round's adjudicated WET observations are deliberately NOT read here
(a wet reading of this run neither supports nor refutes a benchmark-split claim). Verdicts:

  * cleared the gate, decisively  -> SUPPORTED
  * did NOT clear, decisively     -> REJECTED  (the first-class negative result)
  * indecisive evidence (|z| below the 95%-CI bar, or an empty eval set) -> INSUFFICIENT,
    which by kernel red line K3 mutates nothing yet still emits its ``claim_decision``
    event — "not enough evidence" is recorded, never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

# READ-ONLY kernel/planner imports (called, never edited). The kernel stays biology-blind:
# it sees a generic statistic dict (an effect, its SE/CI, a favourable direction) and never
# learns that "the effect" is an L2 improvement of a virtual-cell model. ``CertificationError``
# keeps the fail-loud-at-wiring error family shared with the planner. The ``CertificationPolicy``
# Protocol needs no base class (structural typing).
from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimVersionContent,
    EvidenceStrength,
    GroupSummary,
    Ledger,
    ObservationFingerprint,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    StatisticSnapshot,
    register_decision_fn,
)
from expos.planner.certification import CertificationError

if TYPE_CHECKING:  # pragma: no cover
    from domains.perturbation.competition import GateVerdict
    from expos.adapters.models.cell_state_adapter import CompetitionRoundResult

#: CrossRoundState shape mirrors ``expos.planner.certification.CrossRoundState``
#: (JSON-serializable ``{claim_id: ...}`` or None).
CrossRoundState = dict[str, Any] | None

M27_DECISION_FN_ID = "m27_baseline_gate_verdict"
M27_DECISION_FN_VERSION = "1"
M27_CRITERION_VERSION = "m27-baseline-gate-1"

#: Two-sided 95% normal quantile. The gate's significance test is a paired bootstrap 95% CI,
#: so the decisiveness bar on |z| is set to the SAME 95% level: a verdict is decisive exactly
#: when the gate's own CI would call it, never on a second, looser criterion.
_CI_Z = 1.959963984540054
_DECISIVE_ABS_Z = _CI_Z

#: A thin evaluation set cannot buy strong evidence about a model, however large |z| grows
#: (the M24-B ruling ③ discipline: correlated/insufficient units never masquerade as
#: independent ones). Below this many held-out perturbations the band caps at ``weak``.
_MIN_EVAL_POINTS = 8
#: Bands above ``moderate`` additionally require a genuinely large eval set.
_STRONG_EVAL_POINTS = 16

#: Honest, structural label of the evidence this policy builds deltas from. NOT a knob: the
#: gate is scored on ``datasets/replay/*`` retrospective benchmark/calibration rows, so a
#: caller can never flip these to claim a wet observation (charter §4 iron rule).
VALIDATION_LEVEL = "retrospective"
IS_WET_OBSERVATION = False

#: Prefix of the evidence entity id. It names the round's RETROSPECTIVE EVALUATION, and is
#: deliberately unmistakable for a kernel observation id — no wet/sim observation exists for
#: this claim, and the provenance must not pretend one does.
_EVIDENCE_ID_PREFIX = "m27-retrospective-eval"


def gate_claim_id(backend: str) -> str:
    """The ledger claim id for one proposer's baseline-gate claim.

    Stated in the POSITIVE, falsifiable direction ("...beats baseline") so the negative
    result is a REJECTED status on a real claim — the kernel's own vocabulary — rather than
    a second claim whose truth value would be double-negated. The domain's own negative-claim
    dict (``GateVerdict.negative_claim``) keeps its ``m27_gate_<backend>_not_over_baseline``
    id as the DRY-evidence record; this is its ledger head."""
    return f"m27_gate_{backend}_over_baseline"


@register_decision_fn(M27_DECISION_FN_ID, M27_DECISION_FN_VERSION)
def m27_baseline_gate_verdict(
    *, statistic: dict, power: dict, criterion_version: str
) -> ClaimDecisionStatus:
    """Reference recomputation of the M27 baseline-gate verdict from a self-sufficient
    statistic dict (K4: a third party replays the verdict from the event stream alone).

    Pure, deterministic and biology-agnostic — it sees an effect estimate, a CI lower bound
    and the frozen decision thresholds; nothing here knows what a cell is. It is also the
    SINGLE source of the verdict: :class:`PerturbationGateCertification` calls this very
    function to stamp a delta's status, so the online status and the replayed status cannot
    drift apart.

    Rule (identical to ``competition.baseline_gate``, plus the K3 decisiveness screen):
      * untrusted / empty evaluation / |z| below the decisive bar -> INSUFFICIENT;
      * improvement over the best baseline above ``min_improvement`` AND a bootstrap CI
        lower bound > 0 AND calibration not worse than the baseline by more than
        ``calibration_slack`` -> SUPPORTED;
      * otherwise -> REJECTED (the first-class negative result).
    """
    thresholds = statistic.get("decision_thresholds") or {}
    z = statistic.get("statistic_value")
    imp = statistic.get("effect_estimate")
    ci_low = statistic.get("ci_low")
    decisive = thresholds.get("decisive_abs_z", _DECISIVE_ABS_Z)
    trusted = bool(power.get("trusted", False))
    if z is None or imp is None or ci_low is None or not trusted:
        return ClaimDecisionStatus.INSUFFICIENT
    if abs(z) < decisive:
        return ClaimDecisionStatus.INSUFFICIENT
    cleared = (
        imp > thresholds.get("min_improvement", 0.0)
        and ci_low > 0.0
        and bool(thresholds.get("calibration_ok", False))
    )
    return ClaimDecisionStatus.SUPPORTED if cleared else ClaimDecisionStatus.REJECTED


def _band(*, trusted: bool, z: float | None, n_eval: int) -> EvidenceStrength:
    """Ordinal evidence band for a gate verdict. Mirrors ``analysis_backends.base.
    evidence_strength_band``'s shape with the M27 unit of evidence: a held-out
    perturbation (not a biological replicate — this claim is about a model, and calling an
    eval point a replicate would be exactly the kind of dressing-up charter §4 forbids).

    ``none`` (-> INSUFFICIENT) and the sub-threshold rungs are decided on the SAME inputs
    the registered decision_fn uses, so band and status can never disagree."""
    if not trusted or z is None or abs(z) < _DECISIVE_ABS_Z or n_eval == 0:
        return EvidenceStrength.NONE
    if n_eval < _MIN_EVAL_POINTS:
        return EvidenceStrength.WEAK  # decisive z on a thin eval set -> capped weak
    if abs(z) >= 6.0 and n_eval >= _STRONG_EVAL_POINTS:
        return EvidenceStrength.VERY_STRONG
    if abs(z) >= 4.0 and n_eval >= _STRONG_EVAL_POINTS:
        return EvidenceStrength.STRONG
    return EvidenceStrength.MODERATE


#: Admission roles a :class:`ProposerSelector` may name — the gate's own two outcomes, in
#: neutral vocabulary. ``None`` matches both.
ADMISSION_ROLES = ("admitted", "not_admitted")


@dataclass(frozen=True)
class ProposerSelector:
    """A SEMANTIC, ID-FREE way to name which of a round's competitors get certified.

    The M27 twin of M28's ``ArmSelector``. A selector names competitors by semantics known
    BEFORE the round runs anything:

      * ``names`` — backend names (``("knn_response",)``); ``()`` (default) matches every
        non-baseline proposer the round ACTUALLY ran;
      * ``admission`` — ``"admitted"`` / ``"not_admitted"`` (the gate's own outcome roles);
        ``None`` (default) matches both, so the ledger records the positive and the negative
        results symmetrically.

    It never names a claim id, a backend instance or a run id: those are resolved from THIS
    round's real ``GateVerdict``s inside ``decide``. Baselines carry no gate verdict by
    construction (they are what the gate gates AGAINST), so they are never selectable."""

    names: tuple[str, ...] = ()
    admission: str | None = None

    def __post_init__(self) -> None:
        if self.admission is not None and self.admission not in ADMISSION_ROLES:
            raise CertificationError(
                f"ProposerSelector admission={self.admission!r} must be one of "
                f"{list(ADMISSION_ROLES)} or None (both)"
            )

    def resolve(self, result: "CompetitionRoundResult") -> list["GateVerdict"]:
        """THIS round's real verdicts matching the selector, deterministically ordered by
        backend name. Resolution happens against the round's actual roster — a name the
        round did not run simply does not match (an empty selection is a legal quiet round,
        not an error: the honest surface-absent discipline)."""
        want = set(self.names)
        picked = [
            v
            for v in result.verdicts
            if (not want or v.backend in want)
            and (
                self.admission is None
                or (self.admission == "admitted") is bool(v.admitted)
            )
        ]
        return sorted(picked, key=lambda v: v.backend)


class PerturbationGateCertification:
    """``CertificationPolicy`` adapter — the mcl entry point for the M27 baseline-gate.

    Wire it as ``run_mcl_loop(..., certification=PerturbationGateCertification(provider,
    adapter))``; ``_certify_round`` lands what ``decide`` proposes. Parameters:

      * ``provider`` — the domain's ``PerturbationScreenProvider``. MUST expose the neutral
        ``round_batches(round_id)`` hook; a provider without it fails LOUDLY here at wiring
        time, never mid-round (the birth-time-governance discipline).
      * ``adapter`` — the dry ``CellStatePerturbationAdapter`` (defaults to a fresh one with
        the standard roster). The SAME object mcl's ``batch_compete`` leg dispatches to.
      * ``selector`` — WHICH of the round's proposers to certify (semantic, id-free).
        Default: every proposer the round ran, admitted or not. Pass
        ``ProposerSelector(admission="not_admitted")`` to land ONLY the negatives.
      * ``results_by_round`` — OPTIONAL ``{round_id: CompetitionRoundResult}`` already
        computed by the caller (B's dry leg has one in hand). Provided rounds are certified
        from it and no competition re-runs; a round that is absent falls back to the
        provider hook. With no provider AND no staged result for a round, the policy fails
        loudly rather than certify nothing silently.
      * ``run_fingerprint`` — recorded on each delta's provenance activity (K4 side-info).
      * ``seed`` — threaded into ``round_batches`` (deterministic split).
      * ``min_improvement`` / ``calibration_slack`` — the frozen gate criterion; they ride
        into every delta's ``decision_thresholds`` so the verdict is replayable.

    There is deliberately no ``is_wet_observation`` knob: see :data:`IS_WET_OBSERVATION`.
    """

    name = "perturbation_gate_certification"

    def __init__(
        self,
        provider: Any | None = None,
        adapter: Any | None = None,
        *,
        selector: ProposerSelector | None = None,
        results_by_round: Mapping[int, "CompetitionRoundResult"] | None = None,
        run_fingerprint: str = "m27-mcl",
        seed: int = 0,
        min_improvement: float = 0.05,
        calibration_slack: float = 0.15,
    ) -> None:
        if provider is None and not results_by_round:
            raise CertificationError(
                "PerturbationGateCertification needs either a provider exposing the "
                "round_batches(round_id) hook (docs/bio_seams/M27.md SEAM 2) or a staged "
                "results_by_round map; it will not certify an empty round silently"
            )
        if provider is not None and not callable(getattr(provider, "round_batches", None)):
            raise CertificationError(
                f"provider {type(provider).__name__} exposes no round_batches(round_id) "
                "hook, so this round's (train_batch, held_batch) — with the reference "
                "deltas the baseline-gate scores against — cannot be built. Gate on the "
                "HOOK, not on a domain name (docs/bio_seams/M27.md SEAM 2)"
            )
        if provider is not None:
            self._assert_non_wet(provider)
        if adapter is None and provider is not None:
            from expos.adapters.models.cell_state_adapter import (
                CellStatePerturbationAdapter,
            )

            adapter = CellStatePerturbationAdapter()
        self._provider = provider
        self._adapter = adapter
        self._selector = selector or ProposerSelector()
        self._staged = dict(results_by_round or {})
        self._run_fingerprint = run_fingerprint
        self._seed = int(seed)
        self._min_improvement = float(min_improvement)
        self._calibration_slack = float(calibration_slack)
        self._cache: dict[int, "CompetitionRoundResult"] = {}

    # -- wiring-time guards ------------------------------------------------------------
    @staticmethod
    def _assert_non_wet(provider: Any) -> None:
        """Refuse at wiring time if the provider's batch source claims to be a wet
        observation. The dataset layer already refuses to construct such a provenance; this
        is the third lock, on the object that turns those rows into ledger deltas."""
        prov_fn = getattr(provider, "round_batches_provenance", None)
        if not callable(prov_fn):
            return
        provenance = prov_fn() or {}
        if provenance.get("is_wet_observation"):
            raise CertificationError(
                "round_batches source claims is_wet_observation=True; the baseline-gate is "
                "scored on retrospective benchmark/calibration data and its claims are "
                "claims about MODELS. Replay may never enter as this run's wet observation "
                "(charter §4 iron rule)"
            )

    # -- the round's competition (resolved, never prebuilt) ----------------------------
    def round_result(self, round_id: int) -> "CompetitionRoundResult":
        """THIS round's real ``CompetitionRoundResult``: the staged one when the caller has
        it, else built HERE from ``provider.round_batches(round_id)`` ->
        ``adapter.compete_round(train, held, round_index=round_id)``. Deterministic in
        ``round_id`` (K5), memoized per round, and public so B / an audit can read exactly
        which roster and verdicts a round's deltas came from."""
        if round_id in self._staged:
            return self._staged[round_id]
        if round_id in self._cache:
            return self._cache[round_id]
        if self._provider is None:
            raise CertificationError(
                f"round {round_id}: no staged CompetitionRoundResult and no provider to "
                "build the round's batches from"
            )
        train_batch, held_batch = self._provider.round_batches(round_id, seed=self._seed)
        result = self._adapter.compete_round(
            train_batch,
            held_batch,
            round_index=round_id,
            min_improvement=self._min_improvement,
            calibration_slack=self._calibration_slack,
        )
        self._cache[round_id] = result
        return result

    def decide(
        self,
        adjudicated_observations: Sequence[Any],
        ledger: Ledger,
        cross_round_state: CrossRoundState,
        round_id: int,
        knowledge_fingerprint: str,
    ) -> tuple[list[ClaimDelta], CrossRoundState]:
        """Turn THIS round's baseline-gate verdicts into ClaimDelta PROPOSALS (no mutation).

        Matches the ``CertificationPolicy.decide`` signature exactly, so ``mcl._certify_
        round`` calls it and then owns ``apply_claim_deltas`` + the ``claim_decision`` emit.
        ``knowledge_fingerprint`` (the run's REAL compiled-knowledge fingerprint) is threaded
        into every delta's provenance as ``consumed_knowledge_fingerprint``, so the K4 chain
        closes against B's ledger rather than a domain-local projection.

        ``adjudicated_observations`` is intentionally UNUSED: a gate claim is a claim about a
        model's accuracy on a retrospective held-out split, which this run's wet readings
        neither support nor refute. Reading them here would be exactly the laundering charter
        §4 forbids — so the wet stream cannot manufacture model evidence, and the model
        stream cannot manufacture wet evidence.

        One delta per selected proposer (ids are unique per backend, so the K-C "one delta
        per target claim" invariant holds). The criterion is stateless, so the cross-round
        state passes through untouched — an accumulated e-product across rounds would be a
        lie here: successive rounds re-score the SAME retrospective dataset (a growing train
        split against a FIXED held-out split), so their evidence is correlated, not
        independent."""
        result = self.round_result(round_id)
        deltas = [
            self._delta(v, result, round_id, knowledge_fingerprint)
            for v in self._selector.resolve(result)
        ]
        return deltas, cross_round_state

    # -- delta construction ------------------------------------------------------------
    def _delta(
        self,
        verdict: "GateVerdict",
        result: "CompetitionRoundResult",
        round_id: int,
        knowledge_fingerprint: str,
    ) -> ClaimDelta:
        """One verdict -> one proposed ClaimDelta. The claim id + statement are minted HERE,
        from the round's REAL verdict (which backend, against which baseline) — never
        prebuilt outside the run."""
        statistic = self._statistic(verdict, result)
        stat_dict = statistic.model_dump(mode="json")
        power = self._power(verdict, result)
        # The registered reference fn IS the verdict: online status and replayed status are
        # one function, so the event stream can never disagree with what landed (K4).
        status = m27_baseline_gate_verdict(
            statistic=stat_dict, power=power, criterion_version=M27_CRITERION_VERSION
        )
        band = _band(
            trusted=bool(power["trusted"]),
            z=statistic.statistic_value,
            n_eval=int(power["n_eval"]),
        )
        if (band is EvidenceStrength.NONE) != (status is ClaimDecisionStatus.INSUFFICIENT):
            raise CertificationError(  # pragma: no cover - defensive; both read one input set
                f"round {round_id}: band {band.value} and status {status.value} disagree "
                f"for {verdict.backend!r} (the K3 'no evidence => insufficient' invariant)"
            )
        provenance = ProvenanceSnapshot(
            usage=ProvenanceUsage(
                observations=(self._evidence_ref(verdict, result, round_id),),
                consumed_knowledge_fingerprint=knowledge_fingerprint,
            ),
            activity=ProvenanceActivity(
                decision_fn_id=M27_DECISION_FN_ID,
                decision_fn_version=M27_DECISION_FN_VERSION,
                criterion_version=M27_CRITERION_VERSION,
                run_fingerprint=self._run_fingerprint,
            ),
            statistic=statistic,
        )
        new_content = (
            None
            if status is ClaimDecisionStatus.INSUFFICIENT
            else ClaimVersionContent(statement=self._statement(verdict), status=status)
        )
        return ClaimDelta(
            target_claim_id=gate_claim_id(verdict.backend),
            status=status,
            new_content=new_content,
            evidence_strength=band,
            provenance=provenance,
        )

    def _statement(self, verdict: "GateVerdict") -> str:
        """The claim, in the positive falsifiable direction, SCOPED (bio_refs §1: a negative
        result is only meaningful inside its context boundary) and carrying its honest
        validation label — so no reader of the ledger can mistake it for a wet finding."""
        scope = "retrospective held-out split"
        prov_fn = getattr(self._provider, "round_batches_provenance", None)
        if callable(prov_fn):
            scope = (prov_fn() or {}).get("scope", scope)
        return (
            f"virtual-cell backend '{verdict.backend}' significantly and calibratedly beats "
            f"baseline '{verdict.beat_baseline}' at predicting the cell-state response on "
            f"the held-out split [scope: {scope}] "
            f"(validation_level={VALIDATION_LEVEL}; is_wet_observation={IS_WET_OBSERVATION}; "
            "benchmark/calibration evidence about a MODEL, not a wet observation of a cell)"
        )

    def _statistic(
        self, verdict: "GateVerdict", result: "CompetitionRoundResult"
    ) -> StatisticSnapshot:
        """The self-sufficient statistic record. Domain-NEUTRAL by construction: an effect
        (the paired mean L2 improvement over the best baseline), its SE recovered from the
        bootstrap CI, the z it implies, the two contrast groups, and the frozen thresholds.
        The kernel performs no arithmetic on any of it."""
        cand = result.scores.get(verdict.backend)
        base = result.scores.get(verdict.beat_baseline)
        imp, ci_low = float(verdict.l2_improvement), float(verdict.ci_low)
        # se from the paired bootstrap's 95% lower bound (imp - z_{.975}*se = ci_low). A
        # degenerate/absent spread leaves z undefined -> band none -> INSUFFICIENT (never a
        # divide-by-zero, never a manufactured certainty).
        spread = imp - ci_low
        se = spread / _CI_Z if spread > 0.0 else None
        z = (imp / se) if se else None
        groups = tuple(
            GroupSummary(group=s.backend, n=int(s.n_eval), mean=float(s.l2_mean))
            for s in (cand, base)
            if s is not None
        )
        return StatisticSnapshot(
            test_method="paired-bootstrap-l2-improvement-over-baseline",
            statistic_name="z",
            statistic_value=z,
            effect_estimate=imp,
            effect_se=se,
            ci_low=ci_low,
            favorable_direction="higher",  # a POSITIVE improvement supports the claim
            # Each held-out perturbation is one evaluation unit and they are distinct rows
            # of the split; the paired bootstrap resamples exactly those units.
            independence_assumed=True,
            effect_unit="mean_per_perturbation_l2_of_cell_state_delta",
            per_group=groups,
            seed=0,  # the paired bootstrap's fixed seed (deterministic replay, K5)
            decision_thresholds={
                "min_improvement": self._min_improvement,
                "calibration_slack": self._calibration_slack,
                "decisive_abs_z": _DECISIVE_ABS_Z,
                # The calibration limb of the gate, recorded as the criterion input it is:
                # "beat the baseline" is not enough — it must also stay calibrated.
                "calibration_ok": bool(verdict.calibration_ok),
                "baseline": verdict.beat_baseline,
                "candidate_calibration_error": (
                    float(cand.calibration_error) if cand is not None else None
                ),
                "baseline_calibration_error": (
                    float(base.calibration_error) if base is not None else None
                ),
                "n_eval_for_moderate_band": _MIN_EVAL_POINTS,
            },
        )

    def _power(
        self, verdict: "GateVerdict", result: "CompetitionRoundResult"
    ) -> dict[str, Any]:
        """The reference fn's second input (the qc precedent: derived side-info beside the
        statistic). ``trusted`` here means "the evaluation actually happened": a non-empty
        held-out split, scored against real reference deltas by both arms. It is NOT a claim
        that the DATA is wet — that label lives in the statement + :data:`VALIDATION_LEVEL`
        and is hard-False."""
        cand = result.scores.get(verdict.backend)
        base = result.scores.get(verdict.beat_baseline)
        n_eval = int(getattr(cand, "n_eval", 0) or 0)
        return {
            "trusted": bool(cand is not None and base is not None and n_eval > 0),
            "n_eval": n_eval,
            "validation_level": VALIDATION_LEVEL,
            "is_wet_observation": IS_WET_OBSERVATION,
        }

    def _evidence_ref(
        self, verdict: "GateVerdict", result: "CompetitionRoundResult", round_id: int
    ) -> ObservationFingerprint:
        """The PROV 'entity used': this round's retrospective evaluation of one proposer.

        The id is minted from the round's real content (it cannot exist before the round
        does) and is prefixed :data:`_EVIDENCE_ID_PREFIX` so it can never be mistaken in an
        audit for a kernel observation id — no wet/sim observation underlies this claim and
        the provenance says so. The content fingerprint folds the batch source (dataset
        bytes + provenance + scope + split), BOTH arms' weight fingerprints and the round's
        scores, so a re-fit on different data or a changed replay source flips it."""
        h = hashlib.sha256()
        source_fn = getattr(self._provider, "round_batches_fingerprint", None)
        h.update(str(source_fn() if callable(source_fn) else "unknown-source").encode())
        cand = result.scores.get(verdict.backend)
        base = result.scores.get(verdict.beat_baseline)
        h.update(
            json.dumps(
                {
                    "round_index": int(result.round_index),
                    "candidate": verdict.backend,
                    "baseline": verdict.beat_baseline,
                    "candidate_weights": result.backend_fingerprints.get(verdict.backend),
                    "baseline_weights": result.backend_fingerprints.get(
                        verdict.beat_baseline
                    ),
                    "candidate_l2_mean": float(cand.l2_mean) if cand else None,
                    "baseline_l2_mean": float(base.l2_mean) if base else None,
                    "n_eval": int(cand.n_eval) if cand else 0,
                    "l2_improvement": float(verdict.l2_improvement),
                    "ci_low": float(verdict.ci_low),
                    "validation_level": VALIDATION_LEVEL,
                    "is_wet_observation": IS_WET_OBSERVATION,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return ObservationFingerprint(
            obs_id=f"{_EVIDENCE_ID_PREFIX}:r{round_id}:{verdict.backend}",
            content_fingerprint="sha256:" + h.hexdigest(),
        )
