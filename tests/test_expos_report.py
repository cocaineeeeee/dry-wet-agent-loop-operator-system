"""Acceptance tests for scripts/expos_report.py — the events-pure-function closing report.

Five discriminative groups (Phase 4 item #6, deliverable B):
  1. known-truths: generate against the five real run dirs, assert the closing numbers
     appear — sourced from the generator's own extraction, never hardcoded in the test.
  2. offline guard: no http(s):// / <script src / CDN host string survives to the output.
  3. purity: generating twice yields byte-identical HTML (no wall-clock / machine paths).
  4. tamper discriminative: flip one byte of a copied run's events.jsonl -> the high-water
     sha changes AND the gate-12 status flips to broken (integrity pinning catches doctoring).
  5. literal-free template: no value-bearing content template carries a digit literal.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import expos_report as er  # noqa: E402

RUNS = ROOT / "runs"
RUN_DIRS = [
    RUNS / "corun_flat",
    RUNS / "corun_consistent_zero",
    RUNS / "corun_consistent_strong",
    RUNS / "corun_flipped",
    RUNS / "llm_smoke_stage3",
]
EXPECTED = {
    "corun_flat": "insufficient",
    "corun_consistent_zero": "insufficient",
    "corun_consistent_strong": "supported",
    "corun_flipped": "rejected",
    "llm_smoke_stage3": "supported",
}

pytestmark = pytest.mark.skipif(
    not (RUNS / "corun_flipped" / "events.jsonl").exists(),
    reason="joint-run fixtures not present on disk",
)


@pytest.fixture(scope="module")
def runs():
    return er.build_runs(RUN_DIRS, EXPECTED)


@pytest.fixture(scope="module")
def html(runs):
    return er.render_runs(runs)


# ---------------------------------------------------------------- 1. known truths

def test_all_expected_verdicts_reproduced(runs):
    """4/4 corun conditions (+stage3) reproduce their expected verdict FROM the stream."""
    corun = [r for r in runs if r.name.startswith("corun_")]
    matched = [r for r in corun if r.measured_verdict == r.expected_verdict]
    assert len(matched) == 4, [(r.name, r.measured_verdict, r.expected_verdict) for r in corun]
    # and every supplied expectation across all runs holds
    for r in runs:
        assert r.measured_verdict == r.expected_verdict, (r.name, r.measured_verdict)


def test_fingerprint_migration_pair(runs):
    """corun_flipped migrates its knowledge fingerprint 003cae6f... -> 809ca7a1...."""
    flipped = next(r for r in runs if r.name == "corun_flipped")
    assert len(flipped.fp_migrations) == 1
    a, b = flipped.fp_migrations[0]
    assert a.startswith("003cae6f")
    assert b.startswith("809ca7a1")
    # the stable conditions do not migrate
    for name in ("corun_flat", "corun_consistent_zero", "corun_consistent_strong"):
        r = next(x for x in runs if x.name == name)
        assert r.fp_migrations == []


def test_e_values_and_effects(runs):
    """Terminal e-values / effects match the closing numbers (extraction, not hardcode)."""
    strong = next(r for r in runs if r.name == "corun_consistent_strong")
    flipped = next(r for r in runs if r.name == "corun_flipped")
    stage3 = next(r for r in runs if r.name == "llm_smoke_stage3")

    # strong: terminal effect +0.3193285, terminal e-value 10451.9 (6 sig figs)
    assert abs(strong.rounds[-1].effect - 0.3193285) < 1e-9
    assert abs(strong.rounds[-1].evidence_factor - 10451.906944) < 1e-3
    # flipped: terminal effect -0.085924625 (letter -0.086), same e-value magnitude
    assert abs(flipped.rounds[-1].effect - (-0.085924625)) < 1e-9
    assert abs(flipped.rounds[-1].evidence_factor - 10451.906944) < 1e-3
    # stage3: terminal effect +0.3193285 (LLM backend converges to same verdict)
    assert abs(stage3.rounds[-1].effect - 0.3193285) < 1e-9
    # e-product identical for strong & flipped (magnitude), sign is the discriminator
    assert abs(strong.e_product - flipped.e_product) < 1.0


def test_known_truths_rendered(runs, html):
    """The extracted closing numbers actually reach the HTML (via the generator formatter)."""
    strong = next(r for r in runs if r.name == "corun_consistent_strong")
    flipped = next(r for r in runs if r.name == "corun_flipped")
    # fingerprint migration pair
    assert "003cae6f" in html
    assert "809ca7a1" in html
    # e-value and effect, formatted exactly as the generator formats them
    assert er._num(strong.rounds[-1].evidence_factor) in html  # 10451.9
    assert er._signed(strong.rounds[-1].effect) in html         # +0.3193285
    assert er._signed(flipped.rounds[-1].effect) in html        # -0.08592463
    # adjudication summary present
    assert "reproduce their expected" in html


def test_gate12_all_complete(runs, html):
    for r in runs:
        assert r.verify_ok, (r.name, r.verify_code, r.verify_message)
    assert "CHAIN COMPLETE" in html


# ---------------------------------------------------------------- 2. offline guard

def test_offline_no_external_references(html):
    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html
    assert "<script src" not in html
    for host in ("cdn", "cloudflare", "googleapis", "jsdelivr", "unpkg", "mathjax"):
        assert host not in html.lower(), host
    # inline SVG must NOT carry an xmlns (which would reintroduce an http URI)
    assert "xmlns" not in html


# ---------------------------------------------------------------- 3. purity

def test_generation_is_byte_identical(tmp_path):
    a = er.build_report(RUN_DIRS, EXPECTED)
    b = er.build_report(RUN_DIRS, EXPECTED)
    assert a == b
    # and no absolute machine path leaks into the content
    assert str(ROOT) not in a


def test_report_digest_stable(runs):
    d1 = er.compute_report_digest(runs)
    d2 = er.compute_report_digest(er.build_runs(RUN_DIRS, EXPECTED))
    assert d1 == d2
    assert re.fullmatch(r"[0-9a-f]{64}", d1)


# ---------------------------------------------------------------- 4. tamper discriminative

def test_tampered_byte_flips_sha_and_gate12(tmp_path):
    """Copy a run, flip one byte in events.jsonl: the high-water sha must change AND the
    gate-12 status must flip to broken (integrity pinning catches doctored inputs)."""
    src = RUNS / "corun_consistent_strong"
    dst = tmp_path / "tampered_run"
    shutil.copytree(src, dst)

    clean = er.extract_run(dst, "tampered_run", "supported")
    assert clean.verify_ok
    clean_sha = clean.high_water_sha

    # flip one byte inside a complete event line (mutate a fingerprint hex char).
    ev_path = dst / "events.jsonl"
    raw = ev_path.read_bytes()
    idx = raw.find(b"003cae6f")
    assert idx != -1, "expected a known fingerprint to mutate"
    flip = raw[idx]
    new_byte = ord("0") if flip != ord("0") else ord("1")
    raw = raw[:idx] + bytes([new_byte]) + raw[idx + 1:]
    ev_path.write_bytes(raw)

    tampered = er.extract_run(dst, "tampered_run", "supported")
    assert tampered.high_water_sha != clean_sha, "high-water sha must change on tamper"
    assert not tampered.verify_ok, "gate-12 must flip to broken on tamper"

    # and the report renders the tampered run as broken, not dropped
    html = er.build_report([dst], {"tampered_run": "supported"})
    assert "CHAIN BROKEN" in html
    assert tampered.high_water_sha in html


# ---------------------------------------------------------------- 5. literal-free template

def test_content_templates_carry_no_digit_literals():
    """No value-bearing template bakes a scientific number as a literal: after removing
    the ``{}`` value slots, any remaining digit must belong to an identifier / attribute
    name (e.g. the SVG ``x1``/``y1`` coordinate attributes) — never a standalone number in
    a value position. A hand-typed effect/e-value/count would sit after ``>`` / ``=`` /
    quote and fail here (the SW2 fail-closed invariant)."""
    for tpl in er._CONTENT_TEMPLATES:
        stripped = re.sub(r"\{[^}]*\}", "", tpl)  # drop the value slots
        for m in re.finditer(r"\d+", stripped):
            prev = stripped[m.start() - 1] if m.start() > 0 else ""
            assert prev.isalpha() or prev == "_", (
                f"standalone digit literal {m.group()!r} in value position of: {tpl!r}")


def test_content_templates_collection_is_nonempty():
    assert len(er._CONTENT_TEMPLATES) > 0
    # sanity: the core value-bearing templates (row / cell) are registered
    assert er._TR in er._CONTENT_TEMPLATES
    assert er._TD in er._CONTENT_TEMPLATES
    # the document skeleton is scaffolding (mandated utf-8 boilerplate), not value-bearing
    assert er._DOC in er._SCAFFOLD_TEMPLATES
    assert er._DOC not in er._CONTENT_TEMPLATES


def test_scaffold_templates_have_no_scientific_value_slots():
    """Scaffolding may carry boilerplate digits, but must not host a numeric data slot:
    its only {} slots are structural (title/css/body/sid/anchors/head/rows/...)."""
    allowed = {"title", "css", "body", "sid", "anchors", "head", "rows", "cls",
               "open", "close", "subtitle", "asof"}
    for tpl in er._SCAFFOLD_TEMPLATES:
        slots = set(re.findall(r"\{(\w+)\}", tpl))
        assert slots <= allowed, (tpl, slots - allowed)
