"""M17 K-E — the acceptance discriminator suite (gates K1-K5 of docs/M17_
KNOWLEDGE_FEEDBACK.md).

This suite was LAYERED (the W8 burndown pattern): what could be tested early ran
for real, while the K2 bodies that depended on the then-in-flight K-B statistical
aggregator and K-C mcl wiring were written in FULL as ``@pytest.mark.skip`` stubs
(the executable spec for the pending work). K-B (``qc.certification_stats``) and
K-C (``mcl.run_mcl_loop`` + ``planner.certification.AggregatedCertification``) have
now LANDED, so §5/§7 drive REAL two-legged mcl runs (dry PySCF + wet multi-replicate
sim) — the whole suite is live, no skips remain.

Testable now — against three real substrates:
  * the K-A online claim-ledger (expos.kernel.claims, letter 080 schema reveal);
  * the honest-null decision fn ``reference_round_certification`` (K-A ships it
    registered, certifying INSUFFICIENT unconditionally — absence of evidence is
    not support);
  * the K-D two-face wet domain (expos.adapters.wet, consumed read-only — this
    file never modifies the adapter, letter 075).

Sections:
  §1  Schema-contract discrimination — the letter-080 stable deny codes and the
      insufficient<=>no-content validator, each with a kill construction.
  §2  MR_reverse substrate strengthening — the D1 embryo: the direction statistic
      on the consistent vs flipped face is FULLY SEPARATED across N seeds (the
      full decisive-verdict version is the K2 online ring, §5).
  §3  MR_null negative control — on the landed ``flat`` null face the honest-null
      aggregator certifies INSUFFICIENT and mutates no head; the null face carries
      no stable direction. Kill criterion for a future fn is documented inline.
  §4  Provenance snapshot chain — the K1/K4 audit hook: fingerprinted observation
      set ⊆ the round's obs, consumed-knowledge fingerprint present, the three
      version fields present, and a bit-for-bit deterministic snapshot fingerprint.
  §5-§7  Live K2 acceptance against real mcl runs: the five-conjunction online ring
      (§5), the insufficient three-branch criterion (§6), and the convergence
      double-gate (§7).
"""

from __future__ import annotations

import hashlib
import threading

import pytest
from pydantic import ValidationError

from expos.adapters.wet import sim_reader
from expos.kernel.claims import (
    DENY_UNREGISTERED_DECISION_FN,
    DENY_WEAK_CANNOT_RETRACT_STRONG,
    REFERENCE_CERTIFICATION_FN_ID,
    REFERENCE_CERTIFICATION_FN_VERSION,
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimLedgerError,
    ClaimRecord,
    ClaimVersionContent,
    EvidenceStrength,
    Ledger,
    ObservationFingerprint,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    StatisticSnapshot,
    add_claim_record,
    apply_claim_deltas,
    reference_round_certification,
)

# K-D substrate, consumed read-only (never modified here — letter 075).
from tests.test_k_flipped_domain import (
    _BATCH,
    _corr,
    _free_port,
    _run_and_judge,
    _trusted_pol_resp,
    _wait_port,
)

SUP = ClaimDecisionStatus.SUPPORTED
REJ = ClaimDecisionStatus.REJECTED
QUAL = ClaimDecisionStatus.QUALIFIED
INSUF = ClaimDecisionStatus.INSUFFICIENT

NONE = EvidenceStrength.NONE
WEAK = EvidenceStrength.WEAK
MODERATE = EvidenceStrength.MODERATE
STRONG = EvidenceStrength.STRONG
VERY_STRONG = EvidenceStrength.VERY_STRONG

#: A discriminating clear-direction effect-size gate mirrors the K-D one (|r| >=
#: 0.5). The consistent/flipped signal faces are SIGN-deterministic across seeds;
#: the null (flat) face is not, so its per-seed |r| may briefly exceed the gate —
#: which is exactly why the aggregator, not the raw correlation, is the verdict.
_CLEAR_DIRECTION_GATE = 0.5
_N_SEEDS = 5
_NOISE_SD = 0.04


# ---------------------------------------------------------------- claim builders
# Minimal ClaimDelta / ClaimRecord builders (same shape as tests/test_k_a_claims).


def _prov(
    *,
    decision_fn_id: str = REFERENCE_CERTIFICATION_FN_ID,
    decision_fn_version: str = REFERENCE_CERTIFICATION_FN_VERSION,
    obs: tuple[tuple[str, str], ...] = (("obs_1", "fp_1"),),
    kfp: str = "kfp_old",
    criterion: str = "crit_v1",
    **stat: object,
) -> ProvenanceSnapshot:
    return ProvenanceSnapshot(
        usage=ProvenanceUsage(
            observations=tuple(
                ObservationFingerprint(obs_id=o, content_fingerprint=f) for o, f in obs
            ),
            consumed_knowledge_fingerprint=kfp,
        ),
        activity=ProvenanceActivity(
            decision_fn_id=decision_fn_id,
            decision_fn_version=decision_fn_version,
            criterion_version=criterion,
        ),
        statistic=StatisticSnapshot(**stat),
    )


def _delta(
    target: str,
    status: ClaimDecisionStatus,
    strength: EvidenceStrength,
    *,
    statement: str = "stmt",
    **prov_kw: object,
) -> ClaimDelta:
    content = (
        None
        if status is INSUF
        else ClaimVersionContent(statement=statement, status=status)
    )
    return ClaimDelta(
        target_claim_id=target,
        status=status,
        new_content=content,
        evidence_strength=strength,
        provenance=_prov(**prov_kw),
    )


def _head(
    claim_id: str,
    status: ClaimDecisionStatus,
    strength: EvidenceStrength,
    version: int = 1,
) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        version=version,
        status=status,
        statement="seed",
        evidence_strength=strength,
        provenance=_prov(),
    )


# ---------------------------------------------------------------- wet helpers


class _ProbeReader:
    """In-process reader on a free port for one truth face at a chosen seed/noise.

    A parametrized sibling of test_k_flipped_domain._Reader (which pins
    noise_sd=0.0): §2/§3 need noise + a varying seed to draw a distribution of the
    direction statistic. The adapter itself is untouched — only ``serve`` is called.
    """

    def __init__(self, profile: str, seed: int, noise_sd: float) -> None:
        self._profile, self._seed, self._noise = profile, seed, noise_sd

    def __enter__(self) -> int:
        self.port = _free_port()
        self.srv = sim_reader.serve(
            "127.0.0.1",
            self.port,
            seed=self._seed,
            noise_sd=self._noise,
            truth_profile=self._profile,
        )
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()
        _wait_port(self.port)
        return self.port

    def __exit__(self, *exc: object) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _direction_r(profile: str, seed: int, noise_sd: float, tmp_path) -> float:
    """Drive one full wet leg on ``profile`` and return the K-E direction statistic
    (sign of the Pearson correlation between public solvent polarity and TRUSTED
    response). Reuses the K-D end-to-end pipeline verbatim."""
    with _ProbeReader(profile, seed, noise_sd) as port:
        obs = _run_and_judge(port, _BATCH, tmp_path)
    pol, resp = _trusted_pol_resp(obs, _BATCH)
    assert len(pol) >= 3, "need >=3 TRUSTED points to read a direction"
    return _corr(pol, resp)


def _flat_round_observations(seed: int, tmp_path):
    """Judged observations from one flat-face (null) round + the set of TRUSTED,
    non-control obs ids (the "this round's obs" superset for the §3/§4 audits)."""
    from expos.kernel.objects import TrustLevel

    with _ProbeReader("flat", seed, _NOISE_SD) as port:
        obs = _run_and_judge(port, _BATCH, tmp_path)
    round_obs_ids = {
        o.obs_id for o in obs if not o.is_control and o.trust is TrustLevel.TRUSTED
    }
    return obs, round_obs_ids


def _fingerprint(value: float) -> str:
    return "sha256:" + hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


# ================================================================ §1 schema contract


def test_schema_insufficient_iff_no_content_validator_kills_both_directions():
    """The letter-080 type-level isolation contract: insufficient <=> new_content
    is None, enforced BOTH ways by the ClaimDelta validator.

    KILL: drop the ``_content_matches_status`` validator and BOTH constructions
    below stop raising -> red. This is the K3 boundary made structural: an
    insufficient verdict can never smuggle a head, and a mutating verdict can
    never land headless."""
    # insufficient must NOT carry a new claim version.
    with pytest.raises(ValidationError):
        ClaimDelta(
            target_claim_id="c1",
            status=INSUF,
            new_content=ClaimVersionContent(statement="x", status=INSUF),
            evidence_strength=NONE,
            provenance=_prov(),
        )
    # a mutating verdict MUST carry a new claim version.
    with pytest.raises(ValidationError):
        ClaimDelta(
            target_claim_id="c1",
            status=SUP,
            new_content=None,
            evidence_strength=MODERATE,
            provenance=_prov(),
        )
    # split-brain guard: content.status must echo the delta verdict.
    with pytest.raises(ValidationError):
        ClaimDelta(
            target_claim_id="c1",
            status=SUP,
            new_content=ClaimVersionContent(statement="x", status=REJ),
            evidence_strength=MODERATE,
            provenance=_prov(),
        )


def test_schema_weak_cannot_retract_strong_degrades_to_annotation():
    """weak_cannot_retract_strong is a DEGRADE, not an override: a weak-evidence
    delta against a STRONG head lands only a downgraded, loudly-reasoned annotation
    while the head keeps its effective status.

    KILL: remove the strength-monotonicity gate and the reject supersedes the head
    (effective status flips to REJECTED, is_annotation False) -> red."""
    seed = Ledger(claims=(_head("c1", SUP, STRONG),))
    new, report = apply_claim_deltas(seed, [_delta("c1", REJ, WEAK)])

    assert new.effective_statuses()["c1"] is SUP  # head untouched (K3/red-line-2)
    assert new.head("c1").evidence_strength is STRONG
    (out,) = report.rejected
    assert out.deny_reason == DENY_WEAK_CANNOT_RETRACT_STRONG
    assert out.mutated_effective_status is False
    # degrade never silent: exactly one traceable annotation, reason-coded.
    ann = [r for r in new.claims if r.is_annotation]
    assert len(ann) == 1
    assert ann[0].is_annotation is True
    assert ann[0].deny_reason == DENY_WEAK_CANNOT_RETRACT_STRONG


def test_schema_unregistered_decision_fn_rejected_and_lands_no_record():
    """A delta whose decision_fn is not in the shared registry is denied outright:
    NO record lands and the head is untouched (governance red line 1 — the online
    path may never bypass the offline compiler's membership authority).

    KILL: remove the registration gate and the reject supersedes the head ->
    landed_record_version is not None and the effective status changes -> red."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    before_versions = {(r.claim_id, r.version) for r in seed.claims}
    new, report = apply_claim_deltas(
        seed, [_delta("c1", REJ, STRONG, decision_fn_id="ghost_fn")]
    )
    (out,) = report.rejected
    assert out.deny_reason == DENY_UNREGISTERED_DECISION_FN
    assert out.landed_record_version is None  # nothing landed
    assert {(r.claim_id, r.version) for r in new.claims} == before_versions
    assert new.effective_statuses()["c1"] is SUP


def test_schema_append_only_rewrite_in_place_raises():
    """Rewriting an existing (claim_id, version) with different content is a
    structural append-only violation that raises loudly (never a silent overwrite).

    KILL: drop the collision check in _add_record and this stops raising -> red."""
    seed = Ledger(claims=(_head("c1", SUP, MODERATE),))
    tamper = ClaimRecord(
        claim_id="c1",
        version=1,  # same key, different content
        status=REJ,
        statement="rewritten",
        evidence_strength=MODERATE,
        provenance=_prov(),
    )
    with pytest.raises(ClaimLedgerError):
        add_claim_record(seed, tamper)


def test_schema_effective_status_is_derived_not_a_stored_mutable_field():
    """Effective status and the forward supersede pointer are DERIVED read-side by
    replaying the append-only record set — never stored mutable fields.

    Positive: a supersede chain replays to the right head and the forward pointer
    inverts the immutable back-pointer. Structural KILL: if someone re-introduced a
    writable ``superseded_by`` / ``effective_status`` field on the record (the
    in-place-mutation anti-pattern), the two membership asserts + the frozen-write
    guard below turn red."""
    seed = Ledger(claims=(_head("c1", SUP, WEAK),))
    l1, _ = apply_claim_deltas(seed, [_delta("c1", REJ, STRONG, statement="r")])
    l2, _ = apply_claim_deltas(l1, [_delta("c1", SUP, VERY_STRONG, statement="s2")])

    # derived by replay: stronger last supersede wins; every version retained.
    assert l2.effective_statuses() == {"c1": SUP}
    assert sorted(r.version for r in l2.claims if r.claim_id == "c1") == [1, 2, 3]
    assert l2.head("c1").version == 3
    # forward pointer is inverted from the immutable back-pointer, not stored.
    assert l2.superseded_by("c1", 1) == 2
    assert l2.superseded_by("c1", 2) == 3
    assert l2.superseded_by("c1", 3) is None

    # structural: no stored forward/effective status field exists on the record.
    assert "superseded_by" not in ClaimRecord.model_fields
    assert "effective_status" not in ClaimRecord.model_fields
    # frozen: the stored ``status`` cannot be rewritten in place either.
    with pytest.raises(ValidationError):
        l2.head("c1").status = REJ


# ================================================================ §4 provenance chain


def test_provenance_snapshot_chain_is_complete_and_deterministic(tmp_path):
    """K1/K4 audit hook via the honest-null decision fn: produce an INSUFFICIENT
    delta from a real flat-face round and assert the provenance snapshot is
    self-sufficient and its fingerprint is bit-for-bit deterministic.

    Asserts: (1) the fingerprinted observation set is non-empty and ⊆ the round's
    TRUSTED obs (no substituted / phantom evidence); (2) the consumed-knowledge
    fingerprint is present (which old knowledge this was judged against, K4); (3)
    the three version fields (decision_fn_version, criterion_version,
    gate_rules_version) are all present; (4) the same inputs recompute the SAME
    snapshot fingerprint (K5 determinism)."""
    obs, round_obs_ids = _flat_round_observations(seed=1, tmp_path=tmp_path)
    used = [
        (o.obs_id, _fingerprint(o.result.value))
        for o in obs
        if o.obs_id in round_obs_ids
    ][:4]
    assert used, "expected TRUSTED observations to carry into provenance"

    # honest-null aggregator: absence of a wired statistic certifies INSUFFICIENT.
    verdict = reference_round_certification(
        statistic={}, power={}, criterion_version="crit_v1"
    )
    assert verdict is INSUF

    def _make() -> ProvenanceSnapshot:
        return _prov(obs=tuple(used), kfp="kfp_round0")

    snap = _make()
    delta = ClaimDelta(
        target_claim_id="polar_higher",
        status=verdict,
        new_content=None,  # INSUFFICIENT proposes no head
        evidence_strength=NONE,
        provenance=snap,
    )

    # (1) fingerprinted observation set non-empty and ⊆ this round's obs.
    used_ids = {o.obs_id for o in delta.provenance.usage.observations}
    assert used_ids
    assert used_ids <= round_obs_ids
    # (2) consumed-knowledge fingerprint present.
    assert delta.provenance.usage.consumed_knowledge_fingerprint == "kfp_round0"
    # (3) three version fields present on the activity slot.
    act = delta.provenance.activity
    assert act.decision_fn_version and act.criterion_version and act.gate_rules_version
    # (4) determinism: identical inputs -> identical fingerprint, bit-for-bit.
    assert _make().fingerprint() == snap.fingerprint()


# ================================================================ §2 MR_reverse (D1 embryo)


def test_mr_reverse_direction_statistic_fully_separated(tmp_path):
    """MR_reverse substrate strengthening (the D1 embryo). Over N seeds each, the
    direction statistic on the CONSISTENT (``polar_high``) face and the FLIPPED
    (``nonpolar_high``) face are FULLY SEPARATED: every consistent draw is
    positive, every flipped draw is negative, with a clean margin between the two
    clouds. This is the "direction statistic reverses under τ_flip" relation of
    MR_reverse elevated from a single deterministic point to a seeded distribution;
    the full C2ST two-sample discriminator is deferred to K-B (skip stub §5).

    KILL: a τ_flip that failed to reverse the truth face (or an aggregator reading
    the wrong axis) would overlap the two clouds -> the separation assert reddens."""
    consistent = [
        _direction_r("polar_high", seed, _NOISE_SD, tmp_path) for seed in range(_N_SEEDS)
    ]
    flipped = [
        _direction_r("nonpolar_high", seed, _NOISE_SD, tmp_path)
        for seed in range(_N_SEEDS)
    ]

    assert all(r > 0.0 for r in consistent), f"consistent must be +: {consistent}"
    assert all(r < 0.0 for r in flipped), f"flipped must be -: {flipped}"
    # full separation: the two seeded clouds do not overlap at all.
    assert min(consistent) > max(flipped), (consistent, flipped)
    # the flipped face clears the clear-direction gate on the negative side.
    assert max(flipped) < -_CLEAR_DIRECTION_GATE, flipped


# ================================================================ §3 MR_null (negative control)


def test_mr_null_flat_face_honest_null_certifies_insufficient(tmp_path):
    """MR_null in its first realised form: on the landed ``flat`` null face the
    honest-null aggregator certifies INSUFFICIENT and mutates NO head — the null
    face fabricates no direction claim.

    Two teeth:
      (a) Data — averaged over N seeds the flat face carries no stable direction:
          mean |r| stays below the clear-direction gate, and is far weaker than a
          genuine signal face measured deterministically (nonpolar, noise 0).
      (b) Aggregator — reference_round_certification -> INSUFFICIENT applied to a
          seeded ``supported`` head leaves the effective status UNCHANGED.

    KILL CRITERION for the pending K-B statistical fn (documented, becomes live
    when K-B replaces the honest-null fn): if a future decision_fn returns
    SUPPORTED/REJECTED on this flat-face data, the apply below would mutate the
    head and assert (b) reddens — a null face producing a decisive verdict is the
    exact failure MR_null exists to catch."""
    flat_rs = [
        abs(_direction_r("flat", seed, _NOISE_SD, tmp_path)) for seed in range(_N_SEEDS)
    ]
    mean_flat = sum(flat_rs) / len(flat_rs)
    signal_r = abs(_direction_r("nonpolar_high", 0, 0.0, tmp_path))  # deterministic

    # (a) no stable direction on the null face, and clearly weaker than a signal.
    assert mean_flat < _CLEAR_DIRECTION_GATE, f"flat |r| mean {mean_flat:.3f}"
    assert signal_r > _CLEAR_DIRECTION_GATE
    assert mean_flat < signal_r

    # (b) honest-null aggregator -> INSUFFICIENT -> no head mutation (K3).
    obs, round_obs_ids = _flat_round_observations(seed=0, tmp_path=tmp_path)
    used = [
        (o.obs_id, _fingerprint(o.result.value))
        for o in obs
        if o.obs_id in round_obs_ids
    ][:4]
    verdict = reference_round_certification(
        statistic={}, power={}, criterion_version="crit_v1"
    )
    assert verdict is INSUF
    seeded = Ledger(claims=(_head("polar_higher", SUP, STRONG),))
    delta = ClaimDelta(
        target_claim_id="polar_higher",
        status=verdict,
        new_content=None,
        evidence_strength=NONE,
        provenance=_prov(obs=tuple(used), kfp="kfp_round0"),
    )
    new, report = apply_claim_deltas(seeded, [delta])
    assert new.effective_statuses()["polar_higher"] is SUP  # null face fabricates none
    assert report.applied[0].mutated_effective_status is False


# ================================================================ §5-§7 K2 online-ring
# The K-B statistical aggregator (``qc.certification_stats``) and the K-C mcl wiring
# (``mcl.run_mcl_loop`` + ``planner.certification.AggregatedCertification``) have
# LANDED, so these bodies now drive REAL two-legged mcl runs (dry PySCF + wet
# multi-replicate sim) end to end — the decisive-verdict acceptance the K-F glue
# suite deliberately left to K-E (its single-replicate substrate is honestly
# insufficient; letters 075/085). The speculative ``mcl.run_online_round`` /
# ``mcl.run_until_converged`` hooks the stubs were pinned on never shipped; the
# real entry point is ``run_mcl_loop`` with an ``AggregatedCertification`` seventh
# element, so the online-ring helpers below wrap it (per-face run, per-round view).
#
# SUBSTRATE FACTS pinned by the K-E probes (scratchpad ledger), load-bearing for the
# parameter choices below and honest about what the real loop can and cannot express:
#
#   * DECISIVE reachability needs >= ~6 within-arm pairs/round. The live promotion
#     gate caps the wet leg at top_k=2 candidates (``mcl._PROMOTION_TOP_K``), so an
#     arm holds ONE candidate; the only knob K-E owns for more pairs is the wet
#     REPLICATE count (letter 085 "cure = >= replicate wells per candidate"). At
#     ``replicates=8`` the focal/reference arms hold 8 obs each => 8 pairs => the
#     paired sign-flip permutation floors p at 2/2**8 and the Shafer e-value clears
#     ``1/alpha`` after r_min=2 rounds (empirically e_product ~ 102 on the flipped
#     face). ``replicates=3`` (the domain default) caps n_pairs at 3 => per-round e
#     <= 1.0 => e_product frozen <= 1 => provably NEVER decisive; K-E therefore
#     writes a ``replicates=8`` variant of the domain (a test-local substrate knob,
#     not an OS change). The alternative "2 candidates per arm" is unreachable: the
#     promotion gate never promotes 4 candidates.
#
#   * The two truth faces are NOT symmetric for the ethanol-vs-acetonitrile contrast.
#     ``polar_high`` (truth optimum mu=0.55) sits BETWEEN the two solvents' realised
#     polarities (~0.59 / ~0.51) => the arms read nearly equal => a genuine ~0 effect
#     => honestly insufficient FOREVER (no head mutation). Only ``nonpolar_high``
#     (mu=0.20) puts the pair on a steep flank => a clean separated effect => a
#     decisive verdict. So with a single fixed prior, exactly ONE face re-guides the
#     agent and the other is a genuine null control — this asymmetry is a substrate
#     fact, not a wiring gap, and it SHARPENS the anti-decorative ring (§ conjunct 3).
#
#   * Proposal reordering requires the agent's polar/nonpolar PREFERENCE to flip,
#     which needs BOTH claims certified decisively (polar->rejected AND
#     nonpolar->supported). That flip changes the PROMOTED set, which makes the
#     certification head's arms degenerate at the very round the knowledge has
#     evolved — so conjunct 2 (the aggregator ate this round's obs, head populated)
#     and conjuncts 3-5 (evolved knowledge/proposal/promotion) are read from the
#     rounds where each genuinely holds: conjunct 2 from the frozen consistent run
#     (arms stay populated), conjuncts 3-5 from the contradicted flipped run.

# ---------------------------------------------------------------- K2 online-ring glue

_RING_POLAR = "c_polar_responds_higher"
_RING_NONPOLAR = "c_nonpolar_responds_higher"
#: The default (polar-preferring) knowledge promotes these two in-window solvents to
#: the wet leg every round — the only pair the top_k=2 gate makes available as arms.
_RING_FOCAL = "cand_ethanol"
_RING_REFERENCE = "cand_acetonitrile"
_RING_REPLICATES = 8  # >= 6 pairs/round => decisive after r_min rounds (see note above)


def _replicated_domain(tmp_path, n_replicates: int):
    """Write a test-local variant of the M16 solvent-screen domain with an enlarged
    wet REPLICATE count (the only K-E-owned knob that reaches a decisive wet
    substrate — see the SUBSTRATE FACTS note). The dry leg is one PySCF job per
    candidate regardless, so this only widens the wet arms, never the job count."""
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"
    text = base.read_text()
    assert "replicates: 8" in text, "domain replicate anchor moved — update the K-E knob"
    out = tmp_path / f"solvent_screen_r{n_replicates}.yaml"
    out.write_text(text.replace("replicates: 8", f"replicates: {n_replicates}"))
    return out


def _ring_head(claim_id, focal, reference):
    from expos.qc.certification_stats import ClaimHead

    return ClaimHead(
        claim_id=claim_id, statement=claim_id, favorable_direction="higher",
        focal_group=(focal,), reference_group=(reference,),
    )


def _ring_certification():
    """Two mirror-image heads over the SAME promoted pair, so a decisive round can
    flip the agent's polar<->nonpolar preference (the re-guidance that reorders the
    proposal and the promoted set): ``polar`` supported iff ethanol reads higher,
    ``nonpolar`` supported iff acetonitrile reads higher."""
    from expos.planner.certification import AggregatedCertification
    from expos.qc.certification_stats import AggregationConfig

    return AggregatedCertification(
        [
            _ring_head(_RING_POLAR, _RING_FOCAL, _RING_REFERENCE),
            _ring_head(_RING_NONPOLAR, _RING_REFERENCE, _RING_FOCAL),
        ],
        config=AggregationConfig(run_fingerprint="k_e_ring", r_min=2, w_min=0.5),
    )


def _parse_ring_run(run_dir):
    """Project a completed mcl run into a per-round view list (the online-ring
    forensics): proposal order (PRIOR_PROPOSAL decision), promoted set (promotion
    event), knowledge fingerprint (knowledge_updated event), the round's TRUSTED wet
    obs ids, and the polar claim's consumed-observation ids (claim_decision event)."""
    from types import SimpleNamespace

    from expos.kernel.objects import DecisionKind, TrustLevel
    from expos.kernel.store import RunStore

    store = RunStore(run_dir, create=False)
    proposals = {
        d.round_id: list(d.content["candidates"])
        for d in store.list_decisions()
        if d.kind is DecisionKind.PRIOR_PROPOSAL
    }
    kfps = [e["payload"]["fingerprint"] for e in store.read_events("knowledge_updated")]
    promoted = {
        e["payload"]["round_id"]: [p["cand_id"] for p in e["payload"]["promoted"]]
        for e in store.read_events("promotion_decision")
    }
    polar_cd = {
        e["payload"]["round_id"]: e["payload"]
        for e in store.read_events("claim_decision")
        if e["payload"]["claim_id"] == _RING_POLAR
    }
    round_obs: dict[int, list[str]] = {}
    for o in store.list_observations(trust=TrustLevel.TRUSTED):
        if o.raw_ref.kind == "wet":
            round_obs.setdefault(o.round_id, []).append(o.obs_id)

    views = {}
    for rid in sorted(proposals):
        cd = polar_cd.get(rid, {"input_observation_ids": []})
        views[rid] = SimpleNamespace(
            round_id=rid,
            proposal_order=proposals[rid],
            promoted=promoted.get(rid, []),
            knowledge_fingerprint=kfps[rid],
            round_obs_ids=round_obs.get(rid, []),
            # the delta's PROV usage, reconstructed from the emitted claim_decision —
            # the same obs-id set the aggregator consumed (K1 input introspection).
            delta=SimpleNamespace(
                provenance=SimpleNamespace(
                    usage=SimpleNamespace(
                        observations=[
                            SimpleNamespace(obs_id=oid)
                            for oid in cd["input_observation_ids"]
                        ]
                    )
                )
            ),
        )
    return views


#: memoise the two per-face runs (deterministic) so the four run_online_round calls
#: cost only two real mcl runs, mirroring test_k_f_glue's ``two_face_runs`` fixture.
_RING_RUN_CACHE: dict = {}


def run_online_round(face, round_id, seed, tmp):
    """Drive (once per face, then cached) a real three-round MCL on the given hidden
    truth ``face`` with the two-head ``AggregatedCertification`` seventh element, and
    return the per-round view. ``round_id`` selects the EARLY round (1 -> round 0, the
    pre-feedback proposal) or the LATE round (2 -> round 2, where a contradicted face
    has re-guided the agent)."""
    from expos.mcl import run_mcl_loop

    key = (str(tmp), face)
    if key not in _RING_RUN_CACHE:
        out = tmp / f"ring_{face}"
        run_mcl_loop(
            _replicated_domain(tmp, _RING_REPLICATES),
            rounds=3, seed=seed, out_dir=out,
            certification=_ring_certification(), truth_profile=face,
        )
        _RING_RUN_CACHE[key] = _parse_ring_run(out)
    views = _RING_RUN_CACHE[key]
    return views[0 if round_id == 1 else 2]


def test_k2_five_conjunction_ring(tmp_path):
    """K2 whole-ring five-conjunction (letter 076 / REF-B Ax three-part distillation
    against the modAL "decorative learner" incident). All five must hold together;
    any one missing leaves a decorative pass-through path where the agent only
    APPEARS re-guided by wet data. Driven by two REAL three-round mcl runs (dry PySCF
    + wet, replicates=8), one per hidden truth face, same seed and knowledge prior.

    Conjuncts (consistent = polar_high, flipped = nonpolar_high face, same seed):
      1. pre non-degeneracy — round-0 proposal order is not a constant/trivial order;
      2. input introspection — the aggregator consumed THIS round's TRUSTED obs
         (usage.observations non-empty and ⊆ the round's obs — "fit ate new data"),
         read on the frozen consistent run whose head arms stay populated;
      3. the contradicted (flipped) run's knowledge fingerprint EVOLVES while the
         prior-agreeing (consistent) run does NOT spuriously drift — the DATA-DRIVEN
         differential (a decorative learner that "updates" regardless would drift the
         null face too; a dead one would freeze the flipped face). This is the honest
         realisation of "fingerprints evolve": on THIS substrate only the face that
         CONTRADICTS the prior re-guides (polar_high sits astride the peak => genuine
         ~0 effect => no mutation), so the anti-decorative signal IS the asymmetry;
      4. output-order three-way differential — proposal order differs across
         consistent vs flipped (face-driven) and across the flipped run's late vs
         early (identity-null) rounds (feedback reaches the proposal);
      5. promotion pass-through — the promoted set changes between consistent and
         flipped (the re-guidance reaches promotion, not just the fingerprint)."""
    r1_consistent = run_online_round(face="polar_high", round_id=1, seed=7, tmp=tmp_path)
    r2_consistent = run_online_round(face="polar_high", round_id=2, seed=7, tmp=tmp_path)
    r1_flipped = run_online_round(face="nonpolar_high", round_id=1, seed=7, tmp=tmp_path)
    r2_flipped = run_online_round(face="nonpolar_high", round_id=2, seed=7, tmp=tmp_path)

    # 1. pre non-degeneracy — the seed proposal spans the pool, not a trivial order.
    assert len(set(r1_consistent.proposal_order)) > 1
    # 2. input introspection (fit ate new observations) — read on the consistent
    #    (frozen, populated) run whose head arms stay promoted, so the aggregator
    #    genuinely consumed this round's TRUSTED obs (⊆ the round's obs, no phantoms).
    used = r2_consistent.delta.provenance.usage.observations
    assert used and {o.obs_id for o in used} <= set(r2_consistent.round_obs_ids)
    # 3. contradicted face re-guides; consistent (null) face does not spuriously drift.
    assert r1_flipped.knowledge_fingerprint != r2_flipped.knowledge_fingerprint
    assert r1_consistent.knowledge_fingerprint == r2_consistent.knowledge_fingerprint
    # 4. output-order three-way differential (face-driven, and feedback-driven).
    assert r2_consistent.proposal_order != r2_flipped.proposal_order
    assert r2_flipped.proposal_order != r1_flipped.proposal_order
    # 5. promotion pass-through — the promoted set is re-steered by the wet verdict.
    assert set(r2_consistent.promoted) != set(r2_flipped.promoted)


def test_insufficient_three_branch_criterion():
    """The K-B insufficient criterion (letter 077 <-> e-process): INSUFFICIENT iff
    CS contains zero OR CS width > w_min OR rounds_observed < r_min. Each branch
    alone forces insufficient; only a round clearing all three may adjudicate.
    Acceptance-level restatement against the REAL aggregate_round (unit-level
    per-branch kills live in test_k_b_aggregation.py)."""
    import numpy as np

    from expos.qc.certification_stats import (
        AggregationConfig,
        ClaimHead,
        aggregate_round,
    )
    from expos.kernel.objects import (
        InstrumentMeta,
        LayoutMeta,
        MeasuredResult,
        ObservationObject,
        TrustLevel,
    )

    head = ClaimHead(
        claim_id="c_polar",
        statement="focal arm responds higher than reference",
        favorable_direction="higher",
        focal_group=("F",),
        reference_group=("R",),
    )
    cfg = AggregationConfig()

    def obs(oid, group, value):
        return ObservationObject(
            obs_id=oid, exp_id="exp", round_id=0, cand_id=group,
            result=MeasuredResult(metric="response", value=value),
            layout_meta=LayoutMeta(well_id=oid, row=0, col=0),
            instrument_meta=InstrumentMeta(capture_index=0),
            trust=TrustLevel.TRUSTED,
        )

    def round_obs(tag, f_mu, r_mu, sd, n=8, seed=0):
        rng = np.random.default_rng(seed)
        out = [obs(f"{tag}_f{i:03d}", "F", f_mu + float(rng.normal(0, sd)))
               for i in range(n)]
        out += [obs(f"{tag}_r{i:03d}", "R", r_mu + float(rng.normal(0, sd)))
                for i in range(n)]
        return out

    # Branch 3 (round count): one strong round alone must be INSUFFICIENT.
    delta1, agg1 = aggregate_round(round_obs("r1", 0.90, 0.40, 0.03), head, cfg)
    assert delta1.status.value == "insufficient", "single round must not adjudicate"

    # Branch 1 (CS contains zero): two rounds of NO-signal data stay INSUFFICIENT.
    _, n1 = aggregate_round(round_obs("n1", 0.60, 0.60, 0.03, seed=1), head, cfg)
    dn, _ = aggregate_round(round_obs("n2", 0.60, 0.60, 0.03, seed=2), head, cfg,
                            n1.state)
    assert dn.status.value == "insufficient", "zero-effect data must not adjudicate"

    # Branch 2 (CS width): two rounds of very noisy data stay INSUFFICIENT.
    _, w1 = aggregate_round(round_obs("w1", 0.70, 0.50, 0.60, seed=3), head, cfg)
    dw, _ = aggregate_round(round_obs("w2", 0.70, 0.50, 0.60, seed=4), head, cfg,
                            w1.state)
    assert dw.status.value == "insufficient", "imprecise CS must not adjudicate"

    # Decisive: two strong clean rounds clear all three branches -> adjudicates.
    d2, _ = aggregate_round(round_obs("r2", 0.90, 0.40, 0.03, seed=5), head, cfg,
                            agg1.state)
    assert d2.status.value != "insufficient", (
        "two strong rounds clearing all three branches must earn a verdict"
    )
    assert d2.status.value == "supported"


def run_until_converged(face, seed, tmp, *, r_min=2, w_min=0.5, max_rounds=6):
    """Drive real MCL rounds (replicates=8, single polar head) until the polar claim
    reaches a DECISIVE effective verdict, capped at ``max_rounds`` (letter 077 D3
    double gate: convergence needs BOTH the CS-width gate and the min-round gate).
    Fresh deterministic run per round-count; returns the converged summary."""
    from types import SimpleNamespace

    from expos.kernel.claims import ClaimDecisionStatus, ClaimRecord, Ledger
    from expos.kernel.store import RunStore
    from expos.mcl import run_mcl_loop
    from expos.planner.certification import AggregatedCertification
    from expos.qc.certification_stats import AggregationConfig

    domain = _replicated_domain(tmp, _RING_REPLICATES)
    cfg = AggregationConfig(run_fingerprint="k_e_conv", r_min=r_min, w_min=w_min)
    for rounds in range(r_min, max_rounds + 1):
        out = tmp / f"conv_r{rounds}"
        run_mcl_loop(
            domain, rounds=rounds, seed=seed, out_dir=out,
            certification=AggregatedCertification(
                [_ring_head(_RING_POLAR, _RING_FOCAL, _RING_REFERENCE)], config=cfg
            ),
            truth_profile=face,
        )
        store = RunStore(out, create=False)
        polar_cds = [
            e["payload"]
            for e in store.read_events("claim_decision")
            if e["payload"]["claim_id"] == _RING_POLAR
        ]
        ckpt = store.read_checkpoint() or {}
        ledger = Ledger(
            claims=tuple(
                ClaimRecord.model_validate(r) for r in ckpt.get("claim_ledger", [])
            )
        )
        eff = ledger.effective_statuses()
        # convergence = a DECISIVE effective verdict has landed (insufficient never
        # mutates the head, so the seed status would still stand — no false convergence).
        if eff.get(_RING_POLAR) is not ClaimDecisionStatus.SUPPORTED:
            decisive = polar_cds[-1]
            ci_low, ci_high = decisive["statistic"]["ci"]
            state = (ckpt.get("certification_state") or {}).get(_RING_POLAR, {})
            return SimpleNamespace(
                cs_width=ci_high - ci_low,
                w_min=w_min,
                rounds=state.get("rounds_observed", rounds),
                r_min=r_min,
                effective_status=eff,
            )
    raise AssertionError(f"polar claim did not converge within {max_rounds} rounds")


def test_k2_convergence_double_gate(tmp_path):
    """K2 convergence (letter 077 D3 double gate): running the online loop to
    convergence must satisfy BOTH the CS-width gate and the min-round gate, and the
    converged effective claim must match the true face — on the flipped
    (nonpolar_high) face the seeded ``supported`` polar-higher claim self-derives the
    CONTRARY ``rejected`` verdict from the wet data, with zero external injection.

    KILL: a convergence declared on width alone (skipping the round gate) or a
    converged verdict disagreeing with the true face -> red."""
    converged = run_until_converged(face="nonpolar_high", seed=7, tmp=tmp_path)
    assert converged.cs_width <= converged.w_min  # width gate
    assert converged.rounds >= converged.r_min  # min-round gate (double gate)
    # the flipped face self-derives the CONTRARY verdict, zero external injection.
    assert converged.effective_status[_RING_POLAR] is REJ
