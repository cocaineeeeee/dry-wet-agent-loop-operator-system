"""Round-end Certification policy (M17 K-C) — the SEVENTH planner-injection element.

Where ``planner.promotion`` (the sixth element) decides which dry candidates earn
a wet well, ``certification`` closes the M17 arrow: at round end it turns the
round's adjudicated wet observations into a list of ``kernel.claims.ClaimDelta``
— proposed, provenance-carrying claim-ledger mutations — which the mcl round-end
hook lands via ``apply_claim_deltas`` so the UPDATED ledger re-steers the agent
next round (control loop -> scientific knowledge loop, docs/M17_KNOWLEDGE_FEEDBACK
§0). This is the runtime instantiation of the Certification Policy layer (v1.1
four-layer split, letters 062/063/072).

Symmetric with ``promotion`` by design (letter 072 §"注入位"):

  * ``NullCertification`` — the DEFAULT seventh element for every existing arm.
    ``decide()`` returns ``[]``: no certification mechanism is engaged, no
    ClaimDelta is produced, so the mcl round-end hook applies an empty batch (the
    ledger is frozen) and emits ZERO ``claim_decision`` events. A non-M17 run is
    byte-for-byte unchanged — the M16 regression is that trivial (mirrors
    ``NullPromotion`` returning ``None`` / the ``learning_weight_assigned``
    surface-absent discipline: a base policy never sets the surface -> zero
    mode-branch).
  * ``RegisteredFnCertification`` — wraps a decision function resolved through the
    SHARED ``kernel.claims.DECISION_FN_REGISTRY`` (the same membership authority
    the offline ``scripts/claim_compiler.py`` registers into — the online path
    does not bypass offline governance, letter 072 red line 1). The id + version
    are resolved AT CONSTRUCTION: an unregistered id or a version mismatch fails
    loudly there, never silently at round end. ``decide()`` calls the registered
    fn as the per-round verdict CRITERION (statistic/power -> ClaimDecisionStatus)
    and packages the verdict into a ``ClaimDelta`` per target claim, carrying a
    frozen ``ProvenanceSnapshot`` (K1/K4 audit hook).

Pure-function discipline (gate K5, mirrors ``promotion.decide``): ``decide()``
does NO I/O, NO store access, NO clock, NO randomness. Emission of the
``claim_decision`` event and the ledger apply are wired by the mcl round-end hook
(``expos.mcl``), NOT here — exactly as ``emit_promotion_decision`` /
``emit_knowledge_updated`` keep the emission POINT out of the pure decider.

K-C honesty boundary: ``NullCertification`` / ``RegisteredFnCertification`` do NOT
aggregate statistics (that is K-B, ``expos/qc/certification_stats.py``). They build
the input observation provenance and a MINIMAL (empty) statistic snapshot, and hand
empty statistic/power dicts to the criterion — which is exactly what the honest-null
reference fn (``reference_round_certification`` -> insufficient) consumes.

K-F (this file's final element, ``AggregatedCertification``) wires K-B's real
aggregator in: its ``decide`` calls ``qc.certification_stats.aggregate_round`` per
target claim over the round's TRUSTED wet observations, threading the live
``consumed_knowledge_fingerprint`` and the per-claim cross-round ``RoundState``
through the ``cross_round_state`` seam. The full arrow — TRUSTED wet obs ->
e-value adjudication -> ClaimDelta -> ledger update -> next-round re-steer — closes
through it.

Cross-round state seam (K-F): ``decide`` returns ``(deltas, new_cross_round_state)``
— PURE (state in, state out; no I/O). The state travels as a JSON-serializable dict
(``{claim_id: RoundState-as-json}``) so the mcl round-end hook persists it in the
checkpoint alongside the claim ledger (I4 resume; the accumulated e-product survives
resume bitwise). ``NullCertification`` / ``RegisteredFnCertification`` are stateless
and pass the state through unchanged.

Layering (public red line EXP007): ``planner/`` may import ``kernel/`` and the
peer ``qc/`` aggregator (EXP007 forbids only KERNEL importing upper packages;
``qc.certification_stats`` itself imports only ``kernel`` + stdlib, so
planner->qc is one-directional and acyclic). This module imports no adapters, no
``loop``/``mcl``/``agent``. EXP001: it names no truth identifier — the reader's
hidden face never reaches this layer (the K-B aggregator is itself truth-blind).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from expos.errors import ExposError
from expos.kernel.claims import (
    DECISION_FN_REGISTRY,
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimVersionContent,
    EvidenceStrength,
    ObservationFingerprint,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    StatisticSnapshot,
    decision_fn_deny_reason,
)
from expos.qc.certification_stats import (
    E_VALUE_CERTIFICATION_FN_ID,
    E_VALUE_CERTIFICATION_FN_VERSION,
    AggregationConfig,
    ClaimHead,
    RoundState,
    aggregate_round,
)
from expos.qc.replicate_collapse import (
    REPLICATE_KINDS,
    collapse_technical_replicates,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime coupling
    from expos.kernel.claims import Ledger
    from expos.kernel.objects import ObservationObject

#: The cross-round state that threads through ``decide`` (the ``cross_round_state``
#: seam): a JSON-serializable ``{claim_id: RoundState-as-json}`` mapping, or None on
#: the very first round. Kept JSON-shaped (not raw ``RoundState`` objects) so the mcl
#: checkpoint persists it verbatim, exactly like the claim-ledger snapshot (I4).
CrossRoundState = dict[str, Any] | None


class CertificationError(ExposError):
    """Construction-time governance failure for the online certification path.

    user_facing=False: an unregistered / version-mismatched decision_fn wired into
    a certification policy is a governance-red-line violation (a mis-wiring of the
    run, never a domain error), so it surfaces loudly at construction — the
    ``fail-loud-at-construction, not-at-round-end`` discipline (letter 072)."""

    user_facing = False


def _observation_fingerprint(obs: "ObservationObject") -> str:
    """Content fingerprint of one adjudicated observation (K1 substitution audit).

    A deterministic sha256 over the observation's identity + measured result, so a
    re-derivation can detect that a DIFFERENT reading was substituted for the same
    obs_id. Purely a provenance bookkeeping hash — NOT a statistic (K-C does not
    aggregate; that is K-B)."""
    basis = {
        "obs_id": obs.obs_id,
        "metric": obs.result.metric,
        "value": obs.result.value,
        "trust": obs.trust.value,
    }
    payload = json.dumps(
        basis, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class CertificationPolicy(Protocol):
    """The seventh planner-injection element. ``decide()`` returns a
    ``(deltas, new_cross_round_state)`` pair: ``([], state)`` when no certification
    mechanism is engaged (NullCertification), one ``ClaimDelta`` per adjudicated
    target claim plus the pass-through state when the criterion is a stateless fn
    (RegisteredFnCertification), or the real per-claim aggregation deltas plus the
    UPDATED cross-round state when the K-B aggregator is wired (AggregatedCertification).

    The ``(deltas, state)`` return (K-F seam extension over K-C's ``list[ClaimDelta]``)
    keeps ``decide`` PURE: the per-claim ``RoundState`` accumulators enter via
    ``cross_round_state`` and leave via the returned state — no store, clock or
    randomness. The mcl round-end hook persists the returned state in the checkpoint
    (I4 resume) and feeds it back next round."""

    name: str

    def decide(
        self,
        adjudicated_observations: Sequence["ObservationObject"],
        ledger: "Ledger",
        cross_round_state: CrossRoundState,
        round_id: int,
        knowledge_fingerprint: str,
    ) -> tuple[list[ClaimDelta], CrossRoundState]: ...


class NullCertification:
    """Default seventh element for every existing arm: no certification, no
    ClaimDelta, no ``claim_decision`` event, zero behaviour change. ``decide()``
    returns ``[]`` so the mcl round-end hook applies an empty batch (ledger frozen)
    and emits nothing — the surface-absent discipline of ``NullPromotion`` /
    ``learning_weight_assigned`` (a base policy never sets the surface -> zero
    mode-branch, the M16 regression twin)."""

    name = "null_certification"

    def decide(
        self,
        adjudicated_observations: Sequence["ObservationObject"],
        ledger: "Ledger",
        cross_round_state: CrossRoundState,
        round_id: int,
        knowledge_fingerprint: str,
    ) -> tuple[list[ClaimDelta], CrossRoundState]:
        # Stateless: zero deltas, the cross-round state passed through untouched
        # (a base policy accumulates nothing — the surface-absent discipline).
        return [], cross_round_state


class RegisteredFnCertification:
    """Certification policy backed by a decision function registered in the SHARED
    ``kernel.claims.DECISION_FN_REGISTRY`` (online governance = offline governance,
    letter 072 red line 1).

    The decision_fn id + version are resolved AT CONSTRUCTION — an unregistered id
    or a version mismatch raises ``CertificationError`` here, never silently at
    round end. ``decide()`` calls the registered fn as the per-round verdict
    CRITERION (``fn(*, statistic, power, criterion_version) -> ClaimDecisionStatus``,
    the ``reference_round_certification`` shape) and packages each verdict into a
    ``ClaimDelta`` carrying a frozen ``ProvenanceSnapshot``.

    Parameters:
      * ``decision_fn_id`` / ``decision_fn_version`` — resolved against the shared
        registry at construction (fail-loud).
      * ``criterion_version`` — the statistical-criterion version recorded on the
        provenance activity (K4 audit side-info).
      * ``target_claim_ids`` — the claims to adjudicate; ``None`` => every current
        effective head in the ledger (deterministic sorted order).
      * ``evidence_strength`` — the band stamped on every produced delta. K-C
        placeholder: the real band is the K-B power-table's product (K-F wires it).
        Defaults to ``NONE`` (the honest "no strength asserted yet" band); the
        strength-monotonicity gate reads it against the target head.
    """

    name = "registered_fn_certification"

    def __init__(
        self,
        decision_fn_id: str,
        decision_fn_version: str,
        *,
        criterion_version: str = "v1",
        target_claim_ids: Sequence[str] | None = None,
        evidence_strength: EvidenceStrength = EvidenceStrength.NONE,
    ) -> None:
        # Fail-loud-at-construction (letter 072): resolve the decision_fn against
        # the shared registry now, so a mis-wired run dies here — not mid-round.
        deny = decision_fn_deny_reason(decision_fn_id, decision_fn_version)
        if deny is not None:
            raise CertificationError(
                f"certification decision_fn {decision_fn_id!r} v{decision_fn_version} "
                f"is not usable ({deny}); it must be registered in the shared "
                "DECISION_FN_REGISTRY with a matching version (online path does not "
                "bypass offline governance)"
            )
        self._fn = DECISION_FN_REGISTRY[decision_fn_id].fn
        self._fn_id = decision_fn_id
        self._fn_version = decision_fn_version
        self._criterion_version = criterion_version
        self._target_claim_ids = (
            None if target_claim_ids is None else tuple(target_claim_ids)
        )
        self._evidence_strength = evidence_strength

    def decide(
        self,
        adjudicated_observations: Sequence["ObservationObject"],
        ledger: "Ledger",
        cross_round_state: CrossRoundState,
        round_id: int,
        knowledge_fingerprint: str,
    ) -> tuple[list[ClaimDelta], CrossRoundState]:
        # Target claims in an explicit deterministic order (K5): the provided set,
        # else every current effective head in the ledger.
        if self._target_claim_ids is not None:
            targets = sorted(set(self._target_claim_ids))
        else:
            targets = sorted(ledger.effective_statuses())

        # Input-observation provenance (K1/K4): id + content fingerprint, in a
        # deterministic obs_id order. NOT a statistic (K-C does not aggregate).
        obs_fingerprints = tuple(
            ObservationFingerprint(
                obs_id=obs.obs_id,
                content_fingerprint=_observation_fingerprint(obs),
            )
            for obs in sorted(adjudicated_observations, key=lambda o: o.obs_id)
        )

        # K-C honesty boundary: an EMPTY statistic snapshot / criterion inputs. The
        # honest-null reference fn ignores them; K-B's real aggregator (K-F wiring)
        # fills the snapshot and the criterion inputs. K-C does no statistics.
        statistic = StatisticSnapshot()
        criterion_statistic: dict[str, Any] = {}
        criterion_power: dict[str, Any] = {}

        deltas: list[ClaimDelta] = []
        for claim_id in targets:
            head = ledger.head(claim_id)
            status: ClaimDecisionStatus = self._fn(
                statistic=criterion_statistic,
                power=criterion_power,
                criterion_version=self._criterion_version,
            )
            # insufficient proposes no head (type-level isolation, K3); a mutating
            # verdict carries the new claim version content (statement inherited
            # from the current head, or the claim id when the claim is new).
            new_content = (
                None
                if status is ClaimDecisionStatus.INSUFFICIENT
                else ClaimVersionContent(
                    statement=(head.statement if head is not None else claim_id),
                    status=status,
                )
            )
            provenance = ProvenanceSnapshot(
                usage=ProvenanceUsage(
                    observations=obs_fingerprints,
                    consumed_knowledge_fingerprint=knowledge_fingerprint,
                ),
                activity=ProvenanceActivity(
                    decision_fn_id=self._fn_id,
                    decision_fn_version=self._fn_version,
                    criterion_version=self._criterion_version,
                ),
                statistic=statistic,
            )
            deltas.append(
                ClaimDelta(
                    target_claim_id=claim_id,
                    status=status,
                    new_content=new_content,
                    evidence_strength=self._evidence_strength,
                    provenance=provenance,
                )
            )
        # Stateless criterion: pass the cross-round state through unchanged.
        return deltas, cross_round_state


def _group_key(obs: "ObservationObject") -> str | None:
    """The PUBLIC arm key of an observation (control id for a control, else its
    candidate id) — the same join key K-B's aggregator uses. No truth field is
    read. Kept local so the planner layer does not reach into qc-private helpers."""
    return obs.control_id if obs.is_control else obs.cand_id


class AggregatedCertification:
    """K-F: the certification policy that wires K-B's REAL statistical aggregator
    into the seventh-element seam. ``decide`` runs ``aggregate_round`` for each
    target claim over the round's TRUSTED wet observations, accumulating the
    cross-round e-process/effect state through ``cross_round_state`` and returning
    one ``ClaimDelta`` per claim plus the UPDATED state.

    Every produced delta is stamped (by the aggregator) with the registered K-B
    decision fn ``e_value_round_certification`` v1, so it passes the online
    governance gate exactly like the offline compiler's verdicts. The id + version
    are re-verified against the SHARED ``DECISION_FN_REGISTRY`` AT CONSTRUCTION
    (fail-loud, letter 072 red line 1) — a registry that somehow lost the K-B fn
    dies here, never mid-round.

    Parameters:
      * ``claim_heads`` — the ``ClaimHead`` specs (claim id + statement + stated
        favourable direction + the focal/reference arm keys). These decide WHICH
        claim(s) are certified and WHICH observations (by public arm key = cand_id /
        control_id) form each arm. The RESPONSE covariate is the observation's
        measured value (``result.value``, metric ``solvent_response``) — fixed by
        the aggregator; the arm covariate is the public group key. This mirrors how
        K-B's tests build a two-arm contrast from wet observations.
      * ``config`` — the ``AggregationConfig`` (alpha / w_min / r_min / floor / rho /
        seed / permutations / run_fingerprint). ``consumed_knowledge_fingerprint``
        is IGNORED here and OVERWRITTEN per-round with the live
        ``knowledge_fingerprint`` (K4 chain closure), so the caller need not set it.
      * ``replicate_kind`` — M24 bio ruling ③ (additive-optional; None/``biological``
        => byte-identical, the chemistry regression anchor). When ``"technical"``, the
        round's observations are collapsed by their PUBLIC arm key (each biological
        unit's re-reads -> ONE observation) in the qc layer BEFORE ``aggregate_round``,
        so N correlated technical reads are not counted as N independent evidence units
        (which would OVER-estimate the e-product / fake a decisive verdict). The
        aggregator stays domain-agnostic and byte-identical; only the independent-unit
        count fed to it changes. ``reducer`` (mean/median) is the collapse central value.

    Purity (gate K5): ``decide`` does no I/O — the per-claim ``RoundState`` enters as
    JSON via ``cross_round_state`` and leaves as JSON in the returned state. The mcl
    hook persists that dict in the checkpoint (I4). The technical-replicate collapse is
    itself a pure, deterministic upstream transform (no clock / no randomness)."""

    name = "aggregated_certification"

    def __init__(
        self,
        claim_heads: Sequence[ClaimHead],
        *,
        config: AggregationConfig | None = None,
        replicate_kind: str | None = None,
        reducer: str = "mean",
    ) -> None:
        if not claim_heads:
            raise CertificationError(
                "AggregatedCertification requires at least one ClaimHead target"
            )
        # Fail-loud on an unknown replicate_kind (mirrors the domain-schema Literal): a
        # typo must die at wiring time, never silently skip the collapse at round end.
        if replicate_kind is not None and replicate_kind not in REPLICATE_KINDS:
            raise CertificationError(
                f"AggregatedCertification got replicate_kind={replicate_kind!r}; "
                f"must be None or one of {sorted(REPLICATE_KINDS)}"
            )
        self._replicate_kind = replicate_kind
        self._reducer = reducer
        # Fail-loud-at-construction (letter 072 red line 1): the aggregator stamps
        # every delta with the K-B fn id/version; verify that pairing is registered
        # in the SHARED registry now, so a governance-broken run dies at wiring time.
        deny = decision_fn_deny_reason(
            E_VALUE_CERTIFICATION_FN_ID, E_VALUE_CERTIFICATION_FN_VERSION
        )
        if deny is not None:
            raise CertificationError(
                f"K-B decision_fn {E_VALUE_CERTIFICATION_FN_ID!r} "
                f"v{E_VALUE_CERTIFICATION_FN_VERSION} is not usable ({deny}); the "
                "aggregator's deltas would be denied by the online governance gate"
            )
        # Deterministic per-claim order (K5); reject duplicate claim ids (the mcl
        # emit pairing assumes one delta per target claim id).
        heads = tuple(sorted(claim_heads, key=lambda h: h.claim_id))
        ids = [h.claim_id for h in heads]
        if len(set(ids)) != len(ids):
            raise CertificationError(
                f"AggregatedCertification got duplicate claim ids in claim_heads: {ids}"
            )
        self._heads = heads
        self._config = config or AggregationConfig()

    def decide(
        self,
        adjudicated_observations: Sequence["ObservationObject"],
        ledger: "Ledger",
        cross_round_state: CrossRoundState,
        round_id: int,
        knowledge_fingerprint: str,
    ) -> tuple[list[ClaimDelta], CrossRoundState]:
        prior_state = dict(cross_round_state or {})
        observations = list(adjudicated_observations)
        # M24 bio ruling ③: when the domain declares TECHNICAL replicates, collapse
        # each biological unit's correlated re-reads into ONE observation BEFORE the
        # compiler sees them, feeding it the correct independent-unit count. Grouped by
        # the PUBLIC arm key (_group_key), the SAME key the aggregator joins arms on, so
        # arm membership is preserved and the qc collapse stays domain-agnostic.
        # None / "biological" => identity (independent evidence; byte-identical anchor).
        if self._replicate_kind == "technical":
            observations = collapse_technical_replicates(
                observations,
                biological_unit_key=_group_key,
                reducer=self._reducer,
            )
        # Thread the LIVE consumed-knowledge fingerprint into the config (K4: the
        # adjudication is computed against THIS round's compiled knowledge). The
        # config is frozen, so model_copy produces the per-round variant.
        cfg = self._config.model_copy(
            update={"consumed_knowledge_fingerprint": knowledge_fingerprint}
        )

        new_state: dict[str, Any] = dict(prior_state)
        deltas: list[ClaimDelta] = []
        for head in self._heads:
            # Skip a claim whose two arms are BOTH unpopulated this round (e.g. a
            # zero-promotion / wet-skipped round): aggregating an empty contrast
            # would fold a p=1 (e=0) round and zero the accumulated e-product. An
            # honest glue carries the prior state forward untouched and emits no
            # delta — absence of a measurement is not evidence (gate K3).
            keyset = set(head.focal_group) | set(head.reference_group)
            if not any(_group_key(o) in keyset for o in observations):
                continue
            prior_dict = prior_state.get(head.claim_id)
            prior = RoundState.model_validate(prior_dict) if prior_dict else None
            delta, aggregate = aggregate_round(observations, head, cfg, prior)
            deltas.append(delta)
            # Persist the updated per-claim state as JSON (checkpoint-shaped, I4).
            new_state[head.claim_id] = aggregate.state.model_dump(mode="json")

        return deltas, new_state
