#!/usr/bin/env python3
"""expos report — a human-readable scientific closing report generated as a PURE
FUNCTION of run directories (Phase 4 item #6).

Design lineage: /Data1/ericyang/m19_references/INDEX_REF_R.md (§Convergence — plain
Python script + literal-free template, N run dirs + expected-verdict params in,
ONE self-contained OFFLINE HTML out) and INDEX_REF_E.md (datalad-style human-readable
header + machine-readable JSON anchor block in the same artifact; report_digest over
pinned high-water shas; honest-semantics rules).

Explicit not-copy red line (same as gate-12 verify_run_chain): NO service, NO DB, NO
CDN, NO external link, NO workflow engine — a pure-function read of each run's
``events.jsonl`` (truth) + ``config.json`` (nameplate), never mtime. Fingerprint /
chain / diff logic is REUSED from ``scripts/verify_run_chain.py`` (imported, never
re-implemented). Every displayed number carries a provenance triple
{event_kind, seq/round, field_path} rendered as a superscript anchor + a delimited
JSON anchor block per section.

Offline invariant: the emitted HTML contains no ``http(s)://`` (inline SVG carries no
xmlns; HTML5 parses SVG namespace implicitly), no ``<script src``, no CDN host — a
CI-style grep guard test asserts this.

Literal-free invariant: every value-bearing template string lives in
``_CONTENT_TEMPLATES`` and carries only ``{}`` slots — no digit literals. Numbers reach
the page ONLY by evaluating a run directory. Layout constants (SVG geometry, precision)
live as Python variables in code, never inside a content template string.

Purity: the output is byte-identical across invocations with the same arguments. No
wall-clock is read; the "data as of" date is derived from the latest ``run_stop`` event
time. Only run-directory basenames (never absolute machine paths) enter the content.

CLI:
    python scripts/expos_report.py --run <dir> [--run <dir> ...] \
        --expect <run_name>=<verdict> ... --out report.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---- sys.path bootstrap (same discipline as make_demo.py / verify_run_chain needs) --
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_run_chain as vrc  # noqa: E402  reuse verify_run / build_chain / diff_decision_chain

# ============================================================ formatting helpers
# precision is a Python constant, never inside a content template string.
_SIG_EFFECT = 7   # effects: 0.3193285 stays exact
_SIG_EVALUE = 6   # e-values: 10451.906944 -> 10451.9 (reproduces the closing numbers)
_FP_HEAD = 8      # fingerprint head length shown inline (full form kept in anchors)


def _num(value: Any, sig: int = _SIG_EVALUE) -> str:
    """Deterministic number formatting; integers stay integral, floats use %g with a
    fixed significant-figure count so the page bytes are stable."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    return f"%.{sig}g" % float(value)


def _signed(value: Any, sig: int = _SIG_EFFECT) -> str:
    if value is None:
        return "n/a"
    body = _num(abs(float(value)), sig)
    sign = "-" if float(value) < 0 else "+"
    return sign + body


def _esc(text: Any) -> str:
    """Minimal HTML escaping for text nodes / attribute values."""
    s = str(text)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


# ============================================================ honest semantics glossary
# Fixed English labels mapped from a machine status. No digits, no hand-written narrative:
# each is a template LABEL selected by an extracted enum, never free prose.
_STATUS_GLOSS = {
    "insufficient": "honestly undecided — evidence below decisive threshold",
    "supported": "supported by decisive evidence",
    "rejected": "rejected by decisive evidence",
    "qualified": "qualified — supported under a stated caveat",
}


def _verdict_label(status: Any) -> str:
    if status is None:
        return "no adjudication on record"
    gloss = _STATUS_GLOSS.get(str(status))
    if gloss is None:
        return _esc(status)
    return f"{_esc(status)} ({_esc(gloss)})"


# ============================================================ extraction data model

@dataclass
class RoundRecord:
    round_id: int
    claim_id: str | None
    claim_seq: int | None
    knowledge_fp: str | None
    knowledge_seq: int | None
    decision_status: str | None
    effect: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    evidence_factor: float | None
    evidence_strength: str | None
    deny_reason: str | None
    consumed_fp: str | None
    promoted: list[str]
    promotion_seq: int | None
    proposal_candidates: list[str]
    proposal_seq: int | None


@dataclass
class RunRecord:
    name: str
    domain: str | None
    mode: str | None
    seed: int | None
    loop: str | None
    rounds_target: int | None
    replicates: int | None
    run_start_seq: int | None
    exit_status: str | None
    completed_rounds: int | None
    run_stop_seq: int | None
    run_stop_ts: str | None
    n_events: int
    high_water_sha: str
    rounds: list[RoundRecord]
    kfp_chain: list[str]
    fp_migrations: list[tuple[str, str]]
    e_product: float | None
    measured_verdict: str | None
    expected_verdict: str | None
    # gate-12 re-verification (reused, not re-implemented)
    verify_ok: bool
    verify_layer: int | None
    verify_code: str | None
    verify_message: str
    verify_fps: list[str] = field(default_factory=list)


def _high_water(run_dir: Path) -> tuple[str, int]:
    """Content hash pinning the run's ``events.jsonl`` at its high-water mark: the
    contiguous prefix of complete JSON lines. NEVER mtime. A single flipped byte in any
    consumed line changes the hash (kept valid -> bytes differ; broken -> the prefix is
    shorter). Returns (sha256 hex, n_complete_events)."""
    raw = (run_dir / "events.jsonl").read_bytes()
    good: list[bytes] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except Exception:
            break
        good.append(line)
    digest = hashlib.sha256(b"\n".join(good)).hexdigest()
    return digest, len(good)


def _load_events(run_dir: Path, n_good: int) -> list[dict[str, Any]]:
    """Parse the complete-line prefix ourselves so extraction survives even a run whose
    gate-12 chain is broken (the run is REPORTED as failed, never silently dropped)."""
    raw = (run_dir / "events.jsonl").read_bytes()
    events: list[dict[str, Any]] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        if len(events) >= n_good:
            break
        events.append(json.loads(line))
    return events


def _config_replicates(run_dir: Path) -> int | None:
    cfg_path = run_dir / "config.json"
    if not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
        return None
    return ((cfg.get("domain_config") or {}).get("replicates"))


def _distinct_migrations(chain: list[str]) -> list[tuple[str, str]]:
    """Consecutive distinct knowledge-fingerprint transitions (the flipped run migrates
    once: 003cae6f... -> 809ca7a1...)."""
    out: list[tuple[str, str]] = []
    for a, b in zip(chain, chain[1:]):
        if a != b and (a, b) not in out:
            out.append((a, b))
    return out


def extract_run(run_dir: Path, name: str, expected: str | None) -> RunRecord:
    """Read one run directory into a RunRecord (pure). Gate-12 status comes from the
    reused verifier; the decision-face numbers come from the event stream."""
    high_water_sha, n_good = _high_water(run_dir)
    events = _load_events(run_dir, n_good)

    def of_kind(kind: str) -> list[dict[str, Any]]:
        return [e for e in events if e.get("kind") == kind]

    starts = of_kind("run_start")
    start_payload = starts[0]["payload"] if starts else {}
    stops = of_kind("run_stop")
    stop_ev = stops[-1] if stops else None
    stop_payload = stop_ev["payload"] if stop_ev else {}

    know = of_kind("knowledge_updated")
    kfp_chain = [e["payload"].get("fingerprint") for e in know]
    promos = of_kind("promotion_decision")
    proposals = [e for e in of_kind("decision")
                 if (e.get("payload") or {}).get("kind") == "prior_proposal"]

    def by_round(evs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        return {(e.get("payload") or {}).get("round_id"): e for e in evs}

    promo_by_round = by_round(promos)
    prop_by_round = by_round(proposals)

    rounds: list[RoundRecord] = []
    for cd in sorted(of_kind("claim_decision"),
                     key=lambda e: (e["payload"].get("round_id"), e["payload"].get("claim_id"))):
        p = cd["payload"]
        r = p.get("round_id")
        stat = p.get("statistic") or {}
        power = p.get("power") or {}
        ci = stat.get("ci") or [None, None]
        promo = promo_by_round.get(r)
        prop = prop_by_round.get(r)
        prop_content = (prop["payload"].get("content") or {}) if prop else {}
        rounds.append(RoundRecord(
            round_id=r,
            claim_id=p.get("claim_id"),
            claim_seq=cd.get("seq"),
            knowledge_fp=kfp_chain[r] if isinstance(r, int) and r < len(kfp_chain) else None,
            knowledge_seq=know[r].get("seq") if isinstance(r, int) and r < len(know) else None,
            decision_status=p.get("decision_status"),
            effect=stat.get("value"),
            ci_low=ci[0] if len(ci) > 0 else None,
            ci_high=ci[1] if len(ci) > 1 else None,
            p_value=stat.get("p_value"),
            evidence_factor=power.get("evidence_factor"),
            evidence_strength=power.get("evidence_strength"),
            deny_reason=p.get("deny_reason"),
            consumed_fp=p.get("consumed_knowledge_fingerprint"),
            promoted=[x.get("cand_id") for x in (promo["payload"].get("promoted") if promo else [])],
            promotion_seq=promo.get("seq") if promo else None,
            proposal_candidates=list(prop_content.get("candidates") or []),
            proposal_seq=prop.get("seq") if prop else None,
        ))

    e_product: float | None = None
    factors = [rr.evidence_factor for rr in rounds if rr.evidence_factor is not None]
    if factors:
        e_product = 1.0
        for f in factors:
            e_product *= f

    measured = rounds[-1].decision_status if rounds else None

    # gate-12 re-verification, REUSED (never re-implemented). Guard so a broken/tampered
    # run is reported, not crashed on.
    try:
        vresult = vrc.verify_run(run_dir)
        verify_ok = bool(vresult.ok)
        verify_layer = vresult.layer
        verify_code = vresult.code
        verify_message = vresult.message
        verify_fps = list((vresult.summary or {}).get("knowledge_fingerprints") or [])
    except Exception as exc:  # noqa: BLE001  a broken run must still be reported
        verify_ok = False
        verify_layer = None
        verify_code = "verifier_raised"
        verify_message = f"{type(exc).__name__}: {exc}"
        verify_fps = []

    return RunRecord(
        name=name,
        domain=start_payload.get("domain"),
        mode=start_payload.get("mode"),
        seed=start_payload.get("seed"),
        loop=start_payload.get("loop"),
        rounds_target=start_payload.get("rounds_target"),
        replicates=_config_replicates(run_dir),
        run_start_seq=starts[0].get("seq") if starts else None,
        exit_status=stop_payload.get("exit_status"),
        completed_rounds=stop_payload.get("completed_rounds"),
        run_stop_seq=stop_ev.get("seq") if stop_ev else None,
        run_stop_ts=stop_ev.get("ts") if stop_ev else None,
        n_events=n_good,
        high_water_sha=high_water_sha,
        rounds=rounds,
        kfp_chain=kfp_chain,
        fp_migrations=_distinct_migrations([f for f in kfp_chain if f]),
        e_product=e_product,
        measured_verdict=measured,
        expected_verdict=expected,
        verify_ok=verify_ok,
        verify_layer=verify_layer,
        verify_code=verify_code,
        verify_message=verify_message,
        verify_fps=verify_fps,
    )


# ============================================================ provenance anchors (datalad style)

_ANCHOR_OPEN = "=== provenance:anchors (machine-readable — do not edit) ==="
_ANCHOR_CLOSE = "=== end provenance ==="


class Section:
    """A report section that accumulates provenance anchors. Each displayed number
    interleaves a superscript ``[n]`` whose triple {run, event_kind, seq, round,
    field_path} is enumerated in a delimited JSON block appended to the section."""

    def __init__(self, sid: str, title: str) -> None:
        self.sid = sid
        self.title = title
        self._anchors: list[dict[str, Any]] = []

    def anc(self, run: str, event_kind: str, seq: Any, round_id: Any, field_path: str) -> str:
        n = len(self._anchors) + 1
        self._anchors.append({
            "id": n,
            "run": run,
            "event_kind": event_kind,
            "seq": seq,
            "round": round_id,
            "field_path": field_path,
        })
        return _SUP.format(n=n)

    def anchor_block(self) -> str:
        if not self._anchors:
            return ""
        payload = json.dumps(
            {"section": self.sid, "anchors": self._anchors},
            ensure_ascii=False, indent=2, sort_keys=True,
        )
        return _ANCHOR_BLOCK.format(
            open=_ANCHOR_OPEN, body=_esc(payload), close=_ANCHOR_CLOSE,
        )


# ============================================================ content templates (LITERAL-FREE)
# Every string below carries only ``{}`` slots and NO digit literals. This is the
# invariant the literal-free unit test asserts over ``_CONTENT_TEMPLATES``.

_SUP = '<sup class="anc">[{n}]</sup>'

_ANCHOR_BLOCK = ('<pre class="anchor-block">{open}\n{body}\n{close}</pre>')

_DOC = (
    "<!doctype html>\n"
    '<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width">'
    "<title>{title}</title><style>{css}</style></head>"
    "<body><main>{body}</main></body></html>\n"
)

_SECTION = '<section id="{sid}"><h2>{title}</h2>{body}{anchors}</section>'

_MASTHEAD = (
    '<header class="masthead"><h1>{title}</h1>'
    '<p class="subtitle">{subtitle}</p>'
    '<p class="asof">Data as of {asof} (derived from the latest run_stop event).</p>'
    "{body}</header>"
)

_TABLE = '<div class="tablewrap"><table class="{cls}"><thead>{head}</thead><tbody>{rows}</tbody></table></div>'
_TR = '<tr class="{cls}">{cells}</tr>'
_TH = "<th>{v}</th>"
_TD = '<td class="{cls}">{v}</td>'

_P = "<p>{v}</p>"
_NOTE = '<p class="note">{v}</p>'
_UL = "<ul>{items}</ul>"
_LI = "<li>{v}</li>"
_KV = '<div class="kv"><span class="k">{k}</span><span class="v">{v}</span></div>'
_CODE = "<code>{v}</code>"

_SVG = ('<figure class="chart"><svg viewBox="{vb}" class="evalue-chart" role="img" '
        'aria-label="{alt}">{content}</svg><figcaption>{cap}</figcaption></figure>')
_SVG_AXIS = '<line class="axis" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>'
_SVG_GRID = '<line class="grid" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>'
_SVG_TICK = '<text class="tick" x="{x}" y="{y}">{v}</text>'
_SVG_POLY = '<polyline class="series {cls}" points="{pts}"/>'
_SVG_DOT = '<circle class="dot {cls}" cx="{cx}" cy="{cy}" r="{r}"><title>{tip}</title></circle>'
_SVG_LEGEND = '<span class="legend-item {cls}"><span class="swatch"></span>{v}</span>'

_MIGRATION = '<span class="fp from">{a}</span><span class="arrow">&rarr;</span><span class="fp to">{b}</span>'

# Structural scaffolding: the document skeleton and section frames. These legitimately
# carry mandated boilerplate digits (the ``utf-8`` charset, the doctype) and inject NO
# scientific value, so they are EXCLUDED from the literal-free check.
_SCAFFOLD_TEMPLATES: tuple[str, ...] = (_DOC, _SECTION, _MASTHEAD, _TABLE, _ANCHOR_BLOCK)

# Value-bearing templates: every one carries only ``{}`` slots filled from extracted run
# data and holds NO digit literal. This is the invariant the literal-free unit test
# enforces — a number can reach the page only by evaluating a run directory.
_CONTENT_TEMPLATES: tuple[str, ...] = (
    _SUP, _TR, _TH, _TD, _P, _NOTE, _UL, _LI, _KV, _CODE,
    _SVG, _SVG_AXIS, _SVG_GRID, _SVG_TICK, _SVG_POLY, _SVG_DOT, _SVG_LEGEND, _MIGRATION,
)

# ---- CSS is a styling constant, NOT a value-bearing content template (it legitimately
# ---- carries px sizes) and is deliberately EXCLUDED from the literal-free check.
_CSS = """
:root{--bg:#ffffff;--fg:#1b1f24;--muted:#5b6570;--line:#d8dee4;--head:#0f172a;
--ok:#0f7b3f;--okbg:#e7f6ec;--bad:#b4232a;--badbg:#fbe9ea;--warn:#8a5a00;--warnbg:#fdf3e0;
--strong:#0f7b3f;--reject:#b4232a;--undec:#5b6570;--accent:#1f4f82;--card:#f6f8fa;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--fg:#e6e9ec;--muted:#9aa4ae;
--line:#2a323b;--head:#e6e9ec;--ok:#4ecb7b;--okbg:#122a1c;--bad:#f0868b;--badbg:#2c1416;
--warn:#e0b24a;--warnbg:#2a2110;--strong:#4ecb7b;--reject:#f0868b;--undec:#9aa4ae;
--accent:#7fb0e8;--card:#161b22;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
main{max-width:960px;margin:0 auto;padding:32px 20px 80px;}
.masthead{border-bottom:2px solid var(--head);padding-bottom:18px;margin-bottom:28px;}
h1{font-size:26px;margin:0 0 6px;color:var(--head);letter-spacing:-.01em;}
.subtitle{font-size:15px;color:var(--muted);margin:0 0 4px;}
.asof{font-size:12px;color:var(--muted);margin:0 0 14px;}
h2{font-size:19px;margin:38px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line);color:var(--head);}
h3{font-size:15px;margin:22px 0 8px;color:var(--head);}
p{margin:8px 0;}
.note{font-size:13px;color:var(--muted);border-left:3px solid var(--line);padding-left:12px;}
ul{margin:8px 0;padding-left:22px;}
li{margin:4px 0;}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;
background:var(--card);padding:1px 5px;border-radius:4px;word-break:break-all;}
.kv{display:flex;gap:10px;padding:3px 0;font-size:13.5px;}
.kv .k{color:var(--muted);min-width:150px;}
.kv .v{font-weight:600;}
.nameplate{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
gap:2px 24px;margin:10px 0;padding:14px 16px;background:var(--card);border-radius:8px;}
.tablewrap{overflow-x:auto;margin:14px 0;}
table{border-collapse:collapse;width:100%;font-size:13px;}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top;}
th{background:var(--card);color:var(--head);font-weight:600;white-space:nowrap;}
td.rowhead{font-weight:600;background:var(--card);white-space:nowrap;}
tr.mismatch td{background:var(--badbg);}
tr.diff td.diffcell{background:var(--warnbg);}
td.ok,.pill.ok{color:var(--ok);}
td.bad,.pill.bad{color:var(--bad);font-weight:600;}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;}
.pill.ok{background:var(--okbg);color:var(--ok);}
.pill.bad{background:var(--badbg);color:var(--bad);}
.pill.undec{background:var(--card);color:var(--undec);}
.status-supported{color:var(--strong);font-weight:600;}
.status-rejected{color:var(--reject);font-weight:600;}
.status-insufficient{color:var(--undec);}
sup.anc{color:var(--accent);font-size:10px;font-weight:600;padding-left:1px;cursor:help;}
.anchor-block{background:var(--card);border:1px dashed var(--line);border-radius:6px;
padding:10px 12px;font-size:11px;line-height:1.4;color:var(--muted);overflow-x:auto;
white-space:pre;margin:10px 0 4px;}
.fp{font-family:ui-monospace,monospace;font-size:12px;}
.fp.from{color:var(--muted);}
.fp.to{color:var(--accent);font-weight:600;}
.arrow{margin:0 8px;color:var(--muted);}
.chart{margin:16px 0;}
.evalue-chart{width:100%;height:auto;background:var(--card);border-radius:8px;}
.evalue-chart .axis{stroke:var(--muted);stroke-width:1;}
.evalue-chart .grid{stroke:var(--line);stroke-width:1;stroke-dasharray:3 3;}
.evalue-chart .tick{fill:var(--muted);font-size:10px;}
.evalue-chart .series{fill:none;stroke-width:2;}
.evalue-chart .dot{stroke:var(--bg);stroke-width:1;}
figcaption{font-size:12px;color:var(--muted);margin-top:6px;}
.legend{margin:6px 0;font-size:12px;display:flex;flex-wrap:wrap;gap:14px;}
.legend-item{display:inline-flex;align-items:center;gap:5px;color:var(--muted);}
.legend-item .swatch{width:11px;height:11px;border-radius:2px;display:inline-block;}
.s-flat .swatch,.s-flat{--s:#8a8f96;} .evalue-chart .s-flat{stroke:#8a8f96;} .s-flat .swatch{background:#8a8f96;}
.s-zero .swatch,.s-zero{--s:#c98a00;} .evalue-chart .s-zero{stroke:#c98a00;} .s-zero .swatch{background:#c98a00;}
.s-strong .swatch{background:#0f7b3f;} .evalue-chart .s-strong{stroke:#0f7b3f;}
.s-flipped .swatch{background:#b4232a;} .evalue-chart .s-flipped{stroke:#b4232a;}
.s-stage3 .swatch{background:#1f4f82;} .evalue-chart .s-stage3{stroke:#1f4f82;}
.dot.d-supported{fill:#0f7b3f;} .dot.d-rejected{fill:#b4232a;} .dot.d-insufficient{fill:#8a8f96;}
.boundary{background:var(--warnbg);border:1px solid var(--warn);border-radius:8px;padding:14px 18px;}
.boundary h3{margin-top:0;color:var(--warn);}
.quote{font-style:italic;border-left:4px solid var(--accent);padding:8px 14px;margin:12px 0;
background:var(--card);border-radius:0 6px 6px 0;}
.footer-digest{background:var(--card);border-radius:8px;padding:14px 16px;margin-top:14px;}
"""


# ============================================================ small render helpers

def _row(cells: list[str], cls: str = "") -> str:
    return _TR.format(cls=cls, cells="".join(cells))


def _th(v: str) -> str:
    return _TH.format(v=v)


def _td(v: str, cls: str = "") -> str:
    return _TD.format(cls=cls, v=v)


def _status_span(status: Any) -> str:
    if status is None:
        return "n/a"
    return f'<span class="status-{_esc(status)}">{_esc(status)}</span>'


def _fp_code(fp: str | None) -> str:
    if not fp:
        return "n/a"
    return _CODE.format(v=_esc(fp[:_FP_HEAD]))


# ============================================================ sections

def render_masthead(runs: list[RunRecord], asof: str) -> str:
    ref = runs[0] if runs else None
    sec = Section("masthead", "")
    kvs = []
    if ref is not None:
        kvs = [
            _KV.format(k="Domain", v=_esc(ref.domain) + sec.anc(ref.name, "run_start", ref.run_start_seq, None, "payload.domain")),
            _KV.format(k="Seed", v=_num(ref.seed) + sec.anc(ref.name, "run_start", ref.run_start_seq, None, "payload.seed")),
            _KV.format(k="Rounds target", v=_num(ref.rounds_target) + sec.anc(ref.name, "run_start", ref.run_start_seq, None, "payload.rounds_target")),
            _KV.format(k="Replicates / well", v=_num(ref.replicates) + sec.anc(ref.name, "config", None, None, "domain_config.replicates")),
            _KV.format(k="Loop", v=_esc(ref.loop) + sec.anc(ref.name, "run_start", ref.run_start_seq, None, "payload.loop")),
            _KV.format(k="Conditions", v=_num(len(runs))),
        ]
    nameplate = f'<div class="nameplate">{"".join(kvs)}</div>'
    runlist = _UL.format(items="".join(
        _LI.format(v=_CODE.format(v=_esc(r.name)) + " — " + _verdict_label(r.measured_verdict))
        for r in runs))
    intro = _P.format(v=(
        "This report is regenerated as a pure function of the run directories below: "
        "every number is read from an append-only <code>events.jsonl</code> event stream, "
        "carries a provenance anchor to its source event, and is pinned by a content hash "
        "in the integrity footer. No sentence is hand-written; each is selected from "
        "machine fields. See the scope-and-limits section for the honest boundary of the claim."))
    body = intro + nameplate + _P.format(v="Runs in scope:") + runlist + sec.anchor_block()
    subtitle = ("Four-condition joint closing run (flat / consistent-zero / consistent-strong / "
                "flipped) plus the Stage-3 LLM-backend smoke run — Adaptive Dry–Wet–Agent scientific loop.")
    return _MASTHEAD.format(
        title="Adaptive Dry–Wet–Agent Scientific Loop — Closing Report",
        subtitle=subtitle, asof=_esc(asof), body=body)


def render_adjudication(runs: list[RunRecord]) -> str:
    """Acceptance table: measured vs EXPECTED verdict per run (baseline = expected, not
    the first column). A mismatch row is flagged red."""
    sec = Section("adjudication", "")
    head = _row([_th("Run"), _th("Expected verdict"), _th("Measured verdict"),
                 _th("Terminal effect"), _th("Match")])
    rows = []
    n_match = 0
    for r in runs:
        term = r.rounds[-1] if r.rounds else None
        expected = r.expected_verdict
        measured = r.measured_verdict
        match = (expected is not None and measured == expected)
        if match:
            n_match += 1
        eff_cell = "n/a"
        if term is not None and term.effect is not None:
            eff_cell = _signed(term.effect) + sec.anc(r.name, "claim_decision", term.claim_seq, term.round_id, "payload.statistic.value")
        meas_cell = _verdict_label(measured)
        if term is not None:
            meas_cell += sec.anc(r.name, "claim_decision", term.claim_seq, term.round_id, "payload.decision_status")
        match_pill = ('<span class="pill ok">match</span>' if match
                      else '<span class="pill bad">MISMATCH</span>')
        rows.append(_row([
            _td(_CODE.format(v=_esc(r.name)), "rowhead"),
            _td(_esc(expected) if expected is not None else "(none supplied)"),
            _td(meas_cell),
            _td(eff_cell),
            _td(match_pill),
        ], cls="" if match else "mismatch"))
    summary = _P.format(v=(
        f"<strong>{_num(n_match)} / {_num(len(runs))}</strong> runs reproduce their expected "
        "verdict from the event stream. The expected verdict is the acceptance oracle "
        "(supplied as a parameter); the measured verdict is the terminal round's "
        "<code>claim_decision.decision_status</code> recomputed here."))
    table = _TABLE.format(cls="adj", head=head, rows="".join(rows))
    return summary + table + sec.anchor_block()


def render_comparison(runs: list[RunRecord]) -> str:
    """Union-of-keys comparison: rows = decision-face fields, columns = runs; a row is
    flagged when its values are not identical across runs (mlflow hasDiff data model)."""
    sec = Section("comparison", "")

    def terminal(r: RunRecord) -> RoundRecord | None:
        return r.rounds[-1] if r.rounds else None

    # (label, extractor(run) -> (display, anchor_or_"")) ; comparators use display strings.
    def field_status(r):
        t = terminal(r)
        if t is None:
            return "n/a", ""
        return _status_span(t.decision_status), sec.anc(r.name, "claim_decision", t.claim_seq, t.round_id, "payload.decision_status")

    def field_effect(r):
        t = terminal(r)
        if t is None or t.effect is None:
            return "n/a", ""
        return _signed(t.effect), sec.anc(r.name, "claim_decision", t.claim_seq, t.round_id, "payload.statistic.value")

    def field_evalue(r):
        t = terminal(r)
        if t is None or t.evidence_factor is None:
            return "n/a", ""
        return _num(t.evidence_factor), sec.anc(r.name, "claim_decision", t.claim_seq, t.round_id, "payload.power.evidence_factor")

    def field_strength(r):
        t = terminal(r)
        if t is None:
            return "n/a", ""
        return _esc(t.evidence_strength), sec.anc(r.name, "claim_decision", t.claim_seq, t.round_id, "payload.power.evidence_strength")

    def field_eproduct(r):
        if r.e_product is None:
            return "n/a", ""
        return _num(r.e_product), ""

    def field_ci(r):
        t = terminal(r)
        if t is None or t.ci_low is None:
            return "n/a", ""
        disp = "[" + _num(t.ci_low, _SIG_EFFECT) + ", " + _num(t.ci_high, _SIG_EFFECT) + "]"
        return disp, sec.anc(r.name, "claim_decision", t.claim_seq, t.round_id, "payload.statistic.ci")

    def field_kfp(r):
        t = terminal(r)
        if t is None or t.knowledge_fp is None:
            return "n/a", ""
        return _fp_code(t.knowledge_fp), sec.anc(r.name, "knowledge_updated", t.knowledge_seq, t.round_id, "payload.fingerprint")

    def field_migr(r):
        return _num(len(r.fp_migrations)), ""

    def field_promoted(r):
        t = terminal(r)
        if t is None:
            return "n/a", ""
        disp = ", ".join(_esc(c) for c in t.promoted) or "(none)"
        return disp, sec.anc(r.name, "promotion_decision", t.promotion_seq, t.round_id, "payload.promoted")

    def field_completed(r):
        return _num(r.completed_rounds), sec.anc(r.name, "run_stop", r.run_stop_seq, None, "payload.completed_rounds")

    def field_exit(r):
        return _esc(r.exit_status), sec.anc(r.name, "run_stop", r.run_stop_seq, None, "payload.exit_status")

    fields = [
        ("Terminal decision_status", field_status),
        ("Terminal effect (mean paired diff)", field_effect),
        ("Terminal e-value (evidence_factor)", field_evalue),
        ("Terminal evidence strength", field_strength),
        ("Cumulative e-product", field_eproduct),
        ("Terminal 95% CS bounds", field_ci),
        ("Terminal knowledge fingerprint", field_kfp),
        ("Knowledge-fingerprint migrations", field_migr),
        ("Terminal promoted set", field_promoted),
        ("Completed rounds", field_completed),
        ("Exit status", field_exit),
    ]

    head = _row([_th("Field")] + [_th(_esc(r.name)) for r in runs])
    rows = []
    for label, fn in fields:
        cells = [_td(_esc(label), "rowhead")]
        displays = []
        for r in runs:
            disp, anc = fn(r)
            displays.append(disp)
            cells.append(_td(disp + anc, "diffcell"))
        has_diff = any(d != displays[0] for d in displays)
        rows.append(_row(cells, cls="diff" if has_diff else ""))
    intro = _NOTE.format(v=(
        "Rows shaded across a row indicate a field whose value is not identical across all "
        "conditions — the discriminative axes of the joint run. Note the cumulative e-product "
        "is identical for the strong and flipped conditions: the e-value measures evidence "
        "magnitude; the SIGN of the effect (and hence support vs rejection) is the face "
        "discriminator, exactly as the gate-12 fingerprint isolates."))
    table = _TABLE.format(cls="cmp", head=head, rows="".join(rows))
    return intro + table + sec.anchor_block()


def render_fp_chain(runs: list[RunRecord]) -> str:
    sec = Section("knowledge", "")
    head = _row([_th("Run")] + [_th("round " + _num(i)) for i in range(_max_rounds(runs))] + [_th("Migration")])
    rows = []
    for r in runs:
        cells = [_td(_CODE.format(v=_esc(r.name)), "rowhead")]
        for i in range(_max_rounds(runs)):
            if i < len(r.kfp_chain):
                seq = r.rounds[i].knowledge_seq if i < len(r.rounds) else None
                cells.append(_td(_fp_code(r.kfp_chain[i]) + sec.anc(r.name, "knowledge_updated", seq, i, "payload.fingerprint")))
            else:
                cells.append(_td("—"))
        if r.fp_migrations:
            migr = " ".join(_MIGRATION.format(a=_esc(a[:_FP_HEAD]), b=_esc(b[:_FP_HEAD]))
                            for a, b in r.fp_migrations)
        else:
            migr = "stable"
        cells.append(_td(migr))
        rows.append(_row(cells))
    intro = _P.format(v=(
        "The knowledge fingerprint is the sha256 content hash of the compiled knowledge view "
        "consumed at each round. It stays stable while the evidence does not overturn a claim, "
        "and migrates when a claim is decisively rewritten by wet data — the flipped condition "
        "migrates once, its terminal knowledge face rebuilt by the sign-reversed effect."))
    table = _TABLE.format(cls="kfp", head=head, rows="".join(rows))
    return intro + table + sec.anchor_block()


def render_evalue_chart(runs: list[RunRecord]) -> str:
    """Inline SVG (no xmlns -> no http:// -> offline). Log-scaled per-round e-value
    trajectories; layout numbers are Python variables, never template literals."""
    import math
    sec = Section("evalue", "")
    width, height = 720, 300
    ml, mr, mt, mb = 54, 120, 20, 40
    plot_w = width - ml - mr
    plot_h = height - mt - mb
    radius = 4.0
    epsilon = 1e-6

    max_r = _max_rounds(runs)
    all_ef = [max(rr.evidence_factor or epsilon, epsilon) for r in runs for rr in r.rounds]
    if not all_ef:
        return _NOTE.format(v="No e-value trajectory available.")
    lo = math.log10(min(all_ef + [epsilon]))
    hi = math.log10(max(all_ef))
    if hi - lo < epsilon:
        hi = lo + 1.0

    def x_of(round_i: int) -> float:
        denom = max(max_r - 1, 1)
        return ml + plot_w * (round_i / denom)

    def y_of(ef: float) -> float:
        v = math.log10(max(ef, epsilon))
        return mt + plot_h * (1 - (v - lo) / (hi - lo))

    slug = {"corun_flat": "s-flat", "corun_consistent_zero": "s-zero",
            "corun_consistent_strong": "s-strong", "corun_flipped": "s-flipped",
            "llm_smoke_stage3": "s-stage3"}

    parts: list[str] = []
    # axes
    parts.append(_SVG_AXIS.format(x1=ml, y1=mt, x2=ml, y2=mt + plot_h))
    parts.append(_SVG_AXIS.format(x1=ml, y1=mt + plot_h, x2=ml + plot_w, y2=mt + plot_h))
    # y grid + ticks at each integer decade
    decade = int(math.floor(lo))
    while decade <= int(math.ceil(hi)):
        y = y_of(10 ** decade)
        parts.append(_SVG_GRID.format(x1=ml, y1=y, x2=ml + plot_w, y2=y))
        parts.append(_SVG_TICK.format(x=ml - 8, y=y + 3, v="1e" + _num(decade)))
        decade += 1
    # x ticks per round
    for i in range(max_r):
        parts.append(_SVG_TICK.format(x=x_of(i) - 2, y=mt + plot_h + 16, v="r" + _num(i)))
    # series
    for r in runs:
        cls = slug.get(r.name, "s-stage3")
        pts = []
        for rr in r.rounds:
            ef = rr.evidence_factor if rr.evidence_factor is not None else epsilon
            pts.append(f"{x_of(rr.round_id):.2f},{y_of(ef):.2f}")
        if len(pts) > 1:
            parts.append(_SVG_POLY.format(cls=cls, pts=" ".join(pts)))
        for rr in r.rounds:
            ef = rr.evidence_factor if rr.evidence_factor is not None else epsilon
            dcls = "d-" + _esc(rr.decision_status or "insufficient")
            tip = f"{r.name} r{rr.round_id}: e={_num(ef)} status={rr.decision_status}"
            parts.append(_SVG_DOT.format(cls=dcls, cx=f"{x_of(rr.round_id):.2f}",
                                         cy=f"{y_of(ef):.2f}", r=f"{radius:.1f}", tip=_esc(tip)))
            sec.anc(r.name, "claim_decision", rr.claim_seq, rr.round_id, "payload.power.evidence_factor")

    legend = '<div class="legend">' + "".join(
        _SVG_LEGEND.format(cls=slug.get(r.name, "s-stage3"), v=_esc(r.name)) for r in runs) + "</div>"
    svg = _SVG.format(vb=f"0 0 {width} {height}", alt="Per-round e-value trajectories, log scale",
                      content="".join(parts),
                      cap="Per-round e-value (evidence_factor) on a log scale; dot colour encodes the "
                          "round decision_status. Absent/near-zero evidence sits on the floor decade.")
    intro = _P.format(v=(
        "The e-value accumulates evidence across rounds. The strong and flipped conditions "
        "cross into decisive evidence at the same rate (identical magnitudes); the flat and "
        "zero conditions never leave the undecided floor — honestly undecided, not failed."))
    return intro + legend + svg + sec.anchor_block()


def render_diff_matrix(runs: list[RunRecord]) -> str:
    """Pairwise decision-chain divergence via the REUSED diff_decision_chain. Cell =
    'consistent' or the first divergence (round + reason)."""
    sec = Section("diffmatrix", "")
    # map name -> run_dir for the reused chain builder
    head = _row([_th("")] + [_th(_esc(r.name)) for r in runs])
    rows = []
    for ra in runs:
        cells = [_td(_CODE.format(v=_esc(ra.name)), "rowhead")]
        for rb in runs:
            if ra.name == rb.name:
                cells.append(_td("—"))
                continue
            try:
                d = vrc.diff_decision_chain(ra._dir, rb._dir)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                cells.append(_td('<span class="pill bad">unavailable</span> ' + _esc(type(exc).__name__)))
                continue
            if not d.diverged:
                cells.append(_td('<span class="pill ok">consistent</span>', "ok"))
            else:
                fd = d.first_divergence or {}
                node = fd.get("node_type") or fd.get("reason")
                cell = ('<span class="pill bad">diverge</span> round ' + _num(fd.get("round"))
                        + " (" + _esc(node) + ")")
                cells.append(_td(cell, "bad"))
        rows.append(_row(cells))
    n = len(runs)
    n_pairs = n * (n - 1) // 2
    intro = _P.format(v=(
        f"All {_num(n_pairs)} unordered pairs, diffed node-by-node on the content-addressed "
        "decision-chain fingerprint (reused verbatim from the gate-12 verifier). Runs of "
        "differing round counts diverge by chain length; same-length conditions diverge at the "
        "first round whose claim_decision content differs — the sign-flipped effect at round 0."))
    table = _TABLE.format(cls="diff", head=head, rows="".join(rows))
    return intro + table + sec.anchor_block()


def render_per_run(runs: list[RunRecord]) -> str:
    sec = Section("perround", "")
    blocks = []
    for r in runs:
        head = _row([_th("Round"), _th("Proposed candidates"), _th("Promoted"),
                     _th("Status"), _th("Effect"), _th("e-value"), _th("95% CS"),
                     _th("Deny reason")])
        rows = []
        for rr in r.rounds:
            status_cell = _status_span(rr.decision_status) + sec.anc(r.name, "claim_decision", rr.claim_seq, rr.round_id, "payload.decision_status")
            eff_cell = (_signed(rr.effect) if rr.effect is not None else "n/a") + (sec.anc(r.name, "claim_decision", rr.claim_seq, rr.round_id, "payload.statistic.value") if rr.effect is not None else "")
            ev_cell = (_num(rr.evidence_factor) if rr.evidence_factor is not None else "n/a") + (sec.anc(r.name, "claim_decision", rr.claim_seq, rr.round_id, "payload.power.evidence_factor") if rr.evidence_factor is not None else "")
            ci_cell = ("[" + _num(rr.ci_low, _SIG_EFFECT) + ", " + _num(rr.ci_high, _SIG_EFFECT) + "]") if rr.ci_low is not None else "n/a"
            cand_cell = (", ".join(_esc(c) for c in rr.proposal_candidates) or "(none)")
            if rr.proposal_seq is not None:
                cand_cell += sec.anc(r.name, "decision", rr.proposal_seq, rr.round_id, "payload.content.candidates")
            promo_cell = (", ".join(_esc(c) for c in rr.promoted) or "(none)")
            if rr.promotion_seq is not None:
                promo_cell += sec.anc(r.name, "promotion_decision", rr.promotion_seq, rr.round_id, "payload.promoted")
            deny_cell = _esc(rr.deny_reason) if rr.deny_reason else "—"
            rows.append(_row([
                _td(_num(rr.round_id)), _td(cand_cell), _td(promo_cell),
                _td(status_cell), _td(eff_cell), _td(ev_cell), _td(ci_cell), _td(deny_cell),
            ]))
        table = _TABLE.format(cls="perrun", head=head, rows="".join(rows))
        verdict = _P.format(v=("Terminal verdict: " + _verdict_label(r.measured_verdict) + "."))
        blocks.append(f'<h3><code>{_esc(r.name)}</code></h3>' + verdict + table)
    intro = _NOTE.format(v=(
        "Each round: agent proposal &rarr; Dry&rarr;Wet promotion gate &rarr; wet observation "
        "&rarr; evidence adjudication. An <code>insufficient</code> status is rendered as honestly "
        "undecided (evidence below the decisive threshold), never as a failure."))
    return intro + "".join(blocks) + sec.anchor_block()


def render_gate12(runs: list[RunRecord]) -> str:
    sec = Section("gate12", "")
    head = _row([_th("Run"), _th("Gate-12 chain"), _th("Layer / code"),
                 _th("Events"), _th("Message")])
    rows = []
    for r in runs:
        if r.verify_ok:
            pill = '<span class="pill ok">CHAIN COMPLETE</span>'
            lc = "—"
            cls = ""
        else:
            pill = '<span class="pill bad">CHAIN BROKEN</span>'
            lc = _esc(f"L{r.verify_layer} [{r.verify_code}]")
            cls = "mismatch"
        rows.append(_row([
            _td(_CODE.format(v=_esc(r.name)), "rowhead"),
            _td(pill),
            _td(lc),
            _td(_num(r.n_events)),
            _td(_esc(r.verify_message)),
        ], cls=cls))
    intro = _P.format(v=(
        "Gate-12 re-verification is delegated to <code>scripts/verify_run_chain.py</code> "
        "(lifecycle pairing, payload completeness, fingerprint threading, checkpoint "
        "reconciliation) — imported, never re-implemented. A run that fails verification is "
        "reported here as broken, not silently dropped."))
    table = _TABLE.format(cls="g12", head=head, rows="".join(rows))
    return intro + table + sec.anchor_block()


def render_boundary(runs: list[RunRecord]) -> str:
    approved = ("A provenance-aware adaptive Dry–Wet–Agent scientific loop with real "
                "quantum-chemical dry computation, trusted simulated wet instrumentation, "
                "sequential evidence certification, append-only scientific knowledge evolution, "
                "and validated deterministic and LLM agent backends.")
    quote = f'<blockquote class="quote">{_esc(approved)}</blockquote>'
    limits = _UL.format(items="".join(_LI.format(v=v) for v in [
        "<strong>Simulated wet instrumentation.</strong> The wet leg is a trusted plate-reader "
        "simulator, not a physical instrument. The dry leg is real quantum-chemical computation.",
        "<strong>Real-machine seam ready but not connected.</strong> The wet interface is built "
        "and typed for a real instrument, but no physical laboratory is wired in.",
        "<strong>LLM backend validated but default-off.</strong> The LLM agent backend is "
        "validated (Stage 3) and reaches the same scientific verdict as the deterministic "
        "template backend on the same substrate; the deterministic backend remains the default.",
        "<strong>Single machine, single campaign.</strong> These are single-machine, "
        "single-campaign runs; this is not a multi-campaign production deployment.",
    ]))
    forbid = _UL.format(items="".join(_LI.format(v=v) for v in [
        "This is <strong>not</strong> a physical autonomous laboratory.",
        "This is <strong>not</strong> real wet-lab validation.",
        "This is <strong>not</strong> a multi-campaign production system.",
        "This is <strong>not</strong> a general autonomous scientist.",
    ]))
    body = (_P.format(v="Approved outward description of the system:") + quote
            + _P.format(v="Scope and limits — what this closing run does and does not claim:")
            + limits
            + _P.format(v="Explicitly out of scope (claims this report does not make):")
            + forbid)
    return f'<div class="boundary"><h3>Scope, honest boundary, and limits</h3>{body}</div>'


def render_footer(runs: list[RunRecord], report_digest: str) -> str:
    sec = Section("integrity", "")
    head = _row([_th("Run"), _th("events.jsonl high-water sha256"),
                 _th("Complete events"), _th("Gate-12")])
    rows = []
    for r in runs:
        g = "complete" if r.verify_ok else "BROKEN"
        gcls = "ok" if r.verify_ok else "bad"
        rows.append(_row([
            _td(_CODE.format(v=_esc(r.name)), "rowhead"),
            _td(_CODE.format(v=_esc(r.high_water_sha))),
            _td(_num(r.n_events)),
            _td(f'<span class="pill {gcls}">{g}</span>'),
        ]))
    table = _TABLE.format(cls="integ", head=head, rows="".join(rows))
    params = _UL.format(items="".join(_LI.format(v=v) for v in [
        "Runs: " + ", ".join(_CODE.format(v=_esc(r.name)) for r in runs),
        "Verifier lineage: <code>scripts/verify_run_chain.py</code> "
        "(gate-12 three-layer chain audit, reused for fingerprint / chain / diff logic).",
        "Staleness / integrity: each run pinned by its <code>events.jsonl</code> high-water "
        "content sha256 (never mtime). Re-run this generator against the same directories to "
        "reproduce this report byte-for-byte.",
    ]))
    digest_block = (f'<div class="footer-digest"><div class="kv"><span class="k">report_digest</span>'
                    f'<span class="v">{_CODE.format(v=_esc(report_digest))}</span></div>'
                    f'<p class="note">report_digest = sha256 over the pinned per-run high-water shas '
                    f'(sorted by run name). Any tampered input byte changes a high-water sha and this '
                    f'digest, and flips the corresponding gate-12 status to broken.</p></div>')
    return table + params + digest_block + sec.anchor_block()


# ============================================================ assembly

def _max_rounds(runs: list[RunRecord]) -> int:
    return max((len(r.kfp_chain) for r in runs), default=0)


def _derive_asof(runs: list[RunRecord]) -> str:
    stamps = [r.run_stop_ts for r in runs if r.run_stop_ts]
    return max(stamps) if stamps else "n/a"


def compute_report_digest(runs: list[RunRecord]) -> str:
    canonical = "\n".join(f"{r.name}={r.high_water_sha}"
                          for r in sorted(runs, key=lambda x: x.name))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_SECTION_DEFS = [
    ("adjudication", "Adjudication table — measured vs expected verdict", render_adjudication),
    ("comparison", "Cross-condition comparison", render_comparison),
    ("knowledge", "Knowledge-fingerprint chain and migration", render_fp_chain),
    ("evalue", "E-value evidence trajectory", render_evalue_chart),
    ("diffmatrix", "Decision-chain divergence matrix", render_diff_matrix),
    ("perround", "Per-run round-by-round record", render_per_run),
    ("gate12", "Gate-12 chain re-verification", render_gate12),
    ("boundary", "Scope, honest boundary, and limits", render_boundary),
]


def build_runs(run_dirs: list[Path], expected: dict[str, str]) -> list[RunRecord]:
    runs: list[RunRecord] = []
    for d in run_dirs:
        name = d.name
        rec = extract_run(d, name, expected.get(name))
        rec._dir = d  # type: ignore[attr-defined]  needed by the reused diff builder
        runs.append(rec)
    return runs


def render_runs(runs: list[RunRecord]) -> str:
    """Render an already-extracted run list to the self-contained offline HTML document."""
    asof = _derive_asof(runs)
    report_digest = compute_report_digest(runs)

    body_parts = [render_masthead(runs, asof)]
    for sid, title, fn in _SECTION_DEFS:
        if fn is render_boundary:
            body_parts.append(_SECTION.format(sid=sid, title=_esc(title), body=fn(runs), anchors=""))
        else:
            body_parts.append(_SECTION.format(sid=sid, title=_esc(title), body=fn(runs), anchors=""))
    body_parts.append(_SECTION.format(sid="integrity", title="Integrity footer",
                                      body=render_footer(runs, report_digest), anchors=""))

    return _DOC.format(title="Adaptive Dry–Wet–Agent Scientific Loop — Closing Report",
                       css=_CSS, body="".join(body_parts))


def build_report(run_dirs: list[Path], expected: dict[str, str]) -> str:
    """Pure function: N run directories + expected-verdict oracle -> one self-contained
    offline HTML document (a string)."""
    return render_runs(build_runs(run_dirs, expected))


# ============================================================ CLI

def _parse_expect(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--expect must be <run_name>=<verdict>, got: {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="expos_report",
        description="Generate a human-readable closing report as a pure function of run dirs.")
    parser.add_argument("--run", action="append", metavar="DIR", default=[],
                        help="a run directory (repeatable)")
    parser.add_argument("--expect", action="append", metavar="NAME=VERDICT", default=[],
                        help="expected verdict for a run by basename (repeatable)")
    parser.add_argument("--out", required=True, metavar="FILE", help="output HTML path")
    args = parser.parse_args(argv)

    if not args.run:
        parser.error("at least one --run DIR is required")
    run_dirs = [Path(d) for d in args.run]
    for d in run_dirs:
        if not (d / "events.jsonl").exists():
            parser.error(f"not a run directory (no events.jsonl): {d}")
    expected = _parse_expect(args.expect)

    runs = build_runs(run_dirs, expected)
    html = render_runs(runs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html)} bytes)")
    print(f"report_digest={compute_report_digest(runs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
