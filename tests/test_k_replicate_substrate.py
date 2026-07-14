"""M17 K-F prerequisite — the wet MULTI-REPLICATE substrate (letters 075/085).

Letter 085 pinned the structural blocker: the live mcl wet leg lays ONE well per
candidate, so a polarity claim's two arms each hold a single observation. That
gives n_pairs=1 -> se=inf -> no confidence sequence -> per-round e=0 -> the
e-product is stuck at its 1.0 floor and can never cross 1/alpha; and it makes
corr(capture_index, arm)=+-1, tripping the letter-075 plate-order guard. Both are
HONEST refusals, and both make a DECISIVE verdict unreachable on that substrate.

This suite proves the cure is an EXPERIMENTAL-DESIGN change, not a statistics
change: lay each candidate across n replicate wells in a BALANCED (interleaved)
plate order. Coverage:

  1. regression: the default (n_replicates=1, interleave=False) compile + layout
     are BIT-FOR-BIT the pre-K-F single-well plate (hard gate);
  2. real-pipeline plumbing: a 3-replicate interleaved protocol driven through the
     real reader yields exactly 3 wells per candidate, an INDEPENDENT four-segment
     custody chain per replicate well, and corr(capture_index, arm) ~= 0;
  3. NEGATIVE sample (letter 085): the single-well sequential substrate fed to the
     aggregator is REFUSED to insufficient (confound guard trips + e never
     accumulates) -- the ready-made "delete the guard and this goes red" reference;
  4. K1 reachability pre-verification (the reason this batch exists): the
     3-replicate interleaved substrate, on the flipped truth face, fed two strong
     rounds directly to aggregate_round, accumulates a real e-product ACROSS the
     1/alpha threshold and lands a DECISIVE verdict -- rejected, i.e. CONTRARY to
     the seeded "polar responds higher" claim, with ZERO injection.
"""

from __future__ import annotations

import socket
import threading
import time
from collections import Counter

import numpy as np
import pytest

from expos.adapters.wet import sim_reader
from expos.adapters.wet.driver import GoalState
from expos.adapters.wet.screen import (
    WET_METRIC,
    compile_wet,
    layout_from_protocol,
    protocol_spec_from_experiment,
    run_wet_leg,
)
from expos.kernel.objects import (
    Budget,
    Candidate,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    InstrumentMeta,
    LayoutMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    TrustLevel,
    VariableDef,
)
from expos.kernel.claims import ClaimDecisionStatus
from expos.qc.certification_stats import (
    AggregationConfig,
    ClaimHead,
    aggregate_round,
)

# Two polar (higher-polarity) and two nonpolar candidates -> a two-solvent-per-arm
# polarity contrast. Two candidates per arm x 3 replicates = 6 paired observations
# per round, the pair count a two-round decisive verdict needs (a single candidate
# per arm x 3 replicates gives only 3 pairs, below what the sign-flip calibrator
# can push past 1/alpha -- see the module-level reachability note).
_FOCAL_SOLVENTS = ["ethanol", "methanol"]  # polar
_REFERENCE_SOLVENTS = ["hexane", "toluene"]  # nonpolar
# Deliberately arm-CONTIGUOUS order (focal, focal, reference, reference) so the
# interleave's balancing is tested on its worst case for the arm split.
_SOLVENTS = _FOCAL_SOLVENTS + _REFERENCE_SOLVENTS

_FOCAL = tuple(f"cand_{s}" for s in _FOCAL_SOLVENTS)
_REFERENCE = tuple(f"cand_{s}" for s in _REFERENCE_SOLVENTS)


def _conditions(solvent: str) -> dict:
    return {
        "solvent": solvent,
        "concentration": 5.0,
        "temperature": 25.0,
        "incubation_time": 30.0,
    }


def _wet_experiment(solvents: list[str]) -> ExperimentObject:
    return ExperimentObject(
        exp_id="k_rep",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="response", metric=WET_METRIC),
        design_space=DesignSpace(
            name="solvent_screen",
            variables=[
                VariableDef(
                    name="solvent", kind="categorical", choices=list(solvents)
                ),
            ],
        ),
        active_vars=["solvent"],
        candidates=[
            Candidate(cand_id=f"cand_{s}", params=_conditions(s)) for s in solvents
        ],
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="test"),
    )


def _arm_indicator(cand_id: str | None) -> int:
    return 1 if cand_id in _FOCAL else 0


def _corr(xs: list[float], ys: list[float]) -> float:
    return float(np.corrcoef(np.asarray(xs, float), np.asarray(ys, float))[0, 1])


def _polar_claim() -> ClaimHead:
    """The seed claim: polar solvents respond HIGHER (focal = the polar arm)."""
    return ClaimHead(
        claim_id="c_polar_responds_higher",
        statement="polar solvents give a higher plate-reader response",
        favorable_direction="higher",
        focal_group=_FOCAL,
        reference_group=_REFERENCE,
    )


# ---------------------------------------------------------------- reader fixture


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def reader():
    """In-process plate reader on a free port (deterministic, noiseless)."""
    port = _free_port()
    srv = sim_reader.serve("127.0.0.1", port, seed=7, noise_sd=0.0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.05)
    yield port
    srv.shutdown()
    srv.server_close()


# ============================================================ 1. regression gate


def test_default_compile_and_layout_are_pre_kf_single_well_plate():
    """The hard regression gate: default args reproduce the pre-K-F single-well
    plate BIT-FOR-BIT. One well per candidate, no ``-r`` suffix on any custody id,
    and the layout mirrors ``otp.wells`` one-for-one (same well ids, arms, coords).
    """
    exp = _wet_experiment(_SOLVENTS)
    otp = compile_wet(exp)  # defaults: n_replicates=1, interleave=False
    layout = layout_from_protocol(otp)  # defaults

    assert len(otp.wells) == len(_SOLVENTS)  # exactly one well per candidate
    assert all("-r" not in w.sample_id for w in otp.wells)  # no replicate suffix

    # the spec builder default is likewise unchanged (no suffix, one per candidate).
    spec = protocol_spec_from_experiment(exp)
    assert len(spec.samples) == len(_SOLVENTS)
    assert all(s.replicate is None for s in spec.samples)

    # layout mirrors the deck one-for-one.
    assert len(layout.wells) == len(otp.wells)
    for wa, wp in zip(layout.wells, otp.wells):
        assert wa.well_id == wp.well_id
        assert wa.cand_id == wp.cand_id
        assert wa.control_id == wp.control_id
        assert wa.block_id == ""  # no replicate grouping in the default plate


# ============================================================ 2. real-pipeline plumbing


def test_replicate_interleaved_substrate_through_real_reader(reader):
    """A 3-replicate interleaved protocol driven end-to-end through the real reader:
    exactly 3 wells per candidate, an INDEPENDENT four-segment custody chain per
    replicate well (distinct sample ids), and a BALANCED capture order --
    corr(capture_index, arm) ~= 0 -- the design cure for the letter-075 confound."""
    exp = _wet_experiment(_SOLVENTS)
    otp = compile_wet(exp, n_replicates=3, interleave=True)

    assert len(otp.wells) == 3 * len(_SOLVENTS)  # 12 wells: 4 candidates x 3
    per_cand = Counter(w.cand_id for w in otp.wells)
    assert set(per_cand.values()) == {3}  # every candidate gets exactly 3 wells

    # each replicate owns a DISTINCT, independent custody key.
    sample_ids = [w.sample_id for w in otp.wells]
    assert len(set(sample_ids)) == len(sample_ids)
    for cand in (f"cand_{s}" for s in _SOLVENTS):
        reps = sorted(w.sample_id for w in otp.wells if w.cand_id == cand)
        assert reps == [f"SMP-CND-{cand}-r{k}" for k in range(3)]

    layout = layout_from_protocol(otp)  # default mirror of the replicated deck
    exp = exp.model_copy(update={"layout": layout})
    obs, result = run_wet_leg(exp, otp, port=reader, calibrate=True)
    assert result.outcome is GoalState.SUCCEEDED

    # every replicate well carries a fully-traced, independent four-segment chain.
    for rd in result.readings:
        segs = result.custody.trace(rd.sample_id).segments_complete()
        assert all(segs.values()), (rd.sample_id, segs)

    # balanced plate order: measurement sequence is uncorrelated with the arm split.
    caps = [r.seq or 0 for r in result.readings]
    arms = [_arm_indicator(r.cand_id) for r in result.readings]
    assert abs(_corr(caps, arms)) < 0.1

    # and the ingested observations preserve the same 3-per-candidate arm counts.
    obs_per_cand = Counter(o.cand_id for o in obs)
    assert set(obs_per_cand.values()) == {3}


def test_sequential_multireplicate_reintroduces_the_confound(reader):
    """Discriminative twin of the balanced case: the SAME 3 replicates laid out
    WITHOUT interleaving (candidate-major, each candidate's wells contiguous)
    re-clusters the arms in capture order -- corr(capture_index, arm) is large --
    proving the balance is bought by the interleave, not by replication alone."""
    exp = _wet_experiment(_SOLVENTS)
    otp = compile_wet(exp, n_replicates=3, interleave=False)
    layout = layout_from_protocol(otp)
    exp = exp.model_copy(update={"layout": layout})
    _obs, result = run_wet_leg(exp, otp, port=reader, calibrate=True)
    assert result.outcome is GoalState.SUCCEEDED

    caps = [r.seq or 0 for r in result.readings]
    arms = [_arm_indicator(r.cand_id) for r in result.readings]
    assert abs(_corr(caps, arms)) > 0.3  # confounded: order tracks the arm split


# ============================================================ 3. negative sample (085)


def _trusted_obs(
    oid: str,
    cand_id: str,
    value: float,
    cap: int,
    *,
    well_id: str = "",
    row: int = 0,
    col: int = 0,
    exp_id: str = "sub",
) -> ObservationObject:
    """A minimal TRUSTED wet observation in arm ``cand_id`` at capture order ``cap``."""
    return ObservationObject(
        obs_id=oid,
        exp_id=exp_id,
        round_id=0,
        cand_id=cand_id,
        result=MeasuredResult(metric=WET_METRIC, value=value),
        layout_meta=LayoutMeta(well_id=well_id or oid, row=row, col=col),
        instrument_meta=InstrumentMeta(capture_index=cap),
        trust=TrustLevel.TRUSTED,
    )


def test_single_well_sequential_substrate_is_confound_refused():
    """The letter-085 negative sample, made concrete: single-candidate arms, ONE
    well per candidate, measured in arm order (reference first, focal last). Fed to
    the aggregator this is REFUSED to insufficient on BOTH failure channels 085
    named -- n_pairs=1 (no CS, ci None, per-round e=0 so the e-product is frozen at
    1.0) AND corr(capture_index, arm)=+-1 tripping the plate-order guard. This is
    the ready-made 'delete the guard / ignore the pair count and it goes red' case.
    """
    head = ClaimHead(
        claim_id="c_single",
        statement="polar higher",
        favorable_direction="higher",
        focal_group=("cand_focal",),
        reference_group=("cand_reference",),
    )
    cfg = AggregationConfig()
    state = None
    for r in range(2):
        # sequential single-well plate: reference at capture 0, focal at capture 1.
        round_obs = [
            _trusted_obs(f"r{r}_ref", "cand_reference", 0.90, 0, exp_id="neg"),
            _trusted_obs(f"r{r}_foc", "cand_focal", 0.40, 1, exp_id="neg"),
        ]
        delta, agg = aggregate_round(round_obs, head, cfg, state)
        state = agg.state
        stat = delta.provenance.statistic
        assert delta.status is ClaimDecisionStatus.INSUFFICIENT
        assert stat.confound_suspect is True  # corr(capture, arm) = +-1
        assert stat.ci_low is None  # no CS: single pair, se = inf
        assert agg.round_e_value == 0.0  # a single sign has nothing to permute

    # the e-product never left its 1.0 floor -> decisive verdict unreachable.
    assert state.e_product == 1.0


# ============================================================ 4. K1 reachability


def test_k1_multireplicate_interleaved_reaches_decisive_contrary():
    """The reason this batch exists. Build the 3-replicate interleaved substrate
    with the real ``layout_from_protocol`` expansion, then feed two strong rounds of
    FLIPPED-face wet data (the polar/focal arm reads LOWER, contradicting the seeded
    'polar higher') straight into ``aggregate_round``. Assertions pin the exact
    reachability 085 lacked:

      * the per-round e is a genuine value > 1 (not the frozen 0 of the single-well
        plate), and the e-PRODUCT accumulates ACROSS 1/alpha over the two rounds;
      * after two strong rounds the verdict is DECISIVE (not insufficient) and, on
        this flipped face, REJECTED -- i.e. CONTRARY to the seed;
      * zero injection / zero confound: the recorded effect is negative and the
        plate-order balance is ~0 (the interleave did its job).
    """
    exp = _wet_experiment(_SOLVENTS)
    otp = compile_wet(exp)  # a plain one-well-per-candidate protocol...
    # ...expanded into the 3-replicate interleaved substrate at the layout level.
    layout = layout_from_protocol(otp, n_replicates=3, interleave=True)
    assert len(layout.wells) == 3 * len(_SOLVENTS)
    assert set(Counter(w.cand_id for w in layout.wells).values()) == {3}

    # the layout order balances the arm split (design cure for the 075 confound).
    caps0 = list(range(len(layout.wells)))
    arms0 = [_arm_indicator(w.cand_id) for w in layout.wells]
    assert abs(_corr(caps0, arms0)) < 0.1

    head = _polar_claim()
    cfg = AggregationConfig()

    def _round(tag: str, seed: int) -> list[ObservationObject]:
        # FLIPPED face: focal (polar) reads LOWER than reference (nonpolar). Capture
        # index follows the interleaved layout order, so plate order stays balanced.
        rng = np.random.default_rng(seed)
        out: list[ObservationObject] = []
        for cap, w in enumerate(layout.wells):
            base = 0.40 if w.cand_id in _FOCAL else 0.90
            out.append(
                _trusted_obs(
                    f"{tag}_{cap:03d}",
                    w.cand_id,
                    base + float(rng.normal(0, 0.03)),
                    cap,
                    well_id=w.well_id,
                    row=w.row,
                    col=w.col,
                    exp_id="k1",
                )
            )
        return out

    state = None
    e_trajectory: list[float] = []
    statuses: list[str] = []
    delta = None
    for r in range(2):
        delta, agg = aggregate_round(_round(f"r{r}", r + 1), head, cfg, state)
        state = agg.state
        e_trajectory.append(state.e_product)
        statuses.append(delta.status.value)
        assert agg.round_e_value > 1.0  # a genuine per-round e, never the 085 zero

    # e-product accumulates and CROSSES the eligibility threshold on round 2.
    assert e_trajectory[0] < cfg.e_threshold  # one strong round alone: not yet
    assert e_trajectory[1] >= cfg.e_threshold  # ...the product crosses it
    assert e_trajectory[1] > e_trajectory[0]  # genuinely accumulating

    # round 1 = insufficient (only one round < r_min); round 2 = DECISIVE, contrary.
    assert statuses[0] == "insufficient"
    assert statuses[1] == "rejected"  # decisive AND contrary to the seed
    assert delta.status.value != "insufficient"

    stat = delta.provenance.statistic
    assert stat.effect_estimate < 0.0  # polar arm reads lower -> contrary
    assert stat.confound_suspect is False
    assert abs(stat.plate_order_balance) < 0.1  # interleave balanced the order
