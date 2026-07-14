"""Acceptance tests for scripts/expos_readiness_report.py — the M23 Real-Wet Readiness
report generated as a pure function of a physical-evidence directory (M23 Phase 5).

The real evidence set is produced LATER by the other session via sbatch; these tests
synthesize a MINIMAL evidence set by actually driving the fake physical backend path
(reusing the Phase 3/4 primitives) plus a synthetic crash pair, a loud-failure capture,
and a differential positive/negative pair — enough to exercise all eight sections, the
minimal-safe-steps checklist, AND the missing-evidence blocks.

Discriminative groups (deliverable D):
  1. contract-shape validation: a malformed manifest is loud (validate_manifest + render).
  2. missing-evidence: a listed-but-absent scenario renders an EVIDENCE-MISSING block,
     never silently skipped; an absent evidence dir still renders limits + safe steps.
  3. independent recomputation: tamper one ledger byte -> section 5 flags it AND the
     integrity report_digest changes (the imported ActionLedger replay catches it).
  4. literal-free templates: no authored value-bearing template carries a digit literal.
  5. offline guard: no http(s):// / <script / xmlns / CDN host survives to the output.
  6. purity: two generations over the same evidence dir are byte-identical.
  7. M17 regression: the existing expos_report CLI on the five M17 runs still produces the
     pinned digest 23e0752d... (the shared-primitive reuse did not perturb it).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import expos_readiness_report as rr  # noqa: E402
import expos_report as er  # noqa: E402

from expos.adapters.wet.action_ledger import (  # noqa: E402
    ActionLedger,
    PlannedAction,
    VolumeLedger,
)
from expos.adapters.wet.differential_gate import run_differential_gate  # noqa: E402
from expos.adapters.wet.fake_physical import (  # noqa: E402
    Behaviour,
    BehaviourSpec,
    FakePhysicalBackend,
    Scenario,
)
from expos.adapters.wet.orchestration import (  # noqa: E402
    cancel_action,
    dispatch_round,
    recover_action,
    resume_round,
)
from expos.adapters.wet.recovery import WaitForRecovery  # noqa: E402


# ---------------------------------------------------------------- evidence synthesis

def _led(d: Path) -> ActionLedger:
    return ActionLedger(d, volume=VolumeLedger(capacities={"RSV": 1e9}, initial={"RSV": 1e9}))


def _manifest(base: Path, sid: str, **kw) -> Path:
    d = base / sid
    d.mkdir(parents=True, exist_ok=True)
    m = {"scenario_id": sid, "description": sid, "seed": 7, "rounds": 1,
         "domain": "solvent_screen"}
    m.update(kw)
    (d / "scenario_manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return d


def _build_evidence(base: Path) -> Path:
    """Synthesize a minimal readiness evidence set covering every section by actually
    running the fake physical backend path (no dry leg, cheap)."""
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    # 1. nominal (confirm_exact) — real fake-backend dispatch, all COMMITTED
    d = _manifest(base, "confirm_exact", mode="confirm_exact",
                  expected_outcome="success", killpoint=None)
    sc = Scenario(name="nominal",
                  actions=[{"action_id": "act-r0-e0-w0", "dest": "B2", "volume": 150.0},
                           {"action_id": "act-r0-e0-w1", "dest": "C2", "volume": 100.0}])
    dispatch_round(sc.planned(), FakePhysicalBackend(sc), _led(d / "physical"))

    # 2. synthetic crash pair — dispatch leaves PENDING (crash), resume re-senses (no re-dispatch)
    d = _manifest(base, "crash_I3", mode="crash_I3", expected_outcome="recovered",
                  killpoint="after_pending_before_confirm", resume_style="same_run")
    sc2 = Scenario(name="crash",
                   actions=[{"action_id": "act-r0-e0-w0", "dest": "B2", "volume": 150.0}])
    pa: PlannedAction = sc2.planned()[0]
    l2 = _led(d / "physical")
    l2.dispatch(pa, io_call=lambda: True)          # PENDING persisted before the "crash"
    l2b = _led(d / "physical")                       # fresh ledger over the same dir = resume
    resume_round(sc2.planned(), FakePhysicalBackend(sc2), l2b)

    # 3. human_recover — mismatch parks AWAITING_RECOVERY, operator recovers -> COMMITTED
    d = _manifest(base, "human_recover", mode="human_recover",
                  expected_outcome="recovered", killpoint=None)
    scr = Scenario(name="rec",
                   actions=[{"action_id": "act-r0-e0-w0", "dest": "B2", "volume": 150.0}],
                   behaviours={"B2": [BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE"),
                                      BehaviourSpec(2, Behaviour.CONFIRM_EXACT)]})
    lr = ActionLedger(d / "physical", volume=VolumeLedger(capacities={"RSV": 1e9},
                                                          initial={"RSV": 1e9}),
                      policy=WaitForRecovery())
    be = FakePhysicalBackend(scr)
    dispatch_round(scr.planned(), be, lr)
    recover_action(scr.planned()[0], be, lr)

    # 4. human_cancel — mismatch parks, operator cancels -> ABORTED
    d = _manifest(base, "human_cancel", mode="human_cancel",
                  expected_outcome="aborted", killpoint=None)
    scc = Scenario(name="can",
                   actions=[{"action_id": "act-r0-e0-w0", "dest": "B2", "volume": 150.0}],
                   behaviours={"B2": [BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    lc = ActionLedger(d / "physical", volume=VolumeLedger(capacities={"RSV": 1e9},
                                                         initial={"RSV": 1e9}),
                      policy=WaitForRecovery())
    dispatch_round(scc.planned(), FakePhysicalBackend(scc), lc)
    cancel_action("act-r0-e0-w0", lc)

    # 5. loud failure — unit_mismatch stderr + non-zero exit
    d = _manifest(base, "unit_mismatch", mode="unit_mismatch",
                  expected_outcome="loud_failure", killpoint=None)
    (d / "stderr.txt").write_text(
        "Traceback (most recent call last):\n"
        '  File "expos/mcl.py", line 1130, in run_wet_leg\n'
        "    _ingest_units(cfg, wet_obs, cfg.objective.metric)\n"
        "expos.domain.DomainError: metric 'solvent_response' declared unit 'arbitrary_unit' "
        "but observation carried 'debye' -- refusing to coerce (T2 loud)\n",
        encoding="utf-8")
    (d / "exit_status.txt").write_text("1\n", encoding="utf-8")

    # 6. differential positive + negative
    def _diff(sid: str, drift_pct: float, outcome: str) -> None:
        d = _manifest(base, sid, mode=sid, expected_outcome=outcome, killpoint=None)
        sim_d, real_d = d / "sim", d / "real"
        ssc = Scenario(name="sim",
                       actions=[{"action_id": "act-r0-e0-w0", "dest": "B2", "volume": 150.0}])
        dispatch_round(ssc.planned(), FakePhysicalBackend(ssc),
                       ActionLedger(sim_d, volume=VolumeLedger(capacities={"RSV": 1e9},
                                                              initial={"RSV": 1e9})))
        rsc = Scenario(name="real",
                       actions=[{"action_id": "act-r0-e0-w0", "dest": "B2", "volume": 150.0}],
                       behaviours={"B2": [BehaviourSpec(1, Behaviour.CONFIRM_DRIFT,
                                                        drift_pct=drift_pct)]})
        dispatch_round(rsc.planned(), FakePhysicalBackend(rsc),
                       ActionLedger(real_d, volume=VolumeLedger(capacities={"RSV": 1e9},
                                                               initial={"RSV": 1e9})))
        rep = run_differential_gate(sim_d / "action_ledger.jsonl",
                                    real_d / "action_ledger.jsonl")
        (d / "diff_report.json").write_text(json.dumps(rep.as_dict(), indent=2), encoding="utf-8")
        (d / "physical").mkdir(exist_ok=True)
        shutil.copy(sim_d / "action_ledger.jsonl", d / "physical" / "action_ledger.jsonl")

    _diff("diff_positive", 0.5, "success")
    _diff("diff_negative", 20.0, "gate_reject")

    # index — lists the seven provided scenarios PLUS one listed-but-absent (crash_I1) so the
    # missing-evidence rendering is exercised alongside present evidence.
    idx = {
        "scenarios": ["confirm_exact", "crash_I3", "crash_I1", "human_recover",
                      "human_cancel", "unit_mismatch", "diff_positive", "diff_negative"],
        "envelope_config": "expos/adapters/wet/tolerances_vendor_placeholder.json",
        "generated_by": "test_readiness_report", "sbatch_job_ids": [],
    }
    (base / "evidence_index.json").write_text(json.dumps(idx, indent=2), encoding="utf-8")
    return base


@pytest.fixture(scope="module")
def evidence(tmp_path_factory) -> Path:
    return _build_evidence(tmp_path_factory.mktemp("readiness_evidence") / "readiness_evidence")


@pytest.fixture(scope="module")
def html(evidence) -> str:
    return rr.build_report(evidence)


# ---------------------------------------------------------------- eight sections render

def test_all_eight_sections_and_checklist_render(html):
    for sid in ("coverage", "crash", "redispatch", "mismatch", "volume",
                "differential", "human", "limits", "safesteps", "integrity"):
        assert f'id="{sid}"' in html, sid
    # section-specific machine outputs
    assert ">covered<" in html and "Uncovered legal transitions" in html   # 1
    assert "single-dispatch" in html                                       # 2
    assert "no re-dispatch" in html                                        # 3
    assert "stderr-quote" in html and "DomainError" in html                # 4
    assert ">conserved<" in html                                           # 5
    assert "PASS (within envelope)" in html and "REJECT (drift)" in html   # 6
    assert 'class="arc-node"' in html and ">recovered<" in html and ">canceled<" in html  # 7
    assert "EXP013" in html and "TODO" in html                             # 8
    assert 'class="safesteps"' in html and ">ready<" in html               # checklist


def test_section8_and_checklist_are_machine_derived(evidence):
    """Section 8 items each name their machine source; every checklist status is derived,
    none hand-set. Assert the derivations agree with the underlying tools directly."""
    import expos_lint
    findings = expos_lint.run_lint(ROOT, preview=True, select=["EXP013"])
    html = rr.build_report(evidence)
    # every EXP013 finding path reaches the report (machine-derived, not hand-listed)
    for f in findings:
        assert f.path in html, f.path
    # the safe-steps statuses are computed, not literals: re-run the check functions
    rows = rr._step_checks()
    assert rows, "checklist must have rows"
    # the append-only-ledger step is ready; the placeholder-replacement step is OPEN
    joined = {desc: (cls, text) for desc, cls, text, _ in rows}
    ledger_step = next(k for k in joined if "append-only" in k)
    assert joined[ledger_step][0] == "ok"
    placeholder_step = next(k for k in joined if "gravimetric" in k)
    assert joined[placeholder_step][0] == "warn"       # placeholder present -> OPEN, machine-derived


# ---------------------------------------------------------------- contract-shape validation

def test_bad_manifest_is_loud_and_rendered(evidence, tmp_path):
    # unit-level: validate_manifest flags each missing required key
    validate_errs = rr.validate_manifest({"scenario_id": "x"})
    assert validate_errs
    assert any("mode" in e for e in validate_errs)
    assert any("expected_outcome" in e for e in validate_errs)
    assert rr.validate_manifest("not a dict")
    assert rr.validate_manifest({"scenario_id": "x", "mode": "m",
                                 "expected_outcome": "success"}) == []

    # render-level: a scenario whose manifest is malformed shows a MANIFEST INVALID block
    dst = tmp_path / "ev"
    shutil.copytree(evidence, dst)
    (dst / "confirm_exact" / "scenario_manifest.json").write_text(
        json.dumps({"scenario_id": "confirm_exact"}), encoding="utf-8")  # missing keys
    html = rr.build_report(dst)
    assert "manifest invalid" in html.lower()


# ---------------------------------------------------------------- missing evidence

def test_listed_but_absent_scenario_renders_missing_block(html):
    """crash_I1 is listed in the index but has no directory -> a loud EVIDENCE-MISSING
    block, never silently skipped."""
    assert "crash_I1" in html
    assert "evidence missing" in html.lower()


def test_absent_evidence_dir_still_renders_limits_and_steps(tmp_path):
    """With NO evidence directory at all, the report still renders section 8 + the
    safe-steps checklist (repo-derived) and every scenario as EVIDENCE MISSING."""
    html = rr.build_report(tmp_path / "does_not_exist")
    assert 'id="limits"' in html and 'id="safesteps"' in html
    assert "evidence" in html.lower() and "missing" in html.lower()
    assert "EXP013" in html                              # section 8 still populated
    assert ">ready<" in html                             # checklist still computed
    # every expected scenario is enumerated as not-yet-provided
    for sid in ("crash_I3", "human_recover", "diff_positive"):
        assert sid in html


# ---------------------------------------------------------------- independent recomputation

def test_tampered_ledger_byte_flags_section5_and_changes_digest(evidence, tmp_path):
    """Flip one byte inside a scenario's action_ledger.jsonl: the imported ActionLedger
    replay must flag it (section 5 renders BROKEN/UNVERIFIABLE) AND the integrity
    report_digest must change (the ledger high-water sha is pinned into it)."""
    dst = tmp_path / "ev"
    shutil.copytree(evidence, dst)

    clean = rr.load_evidence_set(dst)
    clean_digest = rr.compute_report_digest(clean)
    # the clean confirm_exact ledger verifies
    clean_conf = next(s for s in clean.scenarios if s.sid == "confirm_exact")
    assert clean_conf.ledger.integrity_ok

    ledger_path = dst / "confirm_exact" / "physical" / "action_ledger.jsonl"
    raw = ledger_path.read_bytes()
    # mutate a hex digit of a line_sha / fingerprint so the hash chain no longer verifies
    idx = raw.find(b'"line_sha"')
    assert idx != -1
    hexpos = raw.find(b'"', idx + len(b'"line_sha": ') ) + 1  # first char of the sha value
    # find a hex char to flip within the sha value region
    region = raw[hexpos:hexpos + 64]
    ch = region[0:1]
    new = b"0" if ch != b"0" else b"1"
    raw = raw[:hexpos] + new + raw[hexpos + 1:]
    ledger_path.write_bytes(raw)

    tampered = rr.load_evidence_set(dst)
    tam_conf = next(s for s in tampered.scenarios if s.sid == "confirm_exact")
    assert not tam_conf.ledger.integrity_ok, "imported replay must catch the tampered chain"
    assert tam_conf.ledger.sha != clean_conf.ledger.sha, "ledger high-water sha must change"

    tam_digest = rr.compute_report_digest(tampered)
    assert tam_digest != clean_digest, "integrity report_digest must change on tamper"

    html = rr.build_report(dst)
    assert "UNVERIFIABLE" in html or "BROKEN" in html    # section 5 / footer flags it loudly


# ---------------------------------------------------------------- literal-free templates

def test_authored_templates_carry_no_digit_literals():
    """No value-bearing template authored here bakes a scientific number as a literal
    (same SW2 invariant expos_report enforces on its own collection)."""
    for tpl in rr._CONTENT_TEMPLATES:
        stripped = re.sub(r"\{[^}]*\}", "", tpl)
        for m in re.finditer(r"\d+", stripped):
            prev = stripped[m.start() - 1] if m.start() > 0 else ""
            assert prev.isalpha() or prev == "_", (
                f"standalone digit literal {m.group()!r} in value position of: {tpl!r}")


def test_scaffold_templates_have_no_scientific_value_slots():
    allowed = {"title", "css", "body", "sid", "anchors", "head", "rows", "cls",
               "subtitle", "asof", "n", "desc", "status", "basis"}
    for tpl in rr._SCAFFOLD_TEMPLATES:
        slots = set(re.findall(r"\{(\w+)\}", tpl))
        assert slots <= allowed, (tpl, slots - allowed)


# ---------------------------------------------------------------- offline guard

def test_offline_no_external_references(html):
    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html
    assert "xmlns" not in html
    for host in ("cdn", "cloudflare", "googleapis", "jsdelivr", "unpkg", "mathjax"):
        assert host not in html.lower(), host


# ---------------------------------------------------------------- purity

def test_generation_is_byte_identical(evidence):
    a = rr.build_report(evidence)
    b = rr.build_report(evidence)
    assert a == b
    assert str(ROOT) not in a          # no absolute machine path leaks into the content


def test_report_digest_stable(evidence):
    d1 = rr.compute_report_digest(rr.load_evidence_set(evidence))
    d2 = rr.compute_report_digest(rr.load_evidence_set(evidence))
    assert d1 == d2
    assert re.fullmatch(r"[0-9a-f]{64}", d1)


# ---------------------------------------------------------------- M17 regression

M17_RUNS = [
    ROOT / "runs" / "corun_flat",
    ROOT / "runs" / "corun_consistent_zero",
    ROOT / "runs" / "corun_consistent_strong",
    ROOT / "runs" / "corun_flipped",
    ROOT / "runs" / "llm_smoke_stage3",
]
M17_EXPECTED = {
    "corun_flat": "insufficient", "corun_consistent_zero": "insufficient",
    "corun_consistent_strong": "supported", "corun_flipped": "rejected",
    "llm_smoke_stage3": "supported",
}
M17_DIGEST = "23e0752d17d655590646f16618879dac3367cbebb63f1d4e2220039a974eecbc"


@pytest.mark.skipif(not (ROOT / "runs" / "corun_flipped" / "events.jsonl").exists(),
                    reason="M17 joint-run fixtures not present on disk")
def test_m17_closing_report_digest_unchanged():
    """The shared-primitive reuse (importing expos_report, never modifying it) must leave
    the M17 closing report byte-identical: its pinned report_digest is unchanged."""
    runs = er.build_runs(M17_RUNS, M17_EXPECTED)
    assert er.compute_report_digest(runs) == M17_DIGEST
    # and the full document reproduces the pinned digest inside its own integrity footer
    html = er.render_runs(runs)
    assert M17_DIGEST in html
