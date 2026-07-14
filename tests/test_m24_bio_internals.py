"""M24 biological dry-leg internals + wet expression faces + plate_offset fault.

Contract-INDEPENDENT slice (does NOT depend on the Contract-v3 ComputeTarget shape;
only on the existing ComputeResult/ExecutionAdapter/TruthSurface mechanisms):

  1. four sequence-feature proxies (sequences.py) each discriminate correctly, each
     honestly labelled a proxy (GC exact / CAI monotone in optimal codons / RBS strong
     vs weak / folding fallback does not crash) + the expression_proxy synthesis;
  2. SequenceProxyAdapter is synchronous & deterministic (same sequence -> bit-equal);
  3. the three biological truth faces (expression_high positive + only-mu-differs /
     expression_flipped negative / flat reused zero), chemistry anchors byte-unchanged;
  4. the plate_offset reader fault is a board-level constant step (NOT the per-well
     monotonic calibration_drift), with truth isolation intact (OS reading carries no
     plate_offset; the truth sidecar records it).
"""

from __future__ import annotations

import json
import socket
import threading
import time

import numpy as np
import pytest

from expos.adapters.dry.sequence_adapter import SequenceProxyAdapter, sequence_params
from expos.adapters.dry.sequences import (
    cai,
    expression_features,
    folding_dg,
    gc_content,
    rbs_strength,
)
from expos.adapters.wet import sim_reader
from expos.adapters.wet.screen import P_TARGET_HI, P_TARGET_LO
from expos.kernel.objects import (
    Budget,
    Candidate,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutAssignment,
    Objective,
    VariableDef,
    WellAssignment,
)


def _realised(coord: float) -> float:
    """The mixable-window mapping (coord in [0,1] -> realised design coordinate)."""
    return P_TARGET_LO + coord * (P_TARGET_HI - P_TARGET_LO)


# ============================================================ 1. four proxies


def test_gc_content_is_exact_count():
    """GC is the one exact (non-approximate) feature: a pure character count."""
    assert gc_content("GGCC") == 1.0
    assert gc_content("ATAT") == 0.0
    assert gc_content("ATGC") == 0.5
    assert gc_content("atgc") == 0.5  # case-insensitive
    assert gc_content("") == 0.0  # empty -> defined 0.0, never a crash


def test_cai_monotone_in_optimal_codons():
    """CAI rises toward 1.0 as codons are swapped for the optimal synonym, and the
    single-codon amino acids (Met/Trp) + stops are excluded (no scorable codon -> 0.0)."""
    optimal = cai("CTG" * 6)   # Leu optimal (w=1.0) -> CAI == 1.0
    rare = cai("CTA" * 6)      # Leu rare (w=0.007) -> CAI far below 1
    assert optimal == pytest.approx(1.0)
    assert optimal > rare
    assert rare < 0.05
    # Met (ATG) / Trp (TGG) / stop have no synonym -> excluded from the geometric mean.
    assert cai("ATG") == 0.0
    assert cai("ATGTGGTAA") == 0.0


def test_rbs_strength_distinguishes_strong_from_weak():
    """A 5'UTR carrying a strong SD (reverse-complement of the anti-SD anchor) at the
    optimal ~5 nt spacing scores far above a spacer with no SD. Crude proxy, but ordered."""
    strong = rbs_strength("TAAGGAGGT" + "AAAAA", start_codon="ATG")   # ideal SD + spacing 5
    weak = rbs_strength("AAAAAAAAAAAAAA", start_codon="ATG")          # no SD motif
    assert strong > weak
    assert strong == pytest.approx(1.0)   # perfect match x spacing 5 x ATG
    # start-codon preference: ATG beats a weak alternative on the same region.
    assert rbs_strength("TAAGGAGGT" + "AAAAA", start_codon="TTG") < strong


def test_folding_dg_fallback_does_not_crash_and_orders_structure():
    """With no ViennaRNA wheel the heuristic fallback must not crash, must return a
    float, and must rank a GC-rich hairpin as MORE negative (more stable) than polyA."""
    hairpin = folding_dg("GGGGGGGGGG" + "CCCCCCCCCC")   # long complementary stem
    polya = folding_dg("AAAAAAAAAAAAAAAAAAAA")           # no structure, GC=0
    assert isinstance(hairpin, float) and isinstance(polya, float)
    assert polya == pytest.approx(0.0)
    assert hairpin < polya      # structured 5'UTR -> more negative Delta G
    assert folding_dg("") == 0.0  # empty -> defined, never a crash


def test_expression_proxy_synthesis_orders_constructs():
    """The synthesised main scalar orders a well-designed construct above a poor one and
    stays in [0,1]; the four raw features all ride along in secondary()."""
    good = expression_features(
        sequence="TAAGGAGGTAAAAA" + "ATG" + "CTG" * 5,
        rbs="TAAGGAGGTAAAAA",
        cds="ATG" + "CTG" * 5,
    )
    bad = expression_features(
        sequence="AAAAAAAAAAAAAA" + "ATG" + "CTA" * 5,
        rbs="AAAAAAAAAAAAAA",
        cds="ATG" + "CTA" * 5,
    )
    good_seq = "TAAGGAGGTAAAAA" + "ATG" + "CTG" * 5
    assert good.expression_proxy > bad.expression_proxy
    assert 0.0 <= good.expression_proxy <= 1.0
    assert set(good.secondary()) == {
        "gc", "cai", "rbs_strength", "folding_dg", "transcript_length",
    }
    assert good.transcript_length == len(good_seq)
    assert good.secondary()["transcript_length"] == float(len(good_seq))


# ============================================================ 2. adapter determinism


def test_sequence_proxy_adapter_is_deterministic():
    """Same construct params -> bit-identical features on two independent compute calls
    (synchronous, no PySCF / subprocess / sbatch; no rng dependence)."""
    adapter = SequenceProxyAdapter()
    params = sequence_params(
        sequence="TAAGGAGGTAAAAA" + "ATG" + "GTTGCTAAA",
        rbs="TAAGGAGGTAAAAA",
        cds="ATG" + "GTTGCTAAA",
        construct_id="c1",
    )
    first = adapter.compute(params)
    second = adapter.compute(params)
    assert first == second  # frozen dataclass equality == bit-for-bit
    # capability declaration is the fixed doc literal (v3 will converge to a constant).
    assert adapter.ACCEPTS_INPUT_KINDS == ("sequence_construct", "sequence_features")


def test_sequence_proxy_adapter_execute_emits_value_and_secondary_no_truth():
    """The synchronous execute face (ExecutionAdapter protocol) emits one RawResult per
    well with value=expression_proxy + the four secondary channels, and NO truth_records
    (a dry observation never produces a truth sidecar). rng is accepted but unused."""
    seq = "TAAGGAGGTAAAAA" + "ATG" + "CTG" * 4
    exp = ExperimentObject(
        exp_id="m24_seq",
        round_id=0,
        domain="cell_free_expression_screen",
        objective=Objective(name="expression", metric="expression_proxy"),
        design_space=DesignSpace(
            name="cell_free_expression_screen",
            variables=[VariableDef(name="construct", kind="categorical", choices=["c1"])],
        ),
        active_vars=["construct"],
        candidates=[Candidate(cand_id="cand_c1", params=sequence_params(
            sequence=seq, rbs="TAAGGAGGTAAAAA", cds="ATG" + "CTG" * 4))],
        layout=LayoutAssignment(
            rows=1, cols=1, seed=0,
            wells=[WellAssignment(well_id="A1", row=0, col=0, cand_id="cand_c1")],
        ),
        budget=Budget(wells_total=1, rounds_total=1),
        execution_req=ExecutionReq(adapter="sequence_proxy"),
        provenance=DesignProvenance(generator="test"),
    )
    before = exp.model_dump()
    res = SequenceProxyAdapter().execute(exp, np.random.default_rng(0))
    assert res.truth_records is None
    assert len(res.raw_results) == 1
    raw = res.raw_results[0]
    assert raw.metric == "expression_proxy"
    assert raw.value == pytest.approx(expression_features(
        sequence=seq, rbs="TAAGGAGGTAAAAA", cds="ATG" + "CTG" * 4).expression_proxy)
    assert set(raw.secondary) == {
        "gc", "cai", "rbs_strength", "folding_dg", "transcript_length",
    }
    assert exp.model_dump() == before  # adapter never mutates the exp


# ============================================================ 3. wet expression faces


def test_expression_high_positive_sign_and_only_mu_differs():
    """expression_high (mu=0.85) is a positive design-coord->response face and obeys the
    only-mu-differs law across domains: amplitude/sigma/baseline identical to the solvent
    signal face; polar_high stays bit-for-bit (M16 anchor)."""
    face = sim_reader.TruthSurface.from_profile("expression_high")
    polar = sim_reader.TruthSurface.from_profile("polar_high")
    assert face.mu == 0.85

    coords = [0.0, 0.25, 0.5, 0.75, 1.0]
    resp = [face.response(_realised(c)) for c in coords]
    assert all(b > a for a, b in zip(resp, resp[1:]))          # strictly increasing
    assert np.corrcoef(coords, resp)[0, 1] > 0.85             # clearly positive
    # only-mu-differs + solvent regression anchor untouched.
    assert (face.amplitude, face.sigma, face.baseline) == (
        polar.amplitude, polar.sigma, polar.baseline)
    assert polar == sim_reader.TruthSurface()


def test_expression_flipped_negative_sign_and_only_mu_differs():
    """expression_flipped (mu=0.20) mirrors it: response DECREASES with design coord --
    low-design constructs express highest, contradicting the seed -- while amplitude/
    sigma/baseline stay identical and expression_high is untouched."""
    low = sim_reader.TruthSurface.from_profile("expression_flipped")
    high = sim_reader.TruthSurface.from_profile("expression_high")
    assert low.mu == 0.20

    coords = [0.0, 0.25, 0.5, 0.75, 1.0]
    resp = [low.response(_realised(c)) for c in coords]
    assert all(b < a for a, b in zip(resp, resp[1:]))          # strictly decreasing
    assert np.corrcoef(coords, resp)[0, 1] < -0.85            # clearly negative
    assert (low.amplitude, low.sigma, low.baseline) == (
        high.amplitude, high.sigma, high.baseline)


def test_flat_reused_and_chemistry_anchors_unchanged():
    """flat is the reused cross-domain null (zero amplitude), and adding the two
    expression faces did not perturb any chemistry anchor (byte regression)."""
    flat = sim_reader.TruthSurface.from_profile("flat")
    assert flat.amplitude == 0.0
    resp = [flat.response(p / 20.0) for p in range(21)]
    assert len(set(resp)) == 1 and resp[0] == flat.baseline
    # chemistry / solvent anchors byte-unchanged
    assert sim_reader.TRUTH_PROFILES["polar_high"] == 0.55
    assert sim_reader.TRUTH_PROFILES["catalyst_high"] == 0.85
    assert sim_reader.TRUTH_PROFILES["catalyst_low"] == 0.20
    assert sim_reader.DEFAULT_TRUTH_PROFILE == "polar_high"
    # unknown profile still fails loudly (never a silent fallback).
    with pytest.raises(ValueError):
        sim_reader.TruthSurface.from_profile("no_such_face")


# ============================================================ 4. plate_offset fault


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


def _send(port: int, obj: dict, timeout: float = 2.0) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(obj) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError("closed without reply")
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode())


@pytest.fixture
def reader():
    """In-process reader (noise_sd=0 -> exact reconstruction); yields its port."""
    port = _free_port()
    srv = sim_reader.serve("127.0.0.1", port, seed=7, noise_sd=0.0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _wait_port(port)
    yield port
    srv.shutdown()
    srv.server_close()


def _measure(port, lease, sample):
    return _send(port, {"cmd": "measure", "lease_id": lease, "samples": [sample]})


def test_plate_offset_is_board_level_constant_not_per_well_monotonic(reader):
    """plate_offset is a per-PLATE CONSTANT step (differs in form from the per-well
    MONOTONIC calibration_drift): every well on plateA carries the SAME +0.08 regardless
    of measurement order, while plateB carries -0.04 and an unassigned well carries 0.0.
    Meanwhile the drift offset field keeps rising per well -- the two faults are distinct."""
    lease = _send(reader, {"cmd": "acquire", "holder": "A"})["lease_id"]
    _send(reader, {"cmd": "calibrate", "lease_id": lease})
    _send(reader, {"cmd": "inject", "plate_offsets": {"plateA": 0.08, "plateB": -0.04}})

    for i in range(3):  # three wells on plateA, increasing capture order
        _measure(reader, lease, {"sample_id": f"SMP-A{i}", "well_id": f"A{i}",
                                 "polarity": 0.55, "plate_id": "plateA"})
    _measure(reader, lease, {"sample_id": "SMP-B0", "well_id": "B0",
                             "polarity": 0.55, "plate_id": "plateB"})
    _measure(reader, lease, {"sample_id": "SMP-N0", "well_id": "N0",
                             "polarity": 0.55})  # no plate_id -> zero offset

    recs = _send(reader, {"cmd": "truth_dump"})["truth_records"]
    a_recs = [r for r in recs if r["plate_id"] == "plateA"]
    b_recs = [r for r in recs if r["plate_id"] == "plateB"]
    n_recs = [r for r in recs if r["plate_id"] == ""]

    # board-level CONSTANT: identical offset on every plateA well, across capture order.
    assert [r["plate_offset"] for r in a_recs] == [0.08, 0.08, 0.08]
    assert all("plate_offset" in r["artifacts"] for r in a_recs)
    assert b_recs[0]["plate_offset"] == -0.04
    # unassigned well: no offset, no tag.
    assert n_recs[0]["plate_offset"] == 0.0
    assert "plate_offset" not in n_recs[0]["artifacts"]
    # DISTINCT from drift: the per-well drift offset rises monotonically while
    # plate_offset stays flat -- the two are not the same mechanism.
    drift_offsets = [r["offset"] for r in a_recs]
    assert all(b > a for a, b in zip(drift_offsets, drift_offsets[1:]))


def test_plate_offset_truth_isolation_os_reading_clean_sidecar_records(reader):
    """Truth isolation: the OS-visible reading carries the CORRUPTED value but NO
    plate_offset field; the offset truth lives only in the server sidecar, and the value
    provably includes it (value == true*gain + offset + noise + plate_offset)."""
    lease = _send(reader, {"cmd": "acquire", "holder": "A"})["lease_id"]
    _send(reader, {"cmd": "calibrate", "lease_id": lease})
    _send(reader, {"cmd": "inject", "plate_offsets": {"plateA": 0.08}})

    reading = _measure(reader, lease, {
        "sample_id": "SMP-A0", "well_id": "A1", "polarity": 0.55, "plate_id": "plateA",
    })["readings"][0]
    # OS reading: no truth/fault fields leak (no plate_offset / true_response / gain).
    for k in ("plate_offset", "true_response", "gain", "offset", "noise"):
        assert k not in reading

    rec = _send(reader, {"cmd": "truth_dump"})["truth_records"][-1]
    assert rec["plate_offset"] == 0.08
    # the OS value equals the full corrupted reconstruction INCLUDING plate_offset.
    expected = rec["true_response"] * rec["gain"] + rec["offset"] + rec["noise"] + 0.08
    assert reading["value"] == pytest.approx(round(expected, 6))
    # removing the offset would NOT reproduce the value -> it is genuinely applied.
    assert reading["value"] != pytest.approx(round(expected - 0.08, 6))
