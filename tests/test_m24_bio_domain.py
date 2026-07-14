"""M24 THIRD-domain (cell_free_expression_screen) discriminative domain-face suite.

The biological analogue of tests/test_m20_catalyst_domain.py. The point is NOT a demo
but the swappable-runtime proof for a structurally GEOMETRY-FREE domain: the SAME wet
generalization + truth surface + evidence compiler serve a biological domain with only
the domain tables (adapters/dry/constructs.py), one provider, one yaml, and a
readout-layer normalization changed -- and the kernel/evidence-compiler stay biology-blind.

Faces + facets covered:
  1. three discriminative wet faces: expression_high (positive coord->response,
     only-mu-differs across chemistry<->biology), expression_flipped (negative), flat
     (shared cross-domain null), chemistry anchors byte-unchanged;
  2. provider birth-time governance + validate_yaml + the compute_targets==wet_coords
     key invariant + compute_targets are sequence_construct (NO fabricated geometry);
  3. domain-neutral wet generalization: injected construct descriptors map each level to
     the correct realised coordinate, monotone;
  4. dry leg: strong-design construct expression_proxy > weak, strictly monotone,
     reproducing the committed ~0.68 vs ~0.20 gradient, correlated with the design coord;
  5. biology-blindness: the evidence compiler consumes ONLY value + TRUSTED obs and is
     structurally free of any biological literal (the red-line mirror of truth-blindness);
  6. readout normalization: percent-of-control uses the negative/positive control
     baselines, lives in the readout layer (imports no evidence-compiler symbol), and so
     runs BEFORE certification.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from expos.adapters.domain_provider import (
    INPUT_KIND_MOLECULAR_GEOMETRY,
    INPUT_KIND_SEQUENCE_CONSTRUCT,
    DomainProviderError,
    adapter_accepts_capability,
)
from expos.adapters.dry.constructs import (
    CONSTRUCT_DESCRIPTORS,
    CONSTRUCTS,
    construct_names,
    construct_params,
    expression_proxy_for,
)
from expos.adapters.dry.sequence_adapter import SequenceProxyAdapter
from expos.adapters.providers.cell_free_expression_screen import (
    CellFreeExpressionScreenProvider,
)
from expos.adapters.wet import bio_readout, sim_reader
from expos.adapters.wet.screen import (
    P_TARGET_HI,
    P_TARGET_LO,
    compile_wet,
    target_coord,
)
from expos.domain import DomainConfig
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
from expos.qc import certification_stats
from expos.qc.certification_stats import (
    AggregationConfig,
    ClaimHead,
    aggregate_round,
)

_YAML_PATH = "domains/cell_free_expression_screen.yaml"
#: construct levels sorted by ascending design coordinate (weakest -> strongest).
_BY_COORD = sorted(construct_names(), key=lambda c: CONSTRUCT_DESCRIPTORS[c]["coord"])
_COORDS_ASC = [CONSTRUCT_DESCRIPTORS[c]["coord"] for c in _BY_COORD]


def _realised(coord: float) -> float:
    """The mixable-window mapping target_coord applies (coord in [0,1])."""
    return P_TARGET_LO + coord * (P_TARGET_HI - P_TARGET_LO)


def _load_cfg() -> DomainConfig:
    """Structurally validate the domain yaml (all pydantic validators run). Full
    load_domain() additionally gates on the adapter registry + auto-loads the provider;
    the biological dry leg `sequence_proxy` awaits session-B dispatch registration (see
    report / yaml note), the exact staging precedent solvent/catalyst sat in. This path
    exercises the real yaml + real provider governance without that pending gate."""
    raw = yaml.safe_load(open(_YAML_PATH, encoding="utf-8"))
    return DomainConfig.model_validate(raw)


# ============================================================ 1. wet expression faces


def test_expression_high_face_positive_sign_and_only_mu_differs():
    """expression_high (mu=0.85) is a positive design-coord->response face -- strong-design
    constructs express highest -- and obeys the only-mu-differs law ACROSS domains:
    amplitude/sigma/baseline identical to the solvent signal face, polar_high bit-for-bit."""
    face = sim_reader.TruthSurface.from_profile("expression_high")
    polar = sim_reader.TruthSurface.from_profile("polar_high")
    assert face.mu == 0.85

    resp = [face.response(_realised(c)) for c in _COORDS_ASC]
    assert all(b > a for a, b in zip(resp, resp[1:]))          # strictly increasing
    assert np.corrcoef(_COORDS_ASC, resp)[0, 1] > 0.85         # clearly positive
    # highest design coordinate is the brightest construct.
    assert face.response(_realised(max(_COORDS_ASC))) > face.response(_realised(min(_COORDS_ASC)))

    # only-mu-differs (K-D law) holds across chemistry<->biology; solvent anchor untouched.
    assert (face.amplitude, face.sigma, face.baseline) == (
        polar.amplitude, polar.sigma, polar.baseline)
    assert polar == sim_reader.TruthSurface()  # dataclass defaults == M16 surface


def test_expression_flipped_face_flips_sign_only_mu_differs():
    """expression_flipped (mu=0.20) mirrors it: response DECREASES with design coord --
    weak-design constructs express highest, contradicting the seed -- while amplitude/
    sigma/baseline stay identical (only-mu-differs) and expression_high is untouched."""
    low = sim_reader.TruthSurface.from_profile("expression_flipped")
    high = sim_reader.TruthSurface.from_profile("expression_high")
    assert low.mu == 0.20

    resp = [low.response(_realised(c)) for c in _COORDS_ASC]
    assert all(b < a for a, b in zip(resp, resp[1:]))          # strictly decreasing
    assert np.corrcoef(_COORDS_ASC, resp)[0, 1] < -0.85        # clearly negative
    assert (low.amplitude, low.sigma, low.baseline) == (
        high.amplitude, high.sigma, high.baseline)


def test_flat_null_face_and_chemistry_anchors_unchanged():
    """flat is the reused cross-domain null (zero amplitude), so every construct shares
    one true mean; and adding the biological faces perturbed no chemistry anchor."""
    flat = sim_reader.TruthSurface.from_profile("flat")
    assert flat.amplitude == 0.0
    resp = [flat.response(_realised(c)) for c in _COORDS_ASC]
    assert len(set(round(r, 12) for r in resp)) == 1 and resp[0] == flat.baseline
    # chemistry / solvent anchors byte-unchanged.
    assert sim_reader.TRUTH_PROFILES["polar_high"] == 0.55
    assert sim_reader.TRUTH_PROFILES["catalyst_high"] == 0.85
    assert sim_reader.TRUTH_PROFILES["catalyst_low"] == 0.20
    assert sim_reader.DEFAULT_TRUTH_PROFILE == "polar_high"
    with pytest.raises(ValueError):  # unknown face still fails loudly
        sim_reader.TruthSurface.from_profile("no_such_face")


# ============================================================ 2. provider governance


def test_provider_passes_birth_time_governance():
    """The provider passes the birth-time completeness + cross-hook gate (compute_targets
    keys == wet_coords keys, the three expression faces non-empty, `flat` null declared,
    seed claims well-formed)."""
    p = CellFreeExpressionScreenProvider.check_complete()
    assert isinstance(p, CellFreeExpressionScreenProvider)
    assert set(p.compute_targets()) == set(p.wet_coords()) == set(CONSTRUCTS)
    assert set(p.truth_profiles()) == {"expression_high", "expression_flipped", "flat"}
    assert p.null_profiles() == frozenset({"flat"})
    for face, mu in p.truth_profiles().items():
        assert mu == sim_reader.TRUTH_PROFILES[face]
    # seed family: strong-design (higher/supported) + weak-design (lower/rejected).
    seeds = {(c.claim_id, c.status, c.direction) for c in p.seed_claims()}
    assert seeds == {
        ("b_strongdesign_expresses_higher", "supported", "higher"),
        ("b_weakdesign_expresses_higher", "rejected", "lower"),
    }


def test_provider_validate_yaml_and_key_invariant():
    """The real yaml declares construct choices == the provider's constructs, so
    validate_yaml passes; the compute_targets/wet_coords key invariant holds on the
    real provider."""
    cfg = _load_cfg()
    p = CellFreeExpressionScreenProvider()
    p.validate_yaml(cfg)  # no raise
    assert set(p.compute_targets()) == set(p.wet_coords())
    # the yaml's per-variable descriptors are transcribed verbatim from the leaf table.
    var = cfg.design_space.var("construct")
    assert var.descriptors == {
        lvl: {"coord": float(cmap["coord"])} for lvl, cmap in CONSTRUCT_DESCRIPTORS.items()
    }


def test_compute_targets_are_sequence_construct_not_geometry():
    """Contract v3: every compute target is a sequence_construct (payload carries the
    SEQUENCE, never a fabricated zmatrix), its required capability is what
    SequenceProxyAdapter consumes, and it is NOT a molecular_geometry target."""
    p = CellFreeExpressionScreenProvider()
    adapter = SequenceProxyAdapter()
    for cid, ct in p.compute_targets().items():
        assert ct.input_kind == INPUT_KIND_SEQUENCE_CONSTRUCT
        assert ct.input_kind != INPUT_KIND_MOLECULAR_GEOMETRY
        assert "sequence" in ct.payload and "zmatrix" not in ct.payload
        assert set(ct.payload) <= {"sequence", "promoter", "rbs", "cds"}
        assert ct.adapter_capability == INPUT_KIND_SEQUENCE_CONSTRUCT
        assert ct.payload_schema_version == "sequence_construct/1"
        # the dry adapter's declared capability accepts this target.
        assert adapter_accepts_capability(adapter, ct.adapter_capability)
        # the payload is the construct's real sequence (public design knowledge).
        assert ct.payload["sequence"] == CONSTRUCTS[cid]["sequence"]


def test_validate_yaml_rejects_construct_choice_mismatch():
    """A yaml whose `construct` choices declare a construct the provider cannot realise
    is rejected loudly (never silently accepted)."""
    fake_cfg = SimpleNamespace(
        design_space=SimpleNamespace(
            variables=[SimpleNamespace(name="construct", choices=["j23100", "nonesuch"])]
        )
    )
    with pytest.raises(DomainProviderError, match="choices must equal"):
        CellFreeExpressionScreenProvider().validate_yaml(fake_cfg)


# ============================================================ 3. wet generalization


def test_descriptors_injection_maps_constructs_monotone():
    """The domain-neutral wet path places each construct level at the correct realised
    coordinate via the INJECTED descriptors (higher design coord -> higher realised,
    monotone) -- the same generalization the chemistry domains use, no biology in it."""
    levels = construct_names()
    exp = ExperimentObject(
        exp_id="m24_wet",
        round_id=0,
        domain="cell_free_expression_screen",
        objective=Objective(name="expression_fluorescence", metric="expression_fluorescence"),
        design_space=DesignSpace(
            name="cell_free_expression_screen",
            variables=[VariableDef(name="construct", kind="categorical", choices=levels)],
        ),
        active_vars=["construct"],
        candidates=[
            Candidate(cand_id=f"cand_{lv}", params={"construct": lv}) for lv in levels
        ],
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="test"),
    )
    otp = compile_wet(exp, descriptors=CONSTRUCT_DESCRIPTORS, screen_param="construct")
    realised_by_cand = {w.cand_id: w.target_polarity for w in otp.wells}
    for lv in levels:
        expected = _realised(CONSTRUCT_DESCRIPTORS[lv]["coord"])
        assert realised_by_cand[f"cand_{lv}"] == pytest.approx(expected)
        assert target_coord(lv, CONSTRUCT_DESCRIPTORS) == pytest.approx(expected)
    coords = [CONSTRUCT_DESCRIPTORS[lv]["coord"] for lv in levels]
    realised = [realised_by_cand[f"cand_{lv}"] for lv in levels]
    assert np.corrcoef(coords, realised)[0, 1] == pytest.approx(1.0)


# ============================================================ 4. dry proxy monotone


def test_dry_expression_proxy_strong_beats_weak_and_monotone():
    """The dry leg is an honest-biased proxy: the strong-design construct's expression_proxy
    is far above the weak one, the gradient is strictly monotone in the design coordinate,
    and the strong/weak magnitudes reproduce the committed ~0.68 vs ~0.20 span."""
    proxies_by_coord = [expression_proxy_for(c) for c in _BY_COORD]  # weak -> strong
    strong = expression_proxy_for(_BY_COORD[-1])  # highest coord
    weak = expression_proxy_for(_BY_COORD[0])     # lowest coord
    assert strong > weak
    assert strong == pytest.approx(0.68, abs=0.03)   # committed strong-design magnitude
    assert weak == pytest.approx(0.20, abs=0.03)     # weak-design magnitude
    # strictly monotone rising with design coordinate (no interior inversion).
    assert all(b > a for a, b in zip(proxies_by_coord, proxies_by_coord[1:]))
    # dry proxy correlates with the public design coordinate -> Dry->Wet signal is real.
    assert np.corrcoef(_COORDS_ASC, proxies_by_coord)[0, 1] > 0.9
    # the adapter reproduces the same proxy deterministically for the strong construct.
    feats = SequenceProxyAdapter().compute(construct_params(_BY_COORD[-1]))
    assert feats.expression_proxy == pytest.approx(strong)


# ============================================================ 5. biology-blindness


def _bio_obs(oid: str, group: str, value: float, secondary: dict[str, float]):
    """A TRUSTED wet ObservationObject in arm ``group`` carrying arbitrary secondary
    channels (the biological content the compiler must ignore)."""
    return ObservationObject(
        obs_id=oid,
        exp_id="bio",
        round_id=0,
        cand_id=group,
        result=MeasuredResult(
            metric="expression_fluorescence", value=value, secondary=secondary
        ),
        layout_meta=LayoutMeta(well_id=oid, row=0, col=0),
        instrument_meta=InstrumentMeta(capture_index=int(oid[-2:])),
        trust=TrustLevel.TRUSTED,
    )


_BIO_HEAD = ClaimHead(
    claim_id="b_strongdesign_expresses_higher",
    statement="strong-design constructs express higher than weak-design",
    favorable_direction="higher",
    focal_group=("strong",),
    reference_group=("weak",),
)


def test_evidence_compiler_reads_only_value_and_trust_bio_blind():
    """The evidence compiler is structurally BIOLOGY-BLIND: two rounds identical in
    obs_id/value/trust but differing ONLY in biological secondary channels
    (gc/cai/rbs_strength/expression names) produce a BIT-IDENTICAL ClaimDelta -- proof it
    consumes only value + TRUSTED obs (the red line: normalization/biology never enters
    the compiler; it reads result.value on TRUSTED arms and public arm keys alone)."""
    cfg = AggregationConfig(seed=5)
    fvals = [0.90, 0.88, 0.91, 0.89, 0.90, 0.92, 0.88, 0.90]
    rvals = [0.40, 0.42, 0.39, 0.41, 0.40, 0.38, 0.42, 0.40]

    def _round(bio: bool):
        obs = []
        for i, v in enumerate(fvals):
            sec = {"gc": 0.5, "cai": 0.9, "rbs_strength": 1.0, "expression_proxy": 0.68} if bio else {}
            obs.append(_bio_obs(f"F{i:02d}", "strong", v, sec))
        for i, v in enumerate(rvals):
            sec = {"gc": 0.3, "cai": 0.1, "rbs_strength": 0.2, "expression_proxy": 0.20} if bio else {}
            obs.append(_bio_obs(f"R{i:02d}", "weak", v, sec))
        return obs

    delta_bio, _ = aggregate_round(_round(bio=True), _BIO_HEAD, cfg)
    delta_plain, _ = aggregate_round(_round(bio=False), _BIO_HEAD, cfg)
    # biological metadata on the observations does not move the verdict by a single byte.
    assert delta_bio.model_dump_json() == delta_plain.model_dump_json()

    # structural red line: the evidence-compiler module carries NO biological literal.
    src = inspect.getsource(certification_stats).lower()
    bio_tokens = ["construct", "promoter", "ribosome", "shine", "fluoresc", "codon", "sfgfp"]
    leaked = [t for t in bio_tokens if t in src]
    assert leaked == [], f"biological literal(s) leaked into the evidence compiler: {leaked}"


# ============================================================ 6. readout normalization


def test_percent_of_control_uses_control_baselines_before_cert():
    """Percent-of-control normalization uses the negative (background) + positive
    (strong reference) control baselines: positive -> ~100, negative -> 0, a midpoint ->
    ~50; a degenerate calibration (positive == negative) is refused loudly."""
    baselines = bio_readout.baselines_from_controls(
        negative_values=[0.05, 0.05, 0.05],   # no-template background wells
        positive_values=[1.00, 0.98, 1.02],   # strong-reference wells
    )
    assert baselines.negative == pytest.approx(0.05)
    assert baselines.positive == pytest.approx(1.00)
    assert bio_readout.percent_of_control(1.00, baselines) == pytest.approx(100.0)
    assert bio_readout.percent_of_control(0.05, baselines) == pytest.approx(0.0)
    mid = 0.05 + 0.5 * baselines.dynamic_range()
    assert bio_readout.percent_of_control(mid, baselines) == pytest.approx(50.0)
    # below-background reading floors at 0 only when explicitly clipped.
    assert bio_readout.percent_of_control(0.00, baselines) < 0.0
    assert bio_readout.percent_of_control(0.00, baselines, clip_negative=True) == 0.0

    # missing charter-required controls / degenerate calibration are LOUD.
    with pytest.raises(bio_readout.ReadoutError):
        bio_readout.baselines_from_controls([], [1.0])
    with pytest.raises(bio_readout.ReadoutError):
        bio_readout.percent_of_control(0.5, bio_readout.ControlBaselines(0.5, 0.5))


def test_readout_layer_imports_no_evidence_compiler_symbol():
    """RED-LINE PLACEMENT: percent-of-control normalization lives in the READOUT layer and
    runs BEFORE certification. Its module must IMPORT NO kernel evidence/certification/
    claim symbol, so normalization can never be smuggled into the evidence compiler. The
    check scans the module's real import statements (via AST), not its prose docstring
    (which legitimately names the compiler to explain the boundary)."""
    import ast

    tree = ast.parse(inspect.getsource(bio_readout))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [f"{node.module}.{a.name}" for a in node.names]
    joined = " ".join(imported).lower()
    for forbidden in ("certification", "aggregate", "claim", "evidence",
                      "kernel", "qc", "compiler"):
        assert forbidden not in joined, (
            f"readout layer must not import {forbidden!r}; imports were {imported}"
        )
    # in fact it imports nothing but stdlib (math + dataclasses + __future__) -- pure.
    allowed = ("math", "dataclasses", "__future__")
    assert all(m == "" or m.startswith(allowed) for m in imported), imported
