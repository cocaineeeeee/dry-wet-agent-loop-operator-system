"""M17 K-C — Certification-Policy loop wiring: discriminative acceptance.

K-C injects the SEVENTH policy element (``planner.certification``) and hooks it at
the mcl round end so wet-evidence ClaimDeltas update the in-run claim ledger and
re-steer the agent next round (control loop -> scientific knowledge loop). These
bodies mirror the W9 style (a real two-round PySCF+reader run) but each proves a
distinct wiring property; each is a real MCL run (~5s/round), kept to the minimum
number of runs.

  * test_null_certification_is_m16_twin — the default policy emits zero
    claim_decision events and freezes the knowledge fingerprints at the exact M16
    value (the M16 regression twin).
  * test_honest_null_fn_is_insufficient_and_frozen — the reference honest-null fn
    lands insufficient claim_decisions that mutate nothing; round-2 fingerprint
    EQUALS round-1 (KILL: land insufficient as a mutation -> fingerprint drifts).
  * test_registered_supported_fn_resteers_next_round — a fake supported fn lands a
    new claim version for a seed claim; round-2 fingerprint DIFFERS from round-1
    and round-2's proposal is computed from the updated view (the K2 substrate).
  * test_resume_rebuilds_ledger_without_re_emitting — interrupt-then-resume emits
    no duplicate claim_decision events; ledger + decision surface replay (I4/K5).
  * test_truth_profile_threads_to_reader / _default_is_byte_identical — the
    evaluation-harness kwarg reaches serve(); None reproduces the M16 face.
  * test_unregistered_decision_fn_fails_at_construction — loud fail at wiring time.
"""

from __future__ import annotations

from pathlib import Path

from expos.kernel.claims import (
    REFERENCE_CERTIFICATION_FN_ID,
    REFERENCE_CERTIFICATION_FN_VERSION,
    ClaimDecisionStatus,
    EvidenceStrength,
    Ledger,
    ClaimRecord,
    register_decision_fn,
)
from expos.kernel.knowledge import compile_knowledge
from expos.kernel.store import RunStore
from expos.mcl import (
    _default_claims,
    _default_hypotheses,
    run_mcl_loop,
)
from expos.planner.certification import (
    CertificationError,
    NullCertification,
    RegisteredFnCertification,
)

import pytest

_DOMAIN = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"

_POLAR = "c_polar_responds_higher"
_NONPOLAR = "c_nonpolar_responds_higher"


# ------------------------------------------------------------------ fake decision fn

#: A fake registered decision fn with a distinct id (K-C E3 substrate): honest
#: about its shape — same criterion signature as reference_round_certification —
#: but returns SUPPORTED so the policy lands a mutating ClaimDelta. Registered in
#: the SHARED registry (additive, id-unique) so RegisteredFnCertification resolves
#: it exactly like the real K-B fn will.
_FAKE_SUPPORTED_ID = "k_c_test_always_supported"


@register_decision_fn(_FAKE_SUPPORTED_ID, "1")
def _always_supported(*, statistic, power, criterion_version) -> ClaimDecisionStatus:
    return ClaimDecisionStatus.SUPPORTED


# ------------------------------------------------------------------ helpers


def _events(run_dir: Path, kind: str) -> list[dict]:
    return RunStore(run_dir, create=False).read_events(kind)


def _knowledge_fps(run_dir: Path) -> list[str]:
    return [e["payload"]["fingerprint"] for e in _events(run_dir, "knowledge_updated")]


def _claim_decisions(run_dir: Path) -> list[dict]:
    return [e["payload"] for e in _events(run_dir, "claim_decision")]


def _ledger_effective(run_dir: Path) -> dict[str, str]:
    """The effective statuses of the persisted ledger snapshot (the decision-face
    projection compile_knowledge consumes; annotation provenance is execution-face
    and excluded)."""
    ckpt = RunStore(run_dir, create=False).read_checkpoint() or {}
    ledger = Ledger(
        claims=tuple(
            ClaimRecord.model_validate(r) for r in ckpt.get("claim_ledger", [])
        )
    )
    return {k: v.value for k, v in ledger.effective_statuses().items()}


def _wet_values(run_dir: Path) -> list[float]:
    store = RunStore(run_dir, create=False)
    return sorted(
        o.result.value
        for o in store.list_observations()
        if o.raw_ref.kind == "wet" and o.result.value is not None
    )


def _honest_null() -> RegisteredFnCertification:
    return RegisteredFnCertification(
        REFERENCE_CERTIFICATION_FN_ID, REFERENCE_CERTIFICATION_FN_VERSION
    )


# ================================================================ E1: M16 twin


def test_null_certification_is_m16_twin(tmp_path):
    """NullCertification: zero claim_decision events and the knowledge fingerprints
    frozen at the exact M16 value (identical pre-K-C behaviour)."""
    run_mcl_loop(
        _DOMAIN,
        rounds=2,
        seed=7,
        out_dir=tmp_path / "run",
        certification=NullCertification(),
    )

    assert _claim_decisions(tmp_path / "run") == []  # seventh element inert

    fps = _knowledge_fps(tmp_path / "run")
    m16_fp = compile_knowledge(
        _default_claims(), _default_hypotheses()
    ).knowledge_fingerprint
    assert len(fps) == 2
    assert fps[0] == fps[1] == m16_fp  # frozen AND pinned to the M16 substrate

    # payload-validation gate clean across the whole stream (no new violations).
    store = RunStore(tmp_path / "run", create=False)
    assert store.validate_event_payloads(store.read_events()) == []


# ================================================================ E2: honest-null


def test_honest_null_fn_is_insufficient_and_frozen(tmp_path):
    """The reference honest-null fn lands insufficient claim_decisions that DO NOT
    mutate the effective status; round-2 knowledge fingerprint EQUALS round-1.

    KILL: if the hook wrongly routed insufficient into a mutation, the effective
    status would move and round-2's fingerprint would drift from round-1 -> the
    frozen-fingerprint assert turns red."""
    run_mcl_loop(
        _DOMAIN,
        rounds=2,
        seed=7,
        out_dir=tmp_path / "run",
        certification=_honest_null(),
    )

    cds = _claim_decisions(tmp_path / "run")
    assert cds, "certification hook must emit claim_decision events"
    assert all(c["decision_status"] == "insufficient" for c in cds)
    # insufficient carries the honest-null decision_fn id + the K4 provenance chain.
    assert all(c["decision_fn_id"] == REFERENCE_CERTIFICATION_FN_ID for c in cds)

    # effective statuses unchanged from the seed (K3: absence of evidence != support).
    assert _ledger_effective(tmp_path / "run") == {
        _POLAR: "supported",
        _NONPOLAR: "rejected",
    }

    fps = _knowledge_fps(tmp_path / "run")
    assert fps[0] == fps[1]  # insufficient mutated nothing -> frozen fingerprint


# ================================================================ E3: K2 substrate


def test_registered_supported_fn_resteers_next_round(tmp_path):
    """K2 substrate: a fake supported fn lands a NEW claim version for a seed claim,
    so round-2's knowledge fingerprint DIFFERS from round-1 and round-2's proposal
    is computed from the UPDATED view (proving the agent is re-steered by the
    round-1 wet-evidence adjudication, not merely by external injection)."""
    cert = RegisteredFnCertification(
        _FAKE_SUPPORTED_ID,
        "1",
        target_claim_ids=(_NONPOLAR,),
        evidence_strength=EvidenceStrength.MODERATE,
    )
    run_mcl_loop(
        _DOMAIN, rounds=2, seed=7, out_dir=tmp_path / "run", certification=cert
    )

    # round-1 landed one supported delta for the seed claim, as a NEW version.
    cds = _claim_decisions(tmp_path / "run")
    r0 = [c for c in cds if c["round_id"] == 0]
    assert len(r0) == 1
    assert r0[0]["claim_id"] == _NONPOLAR
    assert r0[0]["decision_status"] == "supported"
    assert (
        r0[0]["claim_version"] == 2
    )  # superseded seed version 1 -> new head version 2
    # K4 required keys present on the payload (self-sufficient adjudication record).
    for key in (
        "round_id",
        "claim_id",
        "claim_version",
        "decision_status",
        "decision_fn_id",
        "input_observation_ids",
        "statistic",
        "power",
        "consumed_knowledge_fingerprint",
    ):
        assert key in r0[0]
    # the adjudication consumed round-1's knowledge fingerprint (K4 chain closure).
    fps = _knowledge_fps(tmp_path / "run")
    assert r0[0]["consumed_knowledge_fingerprint"] == fps[0]

    # K2: the ledger update moved the compiled knowledge -> round-2 fingerprint differs.
    assert fps[0] != fps[1]
    # and the effective status of the adjudicated claim actually flipped.
    assert _ledger_effective(tmp_path / "run")[_NONPOLAR] == "supported"

    # round-2's proposal was computed from the UPDATED view: the proposal decision
    # records the round-2 (post-update) knowledge fingerprint, not round-1's.
    props = RunStore(tmp_path / "run", create=False).list_decisions()
    fp_by_round = {d.round_id: d.content["knowledge_fingerprint"] for d in props}
    assert fp_by_round[1] == fps[1] and fp_by_round[1] != fps[0]


# ================================================================ E4: resume (I4/K5)


def test_resume_rebuilds_ledger_without_re_emitting(tmp_path):
    """Interrupt after round 1, resume -> no duplicate claim_decision events, and
    the ledger + decision surface replay equal to the uninterrupted run (I4/K5)."""
    # uninterrupted two-round baseline.
    run_mcl_loop(
        _DOMAIN,
        rounds=2,
        seed=7,
        out_dir=tmp_path / "whole",
        certification=_honest_null(),
    )

    # interrupted: complete round 0, then RESUME for round 1 (rebuild ledger from
    # the checkpoint snapshot, re-emit nothing).
    run_mcl_loop(
        _DOMAIN,
        rounds=1,
        seed=7,
        out_dir=tmp_path / "part",
        certification=_honest_null(),
    )
    run_mcl_loop(
        _DOMAIN,
        rounds=2,
        seed=7,
        out_dir=tmp_path / "part",
        certification=_honest_null(),
        resume=True,
    )

    whole = _claim_decisions(tmp_path / "whole")
    part = _claim_decisions(tmp_path / "part")

    # no duplicate emission: the resume did not re-emit round-0's claim_decisions.
    assert len(part) == len(whole)
    assert sum(1 for c in part if c["round_id"] == 0) == sum(
        1 for c in whole if c["round_id"] == 0
    )

    # decision surface replays: knowledge fingerprint chain, ClaimDecision status
    # sequence and the reconstructed ledger effective statuses are all equal.
    assert _knowledge_fps(tmp_path / "part") == _knowledge_fps(tmp_path / "whole")

    def _seq(cds):
        return sorted((c["round_id"], c["claim_id"], c["decision_status"]) for c in cds)

    assert _seq(part) == _seq(whole)
    assert _ledger_effective(tmp_path / "part") == _ledger_effective(tmp_path / "whole")


# ================================================================ E5: truth_profile


def test_truth_profile_threads_to_reader(tmp_path):
    """The evaluation-harness kwarg reaches serve(): a flipped truth face changes
    the wet observation values (proving it selected a different hidden surface)."""
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "default")
    run_mcl_loop(
        _DOMAIN,
        rounds=1,
        seed=7,
        out_dir=tmp_path / "flip",
        truth_profile="nonpolar_high",
    )
    assert _wet_values(tmp_path / "default") != _wet_values(tmp_path / "flip")


def test_truth_profile_default_is_byte_identical(tmp_path):
    """None reproduces the M16 face byte-for-byte: default == explicit polar_high."""
    run_mcl_loop(_DOMAIN, rounds=1, seed=7, out_dir=tmp_path / "none")
    run_mcl_loop(
        _DOMAIN,
        rounds=1,
        seed=7,
        out_dir=tmp_path / "polar",
        truth_profile="polar_high",
    )
    assert _wet_values(tmp_path / "none") == _wet_values(tmp_path / "polar")


# ================================================================ E6: loud construction


def test_unregistered_decision_fn_fails_at_construction():
    """An unregistered decision_fn id fails loudly at CONSTRUCTION (wiring time),
    not silently at round end (letter 072 governance red line 1)."""
    with pytest.raises(CertificationError):
        RegisteredFnCertification("ghost_decision_fn", "1")
    # a registered id with the wrong version is equally loud at construction.
    with pytest.raises(CertificationError):
        RegisteredFnCertification(REFERENCE_CERTIFICATION_FN_ID, "999")
