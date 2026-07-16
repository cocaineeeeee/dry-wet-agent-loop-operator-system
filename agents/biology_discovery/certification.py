"""``DiscoveryCertification`` — the mcl integration seam for M28 (v0.1).

B's ``expos.mcl._certify_round`` calls a ``CertificationPolicy`` (the SEVENTH planner-
injection element, ``expos.planner.certification.CertificationPolicy``):

    certification.decide(adjudicated_observations, ledger, cross_round_state,
                         round_id, knowledge_fingerprint) -> (list[ClaimDelta], state)

and then OWNS the single ``apply_claim_deltas`` mutation + the ``claim_decision`` emit.
``DiscoveryCertification`` is a drop-in object that CONFORMS to that protocol so B injects
M28 discovery into the existing mcl loop with **zero edits to mcl / planner / kernel** — the
same way ``NullCertification`` / ``RegisteredFnCertification`` plug in.

THE MOAT, unchanged: this adapter produces ClaimDelta **proposals** only (via
``ledger_bridge.build_round_deltas`` → ``build_delta``); the kernel gate inside B's
``_certify_round`` (``apply_claim_deltas``) is still the SOLE mutator. Agents/LLM never touch
the ledger. The registered decision_fn ``m28_discovery_verdict`` (imported here → registered)
is biology-agnostic, so B's governance gate accepts these deltas like any other.

Trust discipline (bio_seams/M28.md seam #1): the decisive-band gate is fed ONLY by
``EvidenceObservation.trusted``. B populates that flag from the certified/QC-adjudicated
round observation. This adapter additionally CROSS-CHECKS against the round's
``adjudicated_observations``: an observation the QC stream saw and did NOT mark TRUSTED is
forced ``trusted=False`` (→ collapses to INSUFFICIENT, non-mutating). An observation absent
from the stream keeps the flag B set (the domain-local demo passes an empty stream). So the
certified stream can only ever REMOVE trust, never manufacture it — untrusted evidence never
mutates a claim (kernel red line K3, enforced agent-side before the gate too).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

# READ-ONLY kernel/planner imports (called, never edited). TrustLevel lets the adapter
# read the QC verdict on an adjudicated observation; the CertificationPolicy Protocol is
# imported only so a conformance check / type reader can see the shape (structural typing —
# the adapter needs no base class).
from expos.kernel.claims import ClaimDelta, Ledger
from expos.kernel.objects import TrustLevel

from analysis_backends.objects import EvidenceObservation
from hypotheses.objects import Hypothesis

from agents.biology_discovery import ledger_bridge

#: CrossRoundState shape mirrors ``expos.planner.certification.CrossRoundState``
#: (JSON-serializable ``{claim_id: ...}`` or None). M28 v0.1 is a stateless criterion, so it
#: threads the state through untouched (the honest surface-absent discipline).
CrossRoundState = dict[str, Any] | None


@dataclass(frozen=True)
class DiscoveryVerdict:
    """One trusted-or-not analysis verdict staged for certification: the competing
    hypothesis under test + the ``EvidenceObservation`` the ``AnalysisAgent`` produced for
    it (the "AnalysisVerdict"). This is evidence + a proposal target — NOT a claim.

    ``obs_join_id`` is the id of the ADJUDICATED kernel observation this verdict was computed
    from (defaults to the evidence's own ``observation_id``). It is the key the trust
    cross-check joins on against the round's ``adjudicated_observations``; B sets it to the
    ``ObservationObject.obs_id`` once the wet/sim-reader stream feeds the analysis."""

    hypothesis: Hypothesis
    observation: EvidenceObservation
    obs_join_id: str | None = None

    def join_id(self) -> str:
        return self.obs_join_id if self.obs_join_id is not None else self.observation.observation_id


class DiscoveryCertification:
    """``CertificationPolicy`` adapter — the mcl entry point for M28 discovery.

    Construct it with the round's staged ``DiscoveryVerdict``s (or plain
    ``(Hypothesis, EvidenceObservation)`` tuples). ``decide`` builds one ``ClaimDelta`` per
    verdict via the bridge, chaining provenance to the run's REAL compiled-knowledge
    fingerprint (the ``knowledge_fingerprint`` mcl passes), and returns
    ``(deltas, cross_round_state)`` — B's ``_certify_round`` lands them.

    Parameters:
      * ``verdicts`` — the staged analysis verdicts for this round.
      * ``run_fingerprint`` — recorded on each delta's provenance activity (K4 side-info).
      * ``enforce_adjudicated_trust`` — when True (default), cross-check each verdict's
        ``join_id`` against the round's ``adjudicated_observations``: a QC-non-TRUSTED
        observation forces ``trusted=False``. When B has not yet wired the id join, pass
        False to honor ``EvidenceObservation.trusted`` as B set it (seam #1)."""

    name = "discovery_certification"

    def __init__(
        self,
        verdicts: Sequence["DiscoveryVerdict | tuple[Hypothesis, EvidenceObservation]"],
        *,
        run_fingerprint: str = "m28-mcl",
        enforce_adjudicated_trust: bool = True,
    ) -> None:
        self._verdicts: list[DiscoveryVerdict] = [
            v if isinstance(v, DiscoveryVerdict) else DiscoveryVerdict(v[0], v[1])
            for v in verdicts
        ]
        self._run_fingerprint = run_fingerprint
        self._enforce_adjudicated_trust = enforce_adjudicated_trust

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
        kernel gate certifies one SUPPORTED and REJECTS the other)."""
        items = [
            (v.hypothesis, self._trust_gated(v, adjudicated_observations))
            for v in self._verdicts
        ]
        deltas = ledger_bridge.build_round_deltas(
            ledger,
            items,
            run_fingerprint=self._run_fingerprint,
            consumed_knowledge_fingerprint=knowledge_fingerprint,
        )
        # Stateless criterion: pass the cross-round state through unchanged (surface-absent).
        return deltas, cross_round_state

    # -- trust cross-check ------------------------------------------------------------
    def _trust_gated(
        self, verdict: DiscoveryVerdict, adjudicated_observations: Sequence[Any]
    ) -> EvidenceObservation:
        """Downgrade a verdict to untrusted iff the QC stream SAW its source observation and
        did NOT mark it TRUSTED. The certified stream can only remove trust, never add it."""
        obs = verdict.observation
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
