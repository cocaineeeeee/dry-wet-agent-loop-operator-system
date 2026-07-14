"""M17 K-D — flipped truth-surface discriminator domain (K1/K2 groundwork).

This is the ground-floor acceptance layer for the K1 gate of docs/M17_KNOWLEDGE_
FEEDBACK.md. K1 asks: with ZERO external injection, does round-1 TRUSTED wet
evidence that CONTRADICTS the seeded ledger claim ("polar-higher supported") get
adjudicated as contrary once the Evidence-to-Claim Compiler (K-A/K-B/K-C, session
B) lands? Before that compiler exists we must first prove the *substrate*: a wet
domain whose hidden truth face genuinely contradicts the seed, driven end-to-end
through the real wet pipeline, with the contradiction visible in TRUSTED data.

Three assertions, matching the K-D brief:
  (a) FLIP: on the ``nonpolar_high`` reader face, the empirical response-vs-polarity
      correlation over TRUSTED observations is NEGATIVE -- the mirror of the normal
      ``polar_high`` face's positive correlation. Same solvent batch, two faces.
  (b) DEFAULT-FACE REGRESSION: not passing ``truth_profile`` reproduces the M16
      surface bit-for-bit (default serve() == explicit ``polar_high`` serve()), and
      the normal-face unimodal semantics (mid-polarity near optimum > nonpolar tail)
      still hold.
  (c) TRUTH STAYS OFF THE OS PATH: the flipped face changes the hidden surface only;
      OS-visible observations carry NO truth fields, and the truth sidecar remains an
      INDEPENDENT harvest channel (sim_reader.harvest_truth), unchanged invariant.

K-E / K-C statistical口径 (see the module-level _corr helper and dimkd_handoff.md):
the discriminating statistic is the SIGN of the Pearson correlation between public
solvent polarity and TRUSTED response, with |r| >= 0.5 as the "clear direction"
effect-size gate. Deterministic at noise_sd=0:
    polar_high    -> r ~= +0.335  (positive; supports polar-higher)
    nonpolar_high -> r ~= -0.891  (negative; supports nonpolar-higher, contrary)
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from expos.adapters.wet import sim_reader
from expos.adapters.wet.driver import GoalState
from expos.adapters.wet.screen import (
    SOLVENT_POLARITY,
    WET_METRIC,
    compile_wet,
    layout_from_protocol,
    run_wet_leg,
)
from expos.kernel.lifecycle import TrustPolicy
from expos.kernel.objects import (
    Budget,
    Candidate,
    Control,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    Objective,
    TrustLevel,
    VariableDef,
)
from expos.kernel.store import RunStore
from expos.qc.checks import run_qc
from expos.qc.policy import QCPolicy

# Same 8-solvent batch used for BOTH faces (the K-D "same batch, two faces" rule).
_BATCH = [
    "hexane",
    "toluene",
    "acetone",
    "acetonitrile",
    "ethanol",
    "methanol",
    "dmso",
    "water",
]


# ---------------------------------------------------------------- reader helpers


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"reader on port {port} did not come up")


class _Reader:
    """Context manager: an in-process reader on a free port for one truth face."""

    def __init__(self, truth_profile: str | None = None) -> None:
        self._profile = truth_profile

    def __enter__(self) -> int:
        self.port = _free_port()
        # Explicitly exercise the default path when truth_profile is None (K1 (b)).
        if self._profile is None:
            self.srv = sim_reader.serve("127.0.0.1", self.port, seed=7, noise_sd=0.0)
        else:
            self.srv = sim_reader.serve(
                "127.0.0.1",
                self.port,
                seed=7,
                noise_sd=0.0,
                truth_profile=self._profile,
            )
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()
        _wait_port(self.port)
        return self.port

    def __exit__(self, *exc: object) -> None:
        self.srv.shutdown()
        self.srv.server_close()


# ---------------------------------------------------------------- domain helpers


def _design_space() -> DesignSpace:
    return DesignSpace(
        name="solvent_screen",
        variables=[
            VariableDef(name="solvent", kind="categorical", choices=list(_BATCH)),
            VariableDef(
                name="concentration",
                kind="continuous",
                low=0.5,
                high=20.0,
                transform="log",
                unit="mM",
            ),
            VariableDef(
                name="temperature", kind="continuous", low=15.0, high=45.0, unit="C"
            ),
            VariableDef(
                name="incubation_time",
                kind="continuous",
                low=5.0,
                high=120.0,
                unit="min",
            ),
        ],
    )


def _conditions(solvent: str) -> dict:
    return {
        "solvent": solvent,
        "concentration": 5.0,
        "temperature": 25.0,
        "incubation_time": 30.0,
    }


def _wet_experiment(solvents: list[str]) -> tuple[ExperimentObject, object]:
    cands = [Candidate(cand_id=f"cand_{s}", params=_conditions(s)) for s in solvents]
    controls = [
        Control(control_id="ctl0", kind="sentinel", params=_conditions("acetonitrile"))
    ]
    exp0 = ExperimentObject(
        exp_id="k_flip",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="response", metric=WET_METRIC),
        design_space=_design_space(),
        active_vars=["solvent"],
        candidates=cands,
        controls=controls,
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="test"),
    )
    otp = compile_wet(exp0)
    layout = layout_from_protocol(otp)
    return exp0.model_copy(update={"layout": layout}), otp


def _qc_runner(metric_range):
    def runner(exp, obs_list, history):
        same = [o for o in (history or []) if o.exp_id == exp.exp_id]
        return run_qc(
            exp, obs_list, same or None, metric_range=metric_range, moran_perm=99
        )

    return runner


def _run_and_judge(port: int, solvents: list[str], tmp_path) -> list:
    """Drive the full wet leg through QC/trust; return judged observations."""
    exp, otp = _wet_experiment(solvents)
    obs, result = run_wet_leg(exp, otp, port=port, calibrate=True)
    assert result.outcome is GoalState.SUCCEEDED
    store = RunStore(tmp_path / f"run_{port}")
    store.save_experiment(exp)
    QCPolicy(_qc_runner((0.0, 1.2)), TrustPolicy(0.6, 0.3)).judge(store, obs, exp)
    return obs


def _corr(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation (the K-E discriminating statistic). NaN-safe."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def _trusted_pol_resp(obs, solvents: list[str]) -> tuple[list[float], list[float]]:
    """(polarity, response) pairs restricted to TRUSTED, non-control observations."""
    cand_solvent = {f"cand_{s}": s for s in solvents}
    pol, resp = [], []
    for o in obs:
        if o.is_control or o.trust is not TrustLevel.TRUSTED:
            continue
        solvent = cand_solvent.get(o.cand_id)
        if solvent is None:
            continue
        pol.append(SOLVENT_POLARITY[solvent])
        resp.append(o.result.value)
    return pol, resp


# ================================================================ (a) the flip


def test_k1a_flipped_face_negative_correlation_mirrors_normal(tmp_path):
    """Same solvent batch measured on the two faces: the TRUSTED response-vs-
    polarity correlation flips sign -- POSITIVE on the seeded ``polar_high`` face,
    NEGATIVE on the ``nonpolar_high`` discriminator face. The negative correlation
    is the wet-data statement of "nonpolar-higher", contradicting the seeded
    "polar-higher supported" claim with zero external injection."""
    with _Reader("polar_high") as port:
        normal_obs = _run_and_judge(port, _BATCH, tmp_path)
    with _Reader("nonpolar_high") as port:
        flipped_obs = _run_and_judge(port, _BATCH, tmp_path)

    n_pol, n_resp = _trusted_pol_resp(normal_obs, _BATCH)
    f_pol, f_resp = _trusted_pol_resp(flipped_obs, _BATCH)
    assert len(n_pol) >= 3 and len(f_pol) >= 3, "need TRUSTED points on both faces"

    r_normal = _corr(n_pol, n_resp)
    r_flipped = _corr(f_pol, f_resp)

    # The seeded (normal) face supports polar-higher: positive correlation.
    assert r_normal > 0.0, f"normal face must be positive, got {r_normal:.4f}"
    # The discriminator face contradicts it: clearly negative (|r| >= 0.5 gate).
    assert r_flipped < -0.5, (
        f"flipped face must be strongly negative, got {r_flipped:.4f}"
    )
    # Opposite signs == the mirror the K1 discriminator is built on.
    assert (r_normal > 0) and (r_flipped < 0), (r_normal, r_flipped)


def test_k1a_flipped_face_ranks_nonpolar_highest(tmp_path):
    """A direct, correlation-free reading of the same flip: on the flipped face the
    LOWEST-polarity solvent (hexane) out-responds the HIGHEST-polarity solvent
    (water) among TRUSTED observations -- the opposite of the normal face."""
    with _Reader("nonpolar_high") as port:
        obs = _run_and_judge(port, ["hexane", "water"], tmp_path)
    by_solvent = {
        o.cand_id: o.result.value
        for o in obs
        if not o.is_control and o.trust is TrustLevel.TRUSTED
    }
    assert by_solvent.get("cand_hexane") is not None
    assert by_solvent.get("cand_water") is not None
    assert by_solvent["cand_hexane"] > by_solvent["cand_water"], (
        "nonpolar_high face: nonpolar hexane must out-respond polar water"
    )


# ================================================================ (b) regression


def test_k1b_default_serve_is_bit_identical_to_polar_high(tmp_path):
    """Not passing truth_profile reproduces the M16 surface bit-for-bit: default
    serve() and explicit serve(truth_profile='polar_high') yield byte-equal
    responses across the whole batch (the M16 zero-change guarantee, in one file)."""
    with _Reader(None) as port:
        default_obs = _run_and_judge(port, _BATCH, tmp_path)
    with _Reader("polar_high") as port:
        explicit_obs = _run_and_judge(port, _BATCH, tmp_path)

    default_vals = {o.cand_id: o.result.value for o in default_obs if not o.is_control}
    explicit_vals = {
        o.cand_id: o.result.value for o in explicit_obs if not o.is_control
    }
    assert default_vals == explicit_vals, "default face must equal polar_high exactly"


def test_k1b_default_face_keeps_m16_unimodal_semantics(tmp_path):
    """M16 normal-face semantics re-asserted on the default face: a mid-polarity
    solvent near the truth optimum (acetonitrile) out-responds the nonpolar tail
    (hexane) and lands inside the solvent_screen sentinel band [0.4, 1.15]."""
    with _Reader(None) as port:
        obs = _run_and_judge(port, ["hexane", "acetonitrile"], tmp_path)
    vals = {
        o.cand_id: o.result.value
        for o in obs
        if not o.is_control and o.trust is TrustLevel.TRUSTED
    }
    assert vals["cand_acetonitrile"] > vals["cand_hexane"]  # unimodal: mid > tail
    assert 0.4 <= vals["cand_acetonitrile"] <= 1.15  # M16 sentinel band


def test_k1b_truth_surface_profile_factory_defaults_to_m16(tmp_path):
    """The profile factory itself: default == polar_high == the M16 dataclass
    default (mu=0.55); an unknown profile fails LOUDLY, never a silent fallback."""
    from expos.adapters.wet.sim_reader import TruthSurface

    assert TruthSurface.from_profile() == TruthSurface()  # default is M16
    assert TruthSurface.from_profile("polar_high") == TruthSurface()
    assert TruthSurface.from_profile("polar_high").mu == 0.55
    assert TruthSurface.from_profile("nonpolar_high").mu == 0.20
    # amplitude/sigma/baseline are isomorphic across faces (only mu flips).
    a, b = (
        TruthSurface.from_profile("polar_high"),
        TruthSurface.from_profile("nonpolar_high"),
    )
    assert (a.amplitude, a.sigma, a.baseline) == (b.amplitude, b.sigma, b.baseline)
    with pytest.raises(ValueError):
        TruthSurface.from_profile("no_such_profile")


def test_k1b_flat_null_profile_is_polarity_independent_and_leaves_signals_intact():
    """The ``flat`` null profile (MR_null) at the TruthSurface layer: zero amplitude
    makes response(p) a polarity-INDEPENDENT constant (== baseline) for every p, so
    the face carries no signal to find. The signal faces are byte-unchanged by its
    addition (polar_high still == the M16 default surface; nonpolar_high still
    mu=0.20). Only the truth-function SHAPE changed; sigma/baseline -- and hence the
    caller's added-noise structure -- are untouched (K-D discipline)."""
    from expos.adapters.wet.sim_reader import (
        DEFAULT_TRUTH_PROFILE,
        TRUTH_PROFILES,
        TruthSurface,
    )

    flat = TruthSurface.from_profile("flat")
    assert flat.amplitude == 0.0
    # polarity-independent: identical response across the whole [0,1] polarity range
    resp = [flat.response(p / 20.0) for p in range(21)]
    assert len(set(resp)) == 1 and resp[0] == flat.baseline
    # noise-relevant shape params match the signal faces (only the SIGNAL is removed)
    sig = TruthSurface.from_profile("polar_high")
    assert (flat.sigma, flat.baseline) == (sig.sigma, sig.baseline)
    # adding flat did NOT perturb the default / signal faces (byte regression)
    assert DEFAULT_TRUTH_PROFILE == "polar_high"
    assert TruthSurface.from_profile() == TruthSurface()  # M16 default intact
    assert TruthSurface.from_profile("polar_high") == TruthSurface()
    assert TruthSurface.from_profile("nonpolar_high").mu == 0.20
    assert "flat" in TRUTH_PROFILES


# ================================================================ (c) truth off-path


def test_k1c_truth_never_on_os_path_and_harvest_is_independent(tmp_path):
    """The flipped face changes ONLY the hidden surface: (1) OS-visible observations
    carry NO truth fields (no true_response / gain / offset leak into the store),
    and (2) the truth sidecar remains an INDEPENDENT harvest channel that reflects
    the flipped surface (hexane's hidden true_response > water's), unchanged
    invariant from M16's fairness red line."""
    from expos.adapters.wet.sim_reader import harvest_truth

    with _Reader("nonpolar_high") as port:
        obs = _run_and_judge(port, _BATCH, tmp_path)

        # (1) no truth field reaches any OS observation
        leak_keys = {"true_response", "gain", "offset", "noise"}
        for o in obs:
            dumped = o.model_dump(mode="json")
            flat = repr(dumped)
            for k in leak_keys:
                assert k not in dumped, f"{k} leaked onto observation model"
            assert "true_response" not in flat

        # (2) harvest channel is independent AND reflects the flipped surface
        truth = harvest_truth(port=port)
    assert truth, "harvest_truth must still return the hidden sidecar"
    assert all("true_response" in rec for rec in truth)

    # hexane (target polarity ~0.30, near the flipped peak 0.20) has the HIGHER
    # hidden true_response than water (target ~0.75, far tail) -- the flip lives
    # in the sidecar, exactly where truth belongs.
    def _true_for(polarity_lo, polarity_hi):
        vals = [
            r["true_response"]
            for r in truth
            if polarity_lo <= r["polarity"] <= polarity_hi
        ]
        return max(vals) if vals else None

    low_true = _true_for(0.28, 0.36)  # hexane/toluene end
    high_true = _true_for(0.70, 0.80)  # water end
    assert low_true is not None and high_true is not None
    assert low_true > high_true, "flipped truth sidecar must peak at low polarity"


# ================================================================ (d) the null face

# Balanced plate order: the candidate measurement index is orthogonal to solvent
# polarity (Pearson corr(index, polarity) ~= 0). This is the standard control for
# the reader's calibration-drift artefact, which is a monotonic function of
# MEASUREMENT ORDER, not polarity. On a polarity-sorted plate the drift would ALIAS
# onto polarity; the balanced layout de-aliases it, so on the flat face -- whose
# truth response is a polarity-independent constant -- only the (absent) real signal
# is measured, reading as a clean zero.
_BALANCED_ORDER = [
    "hexane", "acetonitrile", "water", "ethanol",
    "dmso", "acetone", "methanol", "toluene",
]


def test_k1d_flat_null_face_carries_no_polarity_signal(tmp_path):
    """MR_null first realised form (no-signal null face): on the ``flat`` reader face
    the TRUSTED response-vs-polarity correlation is ~ZERO (|r| < 0.15; strictly 0 at
    noise_sd=0 on the drift-balanced plate) -- there is no direction to find, so a
    correct aggregator must return "insufficient" rather than fabricate a claim. The
    SAME balanced plate keeps a real signal fully visible on the polar_high face
    (|r| well clear of the gate), proving the flat zero is a property of the FACE,
    not of the layout suppressing signal."""
    with _Reader("flat") as port:
        flat_obs = _run_and_judge(port, _BALANCED_ORDER, tmp_path)
    with _Reader("polar_high") as port:
        signal_obs = _run_and_judge(port, _BALANCED_ORDER, tmp_path)

    f_pol, f_resp = _trusted_pol_resp(flat_obs, _BALANCED_ORDER)
    s_pol, s_resp = _trusted_pol_resp(signal_obs, _BALANCED_ORDER)
    assert len(f_pol) >= 3 and len(s_pol) >= 3, "need TRUSTED points on both faces"

    r_flat = _corr(f_pol, f_resp)
    r_signal = _corr(s_pol, s_resp)
    # null face: no signal -> essentially zero correlation ...
    assert abs(r_flat) < 0.15, f"flat null face must be ~0, got {r_flat:.6f}"
    # ... and strictly zero at noise_sd=0 on the drift-balanced plate.
    assert abs(r_flat) < 1e-6, f"balanced flat face must be strictly 0, got {r_flat!r}"
    # same layout, real signal survives -> the zero belongs to the FACE, not the plate
    assert abs(r_signal) >= 0.15, (
        f"balanced layout must still reveal a real signal, got {r_signal:.6f}"
    )


def test_k1e_polar_high_strong_gives_genuine_positive_eth_acn_contrast():
    """The SUPPORTED-path face (letter 093 ruling (b)): polar_high_strong lifts ONLY
    mu (0.55 -> 0.70), so the two live-promoted arms (ethanol/acetonitrile) land on a
    steep flank instead of straddling the peak. Three pins:
      (1) genuine positive contrast on the new face (eth - acn well above the ~0
          effect that makes polar_high honestly unadjudicable for this pair);
      (2) polar_high itself is BIT-FOR-BIT untouched (M16 regression anchor);
      (3) only-mu-differs law: amplitude/sigma/baseline identical across the two
          faces (the K-D discipline the variant must inherit)."""
    from expos.adapters.wet.screen import P_TARGET_HI, P_TARGET_LO, SOLVENT_POLARITY

    def realised(solvent: str) -> float:
        return P_TARGET_LO + SOLVENT_POLARITY[solvent] * (P_TARGET_HI - P_TARGET_LO)

    strong = sim_reader.TruthSurface.from_profile("polar_high_strong")
    default = sim_reader.TruthSurface.from_profile("polar_high")

    eth, acn = realised("ethanol"), realised("acetonitrile")
    # (1) genuine contrast on the strong face vs the ~0 effect on the default face.
    diff_strong = strong.response(eth) - strong.response(acn)
    diff_default = default.response(eth) - default.response(acn)
    assert diff_strong > 0.25, f"strong face must separate the arms, got {diff_strong:.4f}"
    assert abs(diff_default) < 0.05, (
        f"polar_high must keep its genuine ~0 eth/acn effect, got {diff_default:.4f}"
    )
    # (2) the M16 anchor face is untouched.
    assert sim_reader.TRUTH_PROFILES["polar_high"] == 0.55
    assert default == sim_reader.TruthSurface()  # dataclass defaults == M16 surface
    # (3) only mu differs.
    assert (strong.amplitude, strong.sigma, strong.baseline) == (
        default.amplitude, default.sigma, default.baseline,
    )
    assert strong.mu == 0.70
