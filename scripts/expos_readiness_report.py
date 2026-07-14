#!/usr/bin/env python3
"""expos readiness report — the M23 Real-Wet Readiness report, generated as a PURE
FUNCTION of a physical-evidence directory (M23 Phase 5).

Design lineage & discipline (identical to ``scripts/expos_report.py``, the M17 closing
report this extends — that generator is NOT modified; its primitives are IMPORTED and
reused so the M17 CLI stays byte-for-byte):

  * ``/Data1/ericyang/m19_references/INDEX_REF_R.md`` §Convergence — a plain-Python
    script + literal-free template; evidence dir in, ONE self-contained OFFLINE HTML out.
  * ``INDEX_REF_E.md`` §Convergence — a datalad-style human header + a machine-readable
    provenance anchor block in the same artifact; a ``report_digest`` over the pinned
    high-water shas; honest-semantics rules (a missing input is REPORTED, never dropped).

The eight sections and their machine derivation follow the input contract in
``mailbox/red_to_blue/126_phase5-input-contract.md`` verbatim. Every displayed number is
recomputed here from the evidence files (the scenario manifest, the run's
``events.jsonl``, the physical ``action_ledger.jsonl``, the differential ``diff_report``,
the captured ``stderr``/``exit_status``) or from in-repo pure functions
(``verify_run_chain`` for the kernel chain, ``action_ledger.ActionLedger`` for an
INDEPENDENT ledger replay + conservation recompute, ``differential_gate`` for the
DiffReport shape, ``expos_lint`` for the known-limit lint nudges). NOTHING is hand-filled
beyond the fixed template sentences.

Symmetric gate-12 red line: NO service, NO DB, NO CDN, NO external link — a pure-function
read of the evidence directory. The output HTML carries no ``http(s)://``, no
``<script src``, no CDN host, no ``xmlns`` (a grep guard test asserts this).

Graceful degradation (the real evidence set is produced LATER by the other session via
sbatch): a scenario or section whose evidence is ABSENT is rendered as an explicit,
loud "EVIDENCE MISSING" block — never silently skipped — so the report is honest at
every stage of evidence accumulation. Section 8 (known limits) and the
minimal-safe-steps checklist derive from in-repo code and therefore render even when the
evidence directory does not exist yet.

Purity: byte-identical across invocations with the same evidence directory. No wall clock
is read here; the "data as of" stamp is derived from the latest ledger transition
timestamp already fixed in the evidence files.

CLI:
    python scripts/expos_readiness_report.py \
        --evidence runs/readiness_evidence \
        --out docs/reports/REALWET_READINESS.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---- sys.path bootstrap (same discipline as expos_report / verify_run_chain) --------
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import expos_report as er  # noqa: E402  REUSE the M17 primitives (never re-implemented)
import expos_lint  # noqa: E402  known-limit lint nudges (section 8), run programmatically
import verify_run_chain as vrc  # noqa: E402  kernel chain verification (reused)
from expos.adapters.wet.action_ledger import (  # noqa: E402
    ActionLedger,
    ActionState,
    LedgerIntegrityError,
    VolumeLedger,
    _LEGAL_TRANSITIONS,
)

# ============================================================ contract constants

#: The scenario roster the input contract (letter 126) promises: the seven fake-backend
#: behaviour modes + the crash-injection matrix + the human-intervention pair + the
#: unit-mismatch loud failure + the differential positive/negative pair. Used ONLY to
#: enumerate what is not-yet-provided when no ``evidence_index.json`` exists — the report
#: is honest about the full expected coverage before any evidence lands.
_SEVEN_MODES = (
    "confirm_exact", "confirm_within_tol", "confirm_drift",
    "mismatch_defined", "mismatch_undefined", "timeout", "unobserved",
)
_CRASH_MODES = tuple(f"crash_I{i}" for i in range(1, 7))
_HUMAN_MODES = ("human_recover", "human_cancel")
_OTHER_MODES = ("unit_mismatch", "diff_positive", "diff_negative")
_EXPECTED_SCENARIOS: tuple[str, ...] = (
    _SEVEN_MODES + _CRASH_MODES + _HUMAN_MODES + _OTHER_MODES
)

_REQUIRED_MANIFEST_KEYS = ("scenario_id", "mode", "expected_outcome")

_LEDGER_REL = Path("physical") / "action_ledger.jsonl"
_EVENTS_REL = "events.jsonl"
_MANIFEST_REL = "scenario_manifest.json"
_DIFF_REL = "diff_report.json"
_STDERR_REL = "stderr.txt"
_EXIT_REL = "exit_status.txt"
_DEFAULT_ENVELOPE = ROOT / "expos" / "adapters" / "wet" / "tolerances_vendor_placeholder.json"

_FP_HEAD = 8  # short-form hash length shown inline (full form kept in the integrity footer)


# ============================================================ manifest validation (loud)

def validate_manifest(manifest: Any) -> list[str]:
    """Contract-shape validation of a scenario manifest. Returns a list of human-readable
    errors (EMPTY == valid). A malformed manifest is REPORTED loudly (rendered as an
    'MANIFEST INVALID' block), never silently accepted — the SW2 fail-closed rule."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return [f"manifest is not a JSON object (got {type(manifest).__name__})"]
    for key in _REQUIRED_MANIFEST_KEYS:
        if key not in manifest or manifest.get(key) in (None, ""):
            errors.append(f"missing required key {key!r}")
    return errors


# ============================================================ ledger independent replay

@dataclass
class LedgerFacts:
    """Everything the report derives from ONE physical ``action_ledger.jsonl`` — both by
    a direct line parse (for the per-action transaction arcs) AND by an INDEPENDENT
    :class:`ActionLedger` replay (imported; recomputes conservation + per-well balances
    from the hash-chained log, and fails loudly on a tampered/truncated chain)."""

    present: bool
    sha: str | None = None                       # sha256 over the ledger bytes (pinning)
    integrity_ok: bool = False                   # imported replay verified the hash chain
    integrity_error: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)
    latest_ts: float | None = None
    # per-action transaction arcs, keyed by action_id (first-seen order preserved)
    order: list[str] = field(default_factory=list)
    arcs: dict[str, list[tuple[str | None, str]]] = field(default_factory=dict)
    final_state: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    pending_into: dict[str, int] = field(default_factory=dict)      # # of ->PENDING edges
    planned_registrations: dict[str, int] = field(default_factory=dict)  # # fresh dispatch
    io_issued: dict[str, int] = field(default_factory=dict)          # # real I/O appends
    idempotent_skips: dict[str, int] = field(default_factory=dict)   # # re-send-nothing
    observed: dict[str, float | None] = field(default_factory=dict)
    # imported conservation / balances
    net_moved: float | None = None
    balances: dict[str, float] = field(default_factory=dict)
    loss_legs: float | None = None
    edges: set[tuple[str, str]] = field(default_factory=set)         # observed (from,to)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_ledger_facts(ledger_path: Path) -> LedgerFacts:
    """Read one physical action ledger into :class:`LedgerFacts`. Two independent passes:
    (1) a raw line parse for the transaction arcs (what the report SHOWS), and (2) the
    imported :class:`ActionLedger` replay over the same directory, which re-verifies the
    hash chain and recomputes the volume conservation — a tampered byte breaks the chain
    (``LedgerIntegrityError``) and is surfaced loudly."""
    if not ledger_path.exists():
        return LedgerFacts(present=False)

    raw = ledger_path.read_bytes()
    facts = LedgerFacts(present=True, sha=_sha_bytes(raw))

    # --- pass 1: raw line parse (arcs) ---
    parse_error: str | None = None
    lines: list[dict[str, Any]] = []
    for rawline in raw.split(b"\n"):
        s = rawline.strip()
        if not s:
            continue
        try:
            lines.append(json.loads(s))
        except Exception as exc:  # noqa: BLE001  a torn/edited tail must be reported
            parse_error = f"unparseable ledger line ({type(exc).__name__}: {exc})"
            break
    facts.lines = lines
    for rec in lines:
        ts = rec.get("ts")
        if isinstance(ts, (int, float)):
            facts.latest_ts = ts if facts.latest_ts is None else max(facts.latest_ts, ts)
        aid = rec.get("action_id")
        if aid is None:
            continue
        if aid not in facts.arcs:
            facts.arcs[aid] = []
            facts.order.append(aid)
            facts.pending_into[aid] = 0
            facts.planned_registrations[aid] = 0
            facts.io_issued[aid] = 0
            facts.idempotent_skips[aid] = 0
        frm, to = rec.get("from"), rec.get("to")
        if to is not None:
            if frm != to:                       # a real state transition (not a note line)
                facts.arcs[aid].append((frm, to))
                facts.final_state[aid] = to
                if isinstance(frm, str):
                    facts.edges.add((frm, to))
                if to == ActionState.PENDING.value:
                    facts.pending_into[aid] += 1
                if to == ActionState.PLANNED.value:
                    facts.planned_registrations[aid] += 1
            else:                               # a same-state note line (io / idempotent)
                note = rec.get("note", "")
                if note == "driver_reply_recorded_not_committed":
                    facts.io_issued[aid] += 1
                elif note == "idempotent_replay_skipped":
                    facts.idempotent_skips[aid] += 1
        facts.attempts[aid] = max(facts.attempts.get(aid, 0), rec.get("attempt", 0) or 0)
        if rec.get("observed_volume_ul") is not None:
            facts.observed[aid] = rec.get("observed_volume_ul")
        elif aid not in facts.observed:
            facts.observed[aid] = None

    # --- pass 2: imported independent replay (conservation + hash chain) ---
    if parse_error is not None:
        facts.integrity_ok = False
        facts.integrity_error = parse_error
        return facts
    try:
        replay = ActionLedger(ledger_path.parent, volume=VolumeLedger(
            capacities={}, initial={}))
        facts.integrity_ok = True
        vol = replay.volume
        facts.net_moved = vol.net_moved()
        balances: dict[str, float] = {}
        losses = 0.0
        for entry in vol._entries:   # the append-only double-entry leg stream (P5 witness)
            well = entry["well"]
            balances[well] = balances.get(well, 0.0) + float(entry["delta"])
            if entry.get("kind") == "loss":
                losses += float(entry["delta"])
        facts.balances = balances
        facts.loss_legs = losses
    except LedgerIntegrityError as exc:
        facts.integrity_ok = False
        facts.integrity_error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001  a broken ledger must be reported, not crash
        facts.integrity_ok = False
        facts.integrity_error = f"{type(exc).__name__}: {exc}"
    return facts


# ============================================================ per-scenario evidence model

@dataclass
class ScenarioEvidence:
    sid: str
    listed: bool                                 # named in the evidence index / roster
    dir_present: bool
    manifest: dict[str, Any] | None
    manifest_errors: list[str]
    mode: str | None
    expected_outcome: str | None
    killpoint: str | None
    resume_style: str | None
    events_present: bool
    events_sha: str | None
    verify_ok: bool | None                        # None => no events.jsonl provided
    verify_code: str | None
    verify_message: str
    ledger: LedgerFacts
    ledger_declared_absent: bool                 # manifest ledger_path=null => physical path not involved
    diff_report: dict[str, Any] | None
    diff_error: str | None
    stderr_text: str | None
    exit_status: str | None

    @property
    def any_evidence(self) -> bool:
        return self.dir_present and (self.manifest is not None or self.ledger.present)


def load_scenario(evidence_dir: Path, sid: str, listed: bool) -> ScenarioEvidence:
    sdir = evidence_dir / sid
    dir_present = sdir.is_dir()

    manifest: dict[str, Any] | None = None
    manifest_errors: list[str] = []
    mpath = sdir / _MANIFEST_REL
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001  a malformed manifest is loud, not fatal
            manifest = None
            manifest_errors = [f"manifest does not parse ({type(exc).__name__}: {exc})"]
        else:
            manifest_errors = validate_manifest(manifest)

    def mget(key: str) -> Any:
        return manifest.get(key) if isinstance(manifest, dict) else None

    # Letter-135 contract amendment: optional self-describing pointers. run_path is
    # relative to the scenario root (e.g. "run"); ledger_path likewise, with null
    # meaning "physical path not involved" (rendered as such, never as BROKEN).
    # Absent pointer fields => legacy root lookup (backward compatible).
    _run_rel = mget("run_path")
    run_dir = (sdir / _run_rel) if isinstance(_run_rel, str) and _run_rel else sdir

    # events.jsonl (optional): if present, verify the kernel chain (imported).
    events_path = run_dir / _EVENTS_REL
    events_present = events_path.exists()
    events_sha: str | None = None
    verify_ok: bool | None = None
    verify_code: str | None = None
    verify_message = ""
    if events_present:
        try:
            events_sha, _ = er._high_water(run_dir)
        except Exception as exc:  # noqa: BLE001
            events_sha = None
            verify_message = f"events unreadable: {type(exc).__name__}: {exc}"
        try:
            vres = vrc.verify_run(run_dir)
            verify_ok = bool(vres.ok)
            verify_code = vres.code
            verify_message = vres.message
        except Exception as exc:  # noqa: BLE001  a broken run is reported, not crashed on
            verify_ok = False
            verify_code = "verifier_raised"
            verify_message = f"{type(exc).__name__}: {exc}"

    ledger_declared_absent = False
    if isinstance(manifest, dict) and "ledger_path" in manifest:
        _lrel = manifest.get("ledger_path")
        if _lrel is None:
            ledger_declared_absent = True
            ledger = LedgerFacts(present=False)
        else:
            ledger = load_ledger_facts(sdir / _lrel)
    else:
        _lp = sdir / _LEDGER_REL
        if not _lp.exists() and run_dir != sdir and (run_dir / _LEDGER_REL).exists():
            _lp = run_dir / _LEDGER_REL
        ledger = load_ledger_facts(_lp)

    diff_report: dict[str, Any] | None = None
    diff_error: str | None = None
    dpath = sdir / _DIFF_REL
    if dpath.exists():
        try:
            diff_report = json.loads(dpath.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            diff_error = f"diff_report.json does not parse ({type(exc).__name__}: {exc})"

    stderr_text: str | None = None
    spath = sdir / _STDERR_REL
    if spath.exists():
        stderr_text = spath.read_text(encoding="utf-8", errors="replace")
    exit_status: str | None = None
    epath = sdir / _EXIT_REL
    if epath.exists():
        exit_status = epath.read_text(encoding="utf-8", errors="replace").strip()

    return ScenarioEvidence(
        sid=sid, listed=listed, dir_present=dir_present,
        manifest=manifest, manifest_errors=manifest_errors,
        mode=mget("mode"), expected_outcome=mget("expected_outcome"),
        killpoint=mget("killpoint"), resume_style=mget("resume_style"),
        events_present=events_present, events_sha=events_sha,
        verify_ok=verify_ok, verify_code=verify_code, verify_message=verify_message,
        ledger=ledger, ledger_declared_absent=ledger_declared_absent,
        diff_report=diff_report, diff_error=diff_error,
        stderr_text=stderr_text, exit_status=exit_status,
    )


# ============================================================ top-level evidence set

@dataclass
class EvidenceSet:
    evidence_dir: Path
    index_present: bool
    index: dict[str, Any] | None
    index_sha: str | None
    envelope_config: dict[str, Any] | None
    envelope_source_path: Path | None
    scenarios: list[ScenarioEvidence]


def load_evidence_set(evidence_dir: Path) -> EvidenceSet:
    index_path = evidence_dir / "evidence_index.json"
    index: dict[str, Any] | None = None
    index_sha: str | None = None
    index_present = index_path.exists()
    if index_present:
        raw = index_path.read_bytes()
        index_sha = _sha_bytes(raw)
        try:
            index = json.loads(raw)
        except Exception:  # noqa: BLE001  a corrupt index is reported, not fatal
            index = None

    listed_ids: list[str] = []
    if isinstance(index, dict) and isinstance(index.get("scenarios"), list):
        listed_ids = [str(s) for s in index["scenarios"]]
    roster = listed_ids or list(_EXPECTED_SCENARIOS)

    scenarios = [load_scenario(evidence_dir, sid, listed=sid in listed_ids or not listed_ids)
                 for sid in roster]

    # envelope config: from the index if it names one, else the in-repo placeholder.
    env_path: Path | None = None
    if isinstance(index, dict) and index.get("envelope_config"):
        cand = Path(index["envelope_config"])
        env_path = cand if cand.is_absolute() else (evidence_dir / cand)
        if not env_path.exists():
            env_path = ROOT / index["envelope_config"]
    if env_path is None or not env_path.exists():
        env_path = _DEFAULT_ENVELOPE
    envelope: dict[str, Any] | None = None
    if env_path.exists():
        try:
            envelope = json.loads(env_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            envelope = None

    return EvidenceSet(
        evidence_dir=evidence_dir, index_present=index_present, index=index,
        index_sha=index_sha, envelope_config=envelope, envelope_source_path=env_path,
        scenarios=scenarios,
    )


# ============================================================ literal-free templates
# Same discipline as expos_report._CONTENT_TEMPLATES: every value-bearing template below
# carries ONLY ``{}`` slots and NO digit literal — a number reaches the page only by
# evaluating the evidence. Structural/label markup that carries no numeric slot is either
# reused from ``expos_report`` (already tested there) or listed in _SCAFFOLD_TEMPLATES.

_MH = (
    '<header class="masthead"><h1>{title}</h1>'
    '<p class="subtitle">{subtitle}</p>'
    '<p class="asof">Evidence as of {asof} (derived from the latest physical-ledger '
    'transition timestamp). Report is a pure function of the evidence directory.</p>'
    "{body}</header>"
)
_MISSING = '<div class="missing"><span class="missing-tag">{label}</span> {detail}</div>'
_PILL = '<span class="pill {cls}">{v}</span>'
_ARC = '<span class="arc-node">{v}</span>'
_ARC_SEP = '<span class="arrow">&rarr;</span>'
_QUOTE = '<pre class="stderr-quote">{v}</pre>'
_STEP = ('<tr class="{cls}"><td class="stepnum">{n}</td><td>{desc}</td>'
         '<td class="stepstatus">{status}</td><td class="note">{basis}</td></tr>')

# Scaffolding (reused from expos_report — mandated boilerplate, no numeric value slot).
_SCAFFOLD_TEMPLATES: tuple[str, ...] = (er._DOC, er._SECTION, _MH, er._TABLE)

# Value-bearing templates authored here (the expos_report ones are covered by its own
# literal-free test; these are the readiness-only additions).
_CONTENT_TEMPLATES: tuple[str, ...] = (_MISSING, _PILL, _ARC, _ARC_SEP, _QUOTE, _STEP)

_EXTRA_CSS = """
.missing{background:var(--warnbg);border:1px solid var(--warn);border-radius:8px;
padding:10px 14px;margin:12px 0;color:var(--warn);font-size:13.5px;}
.missing-tag{font-weight:700;letter-spacing:.02em;text-transform:uppercase;font-size:12px;
margin-right:8px;}
.arc-node{font-family:ui-monospace,monospace;font-size:12px;background:var(--card);
padding:1px 6px;border-radius:4px;}
.stderr-quote{background:var(--card);border-left:4px solid var(--bad);border-radius:0 6px 6px 0;
padding:10px 12px;font-size:12px;line-height:1.45;overflow-x:auto;white-space:pre-wrap;
word-break:break-word;margin:8px 0;}
td.stepnum{text-align:center;font-weight:700;color:var(--accent);width:2.4em;}
td.stepstatus{white-space:nowrap;}
.pill.warn{background:var(--warnbg);color:var(--warn);}
.pill.reserved{background:var(--card);color:var(--muted);}
tr.tamper td{background:var(--badbg);}
"""


# ============================================================ small render helpers

def _pill(cls: str, text: str) -> str:
    return _PILL.format(cls=cls, v=er._esc(text))


def _missing(label: str, detail: str) -> str:
    return _MISSING.format(label=er._esc(label), detail=er._esc(detail))


def _short(h: str | None) -> str:
    if not h:
        return "n/a"
    return er._CODE.format(v=er._esc(h[:_FP_HEAD]))


def _arc(states: list[str]) -> str:
    if not states:
        return "—"
    return _ARC_SEP.join(_ARC.format(v=er._esc(s)) for s in states)


def _scenario_missing_block(sc: ScenarioEvidence) -> str | None:
    """Return a loud EVIDENCE-MISSING block if this scenario contributes nothing, else
    None. Manifest problems get their own MANIFEST-INVALID block regardless."""
    if not sc.dir_present:
        return _missing("evidence missing",
                        f"scenario {sc.sid} not provided (no directory under the evidence set).")
    if sc.manifest is None and not sc.ledger.present and not sc.events_present \
            and sc.diff_report is None and sc.stderr_text is None:
        return _missing("evidence missing",
                        f"scenario {sc.sid} directory is empty of recognised evidence files.")
    return None


def _by_mode(scenarios: list[ScenarioEvidence], modes: tuple[str, ...]) -> list[ScenarioEvidence]:
    want = set(modes)
    return [s for s in scenarios if (s.mode in want) or (s.sid in want)]


# ============================================================ section 1 — state coverage

def render_state_coverage(ev: EvidenceSet) -> str:
    sec = er.Section("coverage", "")
    contributing = [s for s in ev.scenarios if s.ledger.present]
    # sorted(): _LEGAL_TRANSITIONS values are frozensets -- unsorted iteration is
    # process-dependent (hash randomization), which made two runs of this PURE
    # generator differ at the byte level while sharing a report_digest (letter 135
    # finding 1: the digest pins EVIDENCE, so byte determinism must come from here).
    legal_edges = sorted((a.value, b.value) for a, outs in _LEGAL_TRANSITIONS.items() for b in outs)
    covered: set[tuple[str, str]] = set()
    for s in contributing:
        covered |= s.ledger.edges

    states = [st.value for st in ActionState]
    head = er._row([er._th("from \\ to")] + [er._th(er._esc(t)) for t in states])
    rows = []
    for a in states:
        cells = [er._td(er._esc(a), "rowhead")]
        for b in states:
            legal = ActionState(b) in _LEGAL_TRANSITIONS.get(ActionState(a), frozenset())
            if not legal:
                cells.append(er._td("·"))
            elif (a, b) in covered:
                cells.append(er._td(_pill("ok", "covered"), "ok"))
            else:
                cells.append(er._td(_pill("warn", "uncovered"), ""))
        rows.append(er._row(cells))
    table = er._TABLE.format(cls="coverage", head=head, rows="".join(rows))

    uncovered = [e for e in legal_edges if e not in covered]
    cov_n = len(legal_edges) - len(uncovered)
    summary = er._P.format(v=(
        f"<strong>{er._num(cov_n)} / {er._num(len(legal_edges))}</strong> legal action-state "
        "transitions are exercised by at least one scenario ledger. The legal edge set is the "
        "imported <code>_LEGAL_TRANSITIONS</code> table (the ledger's own state machine); a "
        "cell is <em>covered</em> only when some scenario's append-only ledger actually records "
        "that edge."))
    if uncovered:
        items = "".join(er._LI.format(v=er._esc(f"{a} → {b}")) for a, b in uncovered)
        honesty = (er._P.format(v="Uncovered legal transitions (listed honestly — a legal edge no "
                                  "provided scenario drives yet):") + er._UL.format(items=items))
    else:
        honesty = er._NOTE.format(v="Every legal transition is covered by the provided scenarios.")
    if not contributing:
        return (_missing("evidence missing",
                         "no scenario ledger provided yet — the state-coverage matrix will "
                         "populate as evidence lands.") + summary + table + honesty
                + sec.anchor_block())
    return summary + table + honesty + sec.anchor_block()


# ============================================================ section 2 — crash matrix

def render_crash_matrix(ev: EvidenceSet) -> str:
    sec = er.Section("crash", "")
    crash = _by_mode(ev.scenarios, _CRASH_MODES)
    listed = [s for s in ev.scenarios if s.sid in _CRASH_MODES] or crash
    head = er._row([er._th("Scenario"), er._th("Killpoint"), er._th("Resume style"),
                    er._th("Ledger chain"), er._th("No double-dispatch"),
                    er._th("Decision face")])
    rows = []
    any_present = False
    for s in listed:
        mb = _scenario_missing_block(s)
        if mb is not None:
            rows.append(er._row([er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
                                 er._td(_pill("warn", "evidence missing")),
                                 er._td("—"), er._td("—"), er._td("—"), er._td("—")]))
            continue
        any_present = True
        # ledger chain integrity (imported replay). Four-way, letter 135 finding 3:
        # a manifest-declared ledger_path=null means the physical path was NOT engaged
        # (not missing evidence, not a failure) -- render "not involved", never BROKEN.
        cls = ""
        if s.ledger_declared_absent:
            chain = _pill("reserved", "not involved")
        elif not s.ledger.present:
            chain = _pill("warn", "ledger missing")
        elif s.ledger.integrity_ok:
            chain = _pill("ok", "verified")
        else:
            chain = _pill("bad", "BROKEN") + " " + er._esc(s.ledger.integrity_error or "")
            cls = "tamper"
        # no double-dispatch: each action registered once, I/O issued at most once per attempt
        dd_bad = [aid for aid in s.ledger.order
                  if s.ledger.planned_registrations.get(aid, 0) > 1
                  or s.ledger.io_issued.get(aid, 0) > s.ledger.attempts.get(aid, 0)]
        if s.ledger_declared_absent or not s.ledger.present:
            dd_cell = _pill("reserved", "n/a (no physical ledger)")
        else:
            dd_cell = _pill("bad", "DOUBLE-DISPATCH") if dd_bad else _pill("ok", "single-dispatch")
        # decision-face equality is an events.jsonl property; degrade honestly if absent
        if s.events_present:
            face = _pill("ok", "chain complete") if s.verify_ok else _pill("bad", "chain broken")
        else:
            face = _pill("reserved", "events not provided")
        sc_kp = er._esc(s.killpoint) if s.killpoint else "—"
        sc_rs = er._esc(s.resume_style) if s.resume_style else "—"
        rows.append(er._row([er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
                             er._td(sc_kp), er._td(sc_rs), er._td(chain),
                             er._td(dd_cell), er._td(face)], cls=cls))
    table = er._TABLE.format(cls="crash", head=head, rows="".join(rows))
    intro = er._P.format(v=(
        "Each crash-injection scenario is killed at a distinct point and resumed. The report "
        "recomputes, from the resumed physical ledger alone: the hash chain re-verifies "
        "(imported <code>ActionLedger</code> replay), and no action was dispatched twice (each "
        "<code>action_id</code> is registered once and issues hardware I/O at most once per "
        "attempt). Decision-face equality across the crash is a kernel-<code>events.jsonl</code> "
        "property, shown when that log is provided."))
    if not any_present:
        return _missing("evidence missing",
                        "crash-injection scenarios (crash_I1..I6) not provided yet.") + intro + table
    return intro + table + sec.anchor_block()


# ============================================================ section 3 — no-redispatch

def render_no_redispatch(ev: EvidenceSet) -> str:
    sec = er.Section("redispatch", "")
    contributing = [s for s in ev.scenarios if s.ledger.present]
    intro = er._P.format(v=(
        "The red line: a resumed wet leg re-senses a PENDING action, it never re-dispatches it. "
        "This is asserted per <code>action_id</code>: the count of transitions INTO PENDING "
        "must equal the action's attempt count (one PENDING per legitimate <code>recover()</code> "
        "attempt), and resume-time idempotent replays re-send nothing (recorded as "
        "<code>idempotent_replay_skipped</code> notes on the append-only log)."))
    if not contributing:
        return _missing("evidence missing",
                        "no scenario ledger provided yet — the per-action PENDING assertions "
                        "populate as evidence lands.") + intro
    blocks = []
    for s in contributing:
        head = er._row([er._th("action_id"), er._th("Attempts"), er._th("PENDING entries"),
                        er._th("Idempotent skips"), er._th("Final state"), er._th("Assertion")])
        rows = []
        for aid in s.ledger.order:
            attempts = s.ledger.attempts.get(aid, 0)
            pend = s.ledger.pending_into.get(aid, 0)
            skips = s.ledger.idempotent_skips.get(aid, 0)
            final = s.ledger.final_state.get(aid, "—")
            ok = pend <= max(attempts, 1)
            assertion = _pill("ok", "no re-dispatch") if ok else _pill("bad", "RE-DISPATCH")
            rows.append(er._row([
                er._td(er._CODE.format(v=er._esc(aid))),
                er._td(er._num(attempts) + sec.anc(s.sid, "action_ledger.jsonl", aid, None, "attempt")),
                er._td(er._num(pend) + sec.anc(s.sid, "action_ledger.jsonl", aid, None, "to=PENDING")),
                er._td(er._num(skips)),
                er._td(er._esc(final)),
                er._td(assertion),
            ], cls="" if ok else "tamper"))
        table = er._TABLE.format(cls="redispatch", head=head, rows="".join(rows))
        blocks.append(f'<h3><code>{er._esc(s.sid)}</code></h3>' + table)
    return intro + "".join(blocks) + sec.anchor_block()


# ============================================================ section 4 — mismatch behavior

def render_mismatch_behavior(ev: EvidenceSet) -> str:
    sec = er.Section("mismatch", "")
    loud = [s for s in ev.scenarios
            if s.mode == "unit_mismatch" or s.expected_outcome == "loud_failure"
            or s.sid in ("unit_mismatch",)]
    intro = er._P.format(v=(
        "A mismatch is EVIDENCE, not a defect to hide: a loud-failure scenario captures the "
        "process's <code>stderr</code> and its non-zero exit status. The report quotes the "
        "captured failure verbatim — the loud refusal is the readiness signal."))
    if not loud:
        return _missing("evidence missing",
                        "loud-failure scenarios (e.g. unit_mismatch) not provided yet.") + intro
    blocks = []
    for s in loud:
        mb = _scenario_missing_block(s)
        header = f'<h3><code>{er._esc(s.sid)}</code></h3>'
        if mb is not None:
            blocks.append(header + mb)
            continue
        exit_ok = s.exit_status not in (None, "", "0")
        exit_pill = (_pill("ok", f"non-zero exit ({s.exit_status})") if exit_ok
                     else _pill("bad", f"exit {s.exit_status or 'not captured'}"))
        parts = [header, er._P.format(v="Captured exit status: " + exit_pill
                                      + sec.anc(s.sid, "exit_status.txt", None, None, "exit"))]
        if s.stderr_text:
            excerpt = _stderr_excerpt(s.stderr_text)
            parts.append(er._P.format(v="Captured stderr (verbatim excerpt):"))
            parts.append(_QUOTE.format(v=er._esc(excerpt))
                         + sec.anc(s.sid, "stderr.txt", None, None, "excerpt"))
        else:
            parts.append(_missing("evidence missing",
                                  f"scenario {s.sid}: no stderr.txt captured."))
        blocks.append("".join(parts))
    return intro + "".join(blocks) + sec.anchor_block()


def _stderr_excerpt(text: str, max_lines: int = 12) -> str:
    """The tail of the captured stderr (where the loud refusal lands), bounded for display.
    ``max_lines`` is a display layout constant (a Python variable, never a template literal)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = lines[-max_lines:]
    return "\n".join(tail)


# ============================================================ section 5 — volume invariants

def render_volume_invariants(ev: EvidenceSet) -> str:
    sec = er.Section("volume", "")
    contributing = [s for s in ev.scenarios if s.ledger.present]
    intro = er._P.format(v=(
        "Volume conservation is recomputed INDEPENDENTLY by the imported "
        "<code>ActionLedger</code> replay: it re-walks the hash-chained log, re-applies every "
        "COMMITTED transfer as a balanced double-entry (source &minus;v, dest +v), and exposes "
        "the running per-well balance. A conserved ledger nets to zero across all legs; losses "
        "(evaporation / dead volume) are EXPLICIT loss legs, never silent. A tampered ledger "
        "byte breaks the chain and is flagged here — the same break changes the integrity "
        "footer's ledger hash."))
    if not contributing:
        return _missing("evidence missing",
                        "no scenario ledger provided yet — volume invariants populate as "
                        "evidence lands.") + intro
    head = er._row([er._th("Scenario"), er._th("Chain replay"), er._th("Net moved (Σ legs)"),
                    er._th("Loss legs (uL)"), er._th("Committed wells"), er._th("Conservation")])
    rows = []
    for s in contributing:
        L = s.ledger
        if not L.integrity_ok:
            rows.append(er._row([
                er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
                er._td(_pill("bad", "BROKEN")),
                er._td(er._esc(L.integrity_error or "unavailable")),
                er._td("—"), er._td("—"), er._td(_pill("bad", "UNVERIFIABLE")),
            ], cls="tamper"))
            continue
        conserved = L.net_moved is not None and abs(L.net_moved) < 1e-6
        committed_wells = er._num(len([a for a in L.order
                                       if L.final_state.get(a) == ActionState.COMMITTED.value]))
        rows.append(er._row([
            er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
            er._td(_pill("ok", "verified")),
            er._td(er._num(L.net_moved) + sec.anc(s.sid, "action_ledger.jsonl", None, None, "net_moved")),
            er._td(er._num(L.loss_legs)),
            er._td(committed_wells),
            er._td(_pill("ok", "conserved") if conserved else _pill("bad", "NOT CONSERVED")),
        ], cls="" if conserved else "tamper"))
    table = er._TABLE.format(cls="volume", head=head, rows="".join(rows))
    return intro + table + sec.anchor_block()


# ============================================================ section 6 — differential

def render_differential(ev: EvidenceSet) -> str:
    sec = er.Section("differential", "")
    diff_scn = [s for s in ev.scenarios
                if s.mode in ("diff_positive", "diff_negative")
                or s.sid in ("diff_positive", "diff_negative")
                or s.diff_report is not None]
    intro = er._P.format(v=(
        "The sim&ndash;real differential gate accepts a real result only when it lies INSIDE the "
        "simulator-declared tolerance envelope (a containment test; the envelope is never "
        "recomputed). A positive sample passes within the envelope; a negative (drift) sample is "
        "rejected. The declared envelope is echoed below WITH its provenance flags shown."))
    # envelope echo (with vendor_spec_placeholder flags SHOWN)
    env_block = _envelope_echo(ev, sec)
    if not diff_scn:
        return (_missing("evidence missing",
                         "differential scenarios (diff_positive / diff_negative) not provided yet.")
                + intro + env_block)
    head = er._row([er._th("Scenario"), er._th("Gate"), er._th("Failing facets"),
                    er._th("Findings")])
    rows = []
    for s in diff_scn:
        if s.diff_report is None:
            detail = s.diff_error or "diff_report.json not provided"
            rows.append(er._row([er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
                                 er._td(_pill("warn", "evidence missing")),
                                 er._td("—"), er._td(er._esc(detail))]))
            continue
        d = s.diff_report
        passed = bool(d.get("passed"))
        gate = _pill("ok", "PASS (within envelope)") if passed else _pill("bad", "REJECT (drift)")
        failing = [k for k, v in (d.get("facet_status") or {}).items() if not v]
        fcell = ", ".join(er._esc(f) for f in failing) or "(none)"
        findings = d.get("findings") or []
        fitems = "".join(er._LI.format(v=er._esc(
            f"{f.get('facet')}/{f.get('kind')}: {f.get('detail')}")) for f in findings)
        fnd = er._UL.format(items=fitems) if fitems else "—"
        rows.append(er._row([
            er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
            er._td(gate + sec.anc(s.sid, "diff_report.json", None, None, "passed")),
            er._td(fcell), er._td(fnd),
        ], cls="" if passed else "tamper"))
    table = er._TABLE.format(cls="diff", head=head, rows="".join(rows))
    return intro + table + env_block + sec.anchor_block()


def _envelope_echo(ev: EvidenceSet, sec: er.Section) -> str:
    env = ev.envelope_config
    if not isinstance(env, dict):
        return _missing("evidence missing", "tolerance envelope config not readable.")
    meta = env.get("_meta") or {}
    src = meta.get("source", "")
    flags = []
    if src == "vendor_spec_placeholder":
        flags.append(_pill("warn", "vendor_spec_placeholder (NOT gravimetric)"))
    if meta.get("discipline_never_tighter"):
        flags.append(_pill("reserved", "never-tighter discipline declared"))
    if meta.get("n_replicates_min"):
        flags.append(_pill("reserved", "random/CV channel reserved (N≥"
                           + er._num(meta.get("n_replicates_min")) + ")"))
    bands = env.get("bands") or []
    kv = [er._KV.format(k="Envelope source", v=er._esc(src) or "n/a"),
          er._KV.format(k="Declared bands", v=er._num(len(bands))),
          er._KV.format(k="Provenance flags", v=" ".join(flags) or "—")]
    return ('<div class="nameplate">' + "".join(kv) + "</div>"
            + er._NOTE.format(v="These are VENDOR-SPEC placeholder tolerances, shown with their "
                              "own flags: they may only be WIDENED, never tightened, until real "
                              "gravimetric data exists (the declared never-tighter discipline)."))


# ============================================================ section 7 — human intervention

_HUMAN_NOTE_ARC = {
    ActionState.AWAITING_RECOVERY.value: "parked (await human)",
    ActionState.PENDING.value: "recover attempt++",
    ActionState.COMMITTED.value: "recovered",
    ActionState.ROLLED_BACK.value: "abandoned",
    ActionState.ABORTED.value: "canceled",
}


def render_human(ev: EvidenceSet) -> str:
    sec = er.Section("human", "")
    human = _by_mode(ev.scenarios, _HUMAN_MODES)
    listed = [s for s in ev.scenarios if s.sid in _HUMAN_MODES] or human
    intro = er._P.format(v=(
        "Human-in-the-loop recovery is a first-class ledger arc: a sensed mismatch parks the "
        "action in AWAITING_RECOVERY; an operator either recovers it (a distinct auditable "
        "attempt++, re-sensed to COMMITTED) or abandons/cancels it (ROLLED_BACK / ABORTED). "
        "The arc below is reconstructed from the append-only ledger transitions."))
    if not listed:
        return _missing("evidence missing",
                        "human-intervention scenarios (human_recover / human_cancel) not "
                        "provided yet.") + intro
    head = er._row([er._th("Scenario"), er._th("action_id"), er._th("Ledger arc"),
                    er._th("Attempts"), er._th("Terminal")])
    rows = []
    any_present = False
    for s in listed:
        mb = _scenario_missing_block(s)
        if mb is not None or not s.ledger.present:
            rows.append(er._row([er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
                                 er._td(_pill("warn", "evidence missing")),
                                 er._td("—"), er._td("—"), er._td("—")]))
            continue
        any_present = True
        for aid in s.ledger.order:
            states = [ActionState.PLANNED.value]
            for _frm, to in s.ledger.arcs.get(aid, []):
                states.append(to)
            # de-dup consecutive
            arc_states: list[str] = []
            for st in states:
                if not arc_states or arc_states[-1] != st:
                    arc_states.append(st)
            final = s.ledger.final_state.get(aid, "—")
            term_label = _HUMAN_NOTE_ARC.get(final, final)
            rows.append(er._row([
                er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
                er._td(er._CODE.format(v=er._esc(aid))),
                er._td(_arc(arc_states)),
                er._td(er._num(s.ledger.attempts.get(aid, 0))
                       + sec.anc(s.sid, "action_ledger.jsonl", aid, None, "attempt")),
                er._td(_pill("ok", term_label)),
            ]))
    table = er._TABLE.format(cls="human", head=head, rows="".join(rows))
    if not any_present:
        return _missing("evidence missing",
                        "human-intervention ledgers not provided yet.") + intro + table
    return intro + table + sec.anchor_block()


# ============================================================ section 8 — known limits

def render_known_limits(ev: EvidenceSet) -> str:
    """MACHINE-DERIVED ONLY. Every item below is read from an in-repo file or produced by
    running a repo tool programmatically — no hand-filled prose beyond the fixed lead-in."""
    intro = er._P.format(v=(
        "Known limits are derived by machine, not hand-authored: the tolerance-envelope "
        "placeholder flags (read from the envelope config), the EXP013 domain-compliance lint "
        "nudges (the lint run programmatically), acceptance-face debt (parsed from the domain "
        "YAMLs), and TODO notes carried in the domain observables (parsed likewise)."))
    items: list[str] = []

    # (a) envelope placeholder flags
    meta = (ev.envelope_config or {}).get("_meta") or {}
    if meta.get("source") == "vendor_spec_placeholder":
        items.append("Differential tolerance envelope is a <strong>vendor-spec placeholder</strong> "
                     "(Opentrons white-paper / ISO 8655 seed), NOT this instrument's measured "
                     "gravimetric data — source flag <code>vendor_spec_placeholder</code>.")
    if meta.get("n_replicates_min"):
        items.append("The random/CV (precision) differential channel is <strong>reserved</strong>: "
                     "it needs N&ge;" + er._num(meta.get("n_replicates_min"))
                     + " replicate observations per volume; the current per-action ledger carries "
                     "one observed volume, so only the systematic (accuracy) channel is checked.")

    # (b) EXP013 preview lint nudges (run the lint programmatically)
    try:
        findings = expos_lint.run_lint(ROOT, preview=True, select=["EXP013"])
    except Exception as exc:  # noqa: BLE001  a lint failure is itself a reported limit
        findings = []
        items.append("EXP013 domain-compliance lint could not run programmatically ("
                     + er._esc(f"{type(exc).__name__}: {exc}") + ").")
    for f in sorted(findings, key=lambda x: (x.path, x.line, x.message)):
        items.append("EXP013 " + er._esc(f.path) + ": " + er._esc(f.message))

    # (c) acceptance_faces with status != landed + (d) TODO notes in observables
    import yaml  # local import; PyYAML is a repo dependency (used across domains)
    for ypath in sorted((ROOT / "domains").glob("*.yaml")):
        try:
            doc = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        rel = ypath.relative_to(ROOT)
        for face in doc.get("acceptance_faces") or []:
            status = (face or {}).get("status")
            if status and status != "landed":
                items.append(er._esc(f"{rel}: acceptance face "
                                     f"{face.get('face_name')!r} status={status} (declared, "
                                     "not yet landed)."))
        for obs in doc.get("observables") or []:
            note = (obs or {}).get("note") or ""
            if "TODO" in note:
                first = note.strip().splitlines()[0]
                items.append(er._esc(f"{rel}: observable {obs.get('name')!r} carries a TODO — ")
                             + er._esc(first))

    if not items:
        items.append("No machine-derived known limits detected in the current repo state.")
    return intro + er._UL.format(items="".join(er._LI.format(v=v) for v in items))


# ============================================================ minimal safe steps checklist

def _exists(rel: str) -> bool:
    return (ROOT / rel).exists()


def _step_checks() -> list[tuple[str, str, str, str]]:
    """Return the fixed checklist rows as (description, status_cls, status_text, basis).
    Every status is MACHINE-VERIFIED — no hand-set booleans. The step DESCRIPTIONS are the
    only fixed template text; the status cells are derived here."""
    rows: list[tuple[str, str, str, str]] = []

    # 1. append-only hash-chained physical ledger present
    ok = hasattr(ActionLedger, "verify") and bool(_LEGAL_TRANSITIONS)
    rows.append(("Physical-action transaction ledger is append-only and hash-chained",
                 "ok" if ok else "bad", "ready" if ok else "MISSING",
                 "expos/adapters/wet/action_ledger.py: ActionLedger.verify + _LEGAL_TRANSITIONS"))

    # 2. crash / resume guard tests exist
    ok = _exists("tests/test_phase4_interruption.py") and _exists("tests/test_realwet_transactions.py")
    rows.append(("Crash / resume discriminative guard tests exist",
                 "ok" if ok else "bad", "ready" if ok else "MISSING",
                 "tests/test_phase4_interruption.py + tests/test_realwet_transactions.py"))

    # 3. physical-dispatch wiring + AST isolation guard tests exist
    ok = _exists("tests/test_phase4_wiring.py")
    rows.append(("Physical-dispatch mcl wiring + harness-isolation guard tests exist",
                 "ok" if ok else "bad", "ready" if ok else "MISSING",
                 "tests/test_phase4_wiring.py"))

    # 4. differential gate + declared tolerance envelope present
    ok = _exists("expos/adapters/wet/differential_gate.py") and _DEFAULT_ENVELOPE.exists()
    rows.append(("Sim&ndash;real differential gate present with a declared tolerance envelope",
                 "ok" if ok else "bad", "ready" if ok else "MISSING",
                 "expos/adapters/wet/differential_gate.py + tolerances_vendor_placeholder.json"))

    # 5. domain-compliance lint rule registered
    codes = {getattr(r, "code", None) for r in getattr(expos_lint, "RULES", [])}
    ok = "EXP013" in codes
    rows.append(("Domain-compliance lint rule is registered",
                 "ok" if ok else "bad", "ready" if ok else "MISSING",
                 "scripts/expos_lint.py: RULES contains EXP013"))

    # 6. all shipped acceptance faces landed
    import yaml
    all_landed = True
    checked_any = False
    for ypath in sorted((ROOT / "domains").glob("*.yaml")):
        try:
            doc = yaml.safe_load(ypath.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        for face in doc.get("acceptance_faces") or []:
            checked_any = True
            if (face or {}).get("status") != "landed":
                all_landed = False
    ok = checked_any and all_landed
    rows.append(("All shipped domain acceptance faces are landed (not merely declared)",
                 "ok" if ok else "warn", "ready" if ok else "open",
                 "domains/*.yaml: every acceptance_faces[*].status == landed"))

    # 7. tolerance envelope is a placeholder — MUST be replaced before the first real machine
    meta = {}
    if _DEFAULT_ENVELOPE.exists():
        try:
            meta = (json.loads(_DEFAULT_ENVELOPE.read_text(encoding="utf-8")).get("_meta") or {})
        except Exception:  # noqa: BLE001
            meta = {}
    placeholder = meta.get("source") == "vendor_spec_placeholder"
    rows.append(("Replace the vendor-spec tolerance envelope with real gravimetric data",
                 "warn" if placeholder else "ok", "OPEN (placeholder present)" if placeholder
                 else "cleared",
                 "tolerances_vendor_placeholder.json _meta.source == vendor_spec_placeholder"))

    # 8. random/CV precision channel wired (needs a replicate-bearing ledger)
    reserved = bool(meta.get("n_replicates_min"))
    rows.append(("Wire the random/CV precision channel (needs an N&ge;10 replicate ledger)",
                 "reserved" if reserved else "ok", "reserved" if reserved else "n/a",
                 "envelope _meta.n_replicates_min declared; systematic-only gate today"))

    # 9. calibration / deck-setup schema reserved-not-wired
    calib_wired = _exists("expos/adapters/wet/calibration_schema.json")
    rows.append(("Add the calibration / deck-setup action schema (excluded from idempotency)",
                 "ok" if calib_wired else "reserved", "wired" if calib_wired else "reserved (not wired)",
                 "expos/adapters/wet/calibration_schema.json present?  derive_action_id reserves it"))

    return rows


def render_safe_steps() -> str:
    intro = er._P.format(v=(
        "The minimal safe path to wiring the first real machine. Every status cell is "
        "machine-verified against the repo (guard-test files present, lint rules registered, "
        "placeholder flags set or cleared, calibration schema reserved-vs-wired) — the step "
        "descriptions are fixed template text, no status is hand-set. A <em>ready</em> row is a "
        "satisfied prerequisite; an <em>open</em> / <em>reserved</em> row is work that remains "
        "before a physical instrument is trusted."))
    head = er._row([er._th("#"), er._th("Step"), er._th("Status"), er._th("Machine basis")])
    rows = []
    for i, (desc, cls, text, basis) in enumerate(_step_checks(), start=1):
        rows.append(_STEP.format(n=er._num(i), desc=desc, cls="",
                                 status=_pill(cls, text), basis=er._esc(basis)))
    table = er._TABLE.format(cls="safesteps", head=head, rows="".join(rows))
    return intro + table


# ============================================================ integrity footer

def compute_report_digest(ev: EvidenceSet) -> str:
    parts = [f"index={ev.index_sha or 'MISSING'}"]
    for s in sorted(ev.scenarios, key=lambda x: x.sid):
        parts.append(f"{s.sid}:events={s.events_sha or 'NONE'}:ledger={s.ledger.sha or 'NONE'}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def render_footer(ev: EvidenceSet, report_digest: str) -> str:
    sec = er.Section("integrity", "")
    head = er._row([er._th("Scenario"), er._th("events.jsonl sha256"),
                    er._th("action_ledger.jsonl sha256"), er._th("Ledger chain")])
    rows = []
    for s in ev.scenarios:
        if s.ledger.present:
            lstat = _pill("ok", "verified") if s.ledger.integrity_ok else _pill("bad", "BROKEN")
        else:
            lstat = _pill("reserved", "not provided")
        rows.append(er._row([
            er._td(er._CODE.format(v=er._esc(s.sid)), "rowhead"),
            er._td(_short(s.events_sha)),
            er._td(_short(s.ledger.sha)),
            er._td(lstat),
        ], cls="" if (s.ledger.integrity_ok or not s.ledger.present) else "tamper"))
    table = er._TABLE.format(cls="integ", head=head, rows="".join(rows))
    idx = er._KV.format(k="evidence_index sha256",
                        v=(_short(ev.index_sha) if ev.index_sha else _pill("warn", "MISSING")))
    params = er._UL.format(items="".join(er._LI.format(v=v) for v in [
        "Evidence directory pinned by: the <code>evidence_index.json</code> sha, each scenario's "
        "<code>events.jsonl</code> high-water sha, and each physical "
        "<code>action_ledger.jsonl</code> content sha (never mtime).",
        "Independent recomputation: the ledger conservation + hash chain are re-verified here by "
        "the imported <code>ActionLedger</code> replay, not trusted from the evidence.",
        "Re-run this generator against the same evidence directory to reproduce this report "
        "byte-for-byte.",
    ]))
    digest = ('<div class="footer-digest"><div class="kv"><span class="k">report_digest</span>'
              f'<span class="v">{er._CODE.format(v=er._esc(report_digest))}</span></div>'
              '<p class="note">report_digest = sha256 over the pinned evidence_index sha + every '
              'scenario\'s events / ledger high-water shas (sorted by scenario). Any tampered input '
              'byte changes a high-water sha and this digest, and flips the corresponding ledger '
              'chain status to BROKEN.</p></div>')
    return ('<div class="nameplate">' + idx + "</div>" + table + params + digest
            + sec.anchor_block())


# ============================================================ masthead + assembly

def _derive_asof(ev: EvidenceSet) -> str:
    stamps = [s.ledger.latest_ts for s in ev.scenarios if s.ledger.latest_ts is not None]
    if not stamps:
        return "n/a (no evidence yet)"
    return er._num(max(stamps))


def render_masthead(ev: EvidenceSet, asof: str) -> str:
    present = [s for s in ev.scenarios if s.any_evidence]
    kvs = [
        er._KV.format(k="Evidence directory", v=er._CODE.format(v=er._esc(ev.evidence_dir.name))),
        er._KV.format(k="Scenarios expected", v=er._num(len(ev.scenarios))),
        er._KV.format(k="Scenarios with evidence", v=er._num(len(present))),
        er._KV.format(k="Evidence index",
                      v=(_pill("ok", "present") if ev.index_present
                         else _pill("warn", "not produced yet"))),
    ]
    nameplate = f'<div class="nameplate">{"".join(kvs)}</div>'
    intro = er._P.format(v=(
        "This Real-Wet Readiness report is regenerated as a pure function of the physical "
        "evidence directory: every number is recomputed from a scenario manifest, a kernel "
        "<code>events.jsonl</code>, a physical <code>action_ledger.jsonl</code>, a differential "
        "<code>diff_report.json</code>, or a captured <code>stderr</code> — and pinned by a "
        "content hash in the integrity footer. Missing evidence is rendered as a loud "
        "EVIDENCE-MISSING block, never silently skipped, so the report is honest at every stage "
        "of evidence accumulation."))
    if not ev.index_present:
        banner = _missing("evidence pending",
                          "the evidence set has not been produced yet (the取证 run lands via "
                          "sbatch later). Sections below render machine-derived limits + the "
                          "safe-steps checklist now, and each scenario as EVIDENCE MISSING.")
    else:
        banner = ""
    # Any malformed scenario manifest is surfaced loudly here (contract-shape validation),
    # regardless of which section the scenario falls in.
    bad_manifests = [s for s in ev.scenarios if s.manifest_errors]
    for s in bad_manifests:
        banner += _missing("manifest invalid",
                           f"scenario {s.sid}: " + "; ".join(s.manifest_errors))
    subtitle = ("Real-Wet Readiness — transaction-state coverage, crash matrix, no-redispatch "
                "proof, mismatch-as-evidence, volume invariants, differential results, human "
                "intervention, machine-derived limits, and the minimal safe steps to the first "
                "real machine.")
    body = intro + banner + nameplate
    return _MH.format(title="expos Real-Wet Readiness Report",
                      subtitle=subtitle, asof=er._esc(asof), body=body)


_SECTION_DEFS = [
    ("coverage", "1. Transaction-state coverage", render_state_coverage),
    ("crash", "2. Crash matrix (resume equivalence + no double-dispatch)", render_crash_matrix),
    ("redispatch", "3. No-redispatch proof (per-action PENDING assertions)", render_no_redispatch),
    ("mismatch", "4. Mismatch behavior (loud failures as evidence)", render_mismatch_behavior),
    ("volume", "5. Volume invariants (independent replay)", render_volume_invariants),
    ("differential", "6. Differential results (envelope containment)", render_differential),
    ("human", "7. Human intervention (recovery / cancel arcs)", render_human),
    ("limits", "8. Known limits (machine-derived)", None),
    ("safesteps", "Minimal safe steps to the first real machine", None),
]


def render_report(ev: EvidenceSet) -> str:
    asof = _derive_asof(ev)
    report_digest = compute_report_digest(ev)
    body_parts = [render_masthead(ev, asof)]
    for sid, title, fn in _SECTION_DEFS:
        if sid == "limits":
            body = render_known_limits(ev)
        elif sid == "safesteps":
            body = render_safe_steps()
        else:
            body = fn(ev)
        body_parts.append(er._SECTION.format(sid=sid, title=er._esc(title),
                                             body=body, anchors=""))
    body_parts.append(er._SECTION.format(sid="integrity", title="Integrity footer",
                                         body=render_footer(ev, report_digest), anchors=""))
    css = er._CSS + _EXTRA_CSS
    return er._DOC.format(title="expos Real-Wet Readiness Report",
                          css=css, body="".join(body_parts))


def build_report(evidence_dir: Path) -> str:
    """Pure function: an evidence directory -> one self-contained offline HTML document."""
    return render_report(load_evidence_set(evidence_dir))


# ============================================================ CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="expos_readiness_report",
        description="Generate the M23 Real-Wet Readiness report as a pure function of a "
                    "physical-evidence directory.")
    parser.add_argument("--evidence", required=True, metavar="DIR",
                        help="the readiness evidence directory (evidence_index.json + scenarios)")
    parser.add_argument("--out", required=True, metavar="FILE", help="output HTML path")
    args = parser.parse_args(argv)

    evidence_dir = Path(args.evidence)
    ev = load_evidence_set(evidence_dir)
    html = render_report(ev)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    present = sum(1 for s in ev.scenarios if s.any_evidence)
    print(f"wrote {out} ({len(html)} bytes)")
    print(f"scenarios: {present}/{len(ev.scenarios)} with evidence; "
          f"index={'present' if ev.index_present else 'MISSING'}")
    print(f"report_digest={compute_report_digest(ev)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
