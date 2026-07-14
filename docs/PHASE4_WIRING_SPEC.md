# PHASE 4 WIRING SPEC — physical-dispatch orchestration → mcl wet leg

**For:** session B (single-batch mcl.py施工). **From:** session A (Phase 4-A).
**Deliverable under wire:** `expos/adapters/wet/orchestration.py`
(`dispatch_round` / `resume_round` / `recover_action` / `cancel_action` +
`DispatchRoundResult`). A domain owns the orchestration件; B owns every mcl缝.
This spec is the contract between them — anchors, not code (mcl缝 code is B's).

Facade signature (final):
```python
dispatch_round(actions: Iterable[PlannedAction], sensed_backend: SensedState,
               ledger: ActionLedger, *, clock=None, max_polls=64) -> DispatchRoundResult
resume_round(...same...) -> DispatchRoundResult          # crash-resume, idempotent
recover_action(action, sensed_backend, ledger, *, clock=None, max_polls=64) -> ActionRecord
cancel_action(action_id, ledger, *, reason="canceled") -> ActionRecord
```
`DispatchRoundResult.committed_results: list[CommittedResult]` (observed volume +
evidence id, keyed via `.committed_by_well()` / `.committed_by_action()`) is the
**only** channel carrying an observed value; `.non_committed: list[NonCommittedAction]`
carries **no** observed field. `.all_committed` is the round-clean predicate.

---

## 1. Call point — the "transition-execution" segment to wrap

Replacement is inside `expos/adapters/wet/screen.py::run_wet_leg` (called from
`mcl.py:1129`). Split the function at its two existing segments:

| lines | segment | Phase 4 action |
|---|---|---|
| `screen.py:399–409` | **transition execution** — build `WetDriver`, `submit_goal(otp)`, `driver.run(...)` (executes the deck transfers) | **WRAP** with `dispatch_round(...)`: the transfers become `PlannedAction`s dispatched through the ledger; only wells whose transfer **COMMITTED** proceed to the read |
| `screen.py:411–423` | **read-value / observation construction** — `to_execution_result`, `raw_to_observations`, reader provenance stamp | **DO NOT TOUCH.** Only its INPUT set narrows: build observations for `result.committed_by_well()` wells only (a non-committed well yields no observation — the structural gate) |

Do the wrap either inside `run_wet_leg` (add a `sensed_backend`/`ledger` param,
default `None` = pre-Phase-4 path, byte-identical) or at the `mcl.py:1128–1132`
call site. The QC/Trust call at `mcl.py:1135–1137` and the `wet_leg_issued` marker
at `mcl.py:1124–1127` stay as-is (see §5).

## 2. Parameter-source table

`actions` — one `PlannedAction` per deck transfer, from the `otp` (=`compile_wet`
product, `OTProtocol.wells: list[WellPlan]`). Per `WellPlan` there are up to two
stock transfers (`vol_low_ul` from the low-polarity stock, `vol_high_ul` from the
high-polarity stock); map each transfer:

| PlannedAction field | source |
|---|---|
| `action_id` | `ActionLedger.derive_action_id(round_id, exp_id, well_idx)` (+ a stable low/high suffix if two transfers/well) — deterministic idempotency key, resume-reproducible |
| `round_id` | `exp.round_id` |
| `spec_fingerprint` | `otp.custody` spec fingerprint (or `wet_exp.exp_id`) — the protocol identity |
| `source_well` | the stock reservoir id (low- vs high-polarity stock) |
| `destination_well` | `WellPlan.well_id` |
| `requested_volume_ul` | `vol_low_ul` / `vol_high_ul` (per transfer), or `total_ul` if modeled one-transfer-per-well |
| `backend_id` | `"fake-0"` this phase (see below) |
| `expected_pre_state` | derived from the **volume台账初态**: stock/well volumes the `VolumeLedger` holds before this transfer (optimistic-concurrency snapshot) |
| `expected_post_state` | `{}` (unused by the commit gate; reserved) |

`sensed_backend` — this phase = `FakePhysicalBackend(Scenario)` with the
**exact-success default** scenario (`Behaviour.CONFIRM_EXACT`), so every well
CONFIRMs and the wet leg is byte-behaviour-equivalent to pre-Phase-4. The Scenario's
`actions`/`initial`/`source_capacity` mirror the deck. (Real backend later: same
`SensedState` protocol, no mcl change.)

`ledger` — `ActionLedger(store.root / "physical", volume=<VolumeLedger seeded from
the deck initial state>, policy=<default NeverRecover, or WaitForRecovery if the
round wants operator pause>)`. Path convention: `<run>/physical/action_ledger.jsonl`
(append-only, hash-chained; ActionLedger creates the subdir). One ledger per run;
`resume_round` replays it on a resumed round.

`clock` — omit (None) for the fake backend (it owns its virtual clock). `max_polls`
— leave default 64.

## 3. Event emission — timing & kind

Kind `physical_action_transition` (already registered by B). Guarantees the facade
preserves (assert-anchored in `tests/test_realwet_phase4a.py`):
- **PENDING event precedes the hardware I/O** — the `-> PENDING` line is appended and
  flushed before `io_call` runs; the I/O reply is a later `driver_reply_recorded…`
  note. Crash-visible pending, never a silent lost send.
- **COMMITTED event precedes `committed_results`** — every `-> COMMITTED` line is on
  the append-only log before the returned result is built (result = derivative of
  already-persisted records). The event is always the source.

The ledger's `.events` list mirrors the append order; a host that persists them into
the kernel store should preserve that order (append-only, source-of-truth §5).

## 4. Unit ingest — anchor (B段活, no code here)

The single unit-ingest line B adds (`MeasuredResult.unit` vs `cfg.metric_units`)
belongs on the **observation-construction** side (§1, `screen.py:411–423` / the
`raw_to_observations` path), NOT inside the orchestration — a committed transfer's
*volume* is uL by the ledger contract; the *metric* unit is a reader-channel concern.
Place the check where `MeasuredResult` is built from the reader result, gated so it
only runs for `committed_by_well()` wells.

## 5. QC / Trust routing gate (B段守卫测试的验收语句)

A `CommittedResult` is **necessary but not sufficient** for certification: every
committed observation MUST still pass the existing `QCPolicy(...).judge(...)`
(`mcl.py:1135–1137`) → `TrustPolicy` route before it can enter certification
(`_certify_round`, `mcl.py:1158`). Acceptance語 for B's guard test: *"a committed
physical observation that fails QC/Trust does not reach certification; the
decision-face exactly-once semantics (claim_decision dedup) is unchanged by the
orchestration wiring."* The orchestration gate (commit-before-observation) and the
QC/Trust gate compose in series — neither replaces the other.

Event-log-as-truth-source / checkpoint-cursor: the `physical_action_transition` log
is the append-only truth for physical state; a resumed round rebuilds it via
`resume_round` (COMMITTED skip / PENDING re-sense / PLANNED re-dispatch) — verify the
checkpoint cursor consumes, not re-issues, a complete wet leg (`mcl.py:1149–1152`
I2 killpoint semantics unchanged).

## 6. Harness-separation symmetry (AST guard — B adds)

`orchestration.py` is asserted harness-free: it imports **only** `action_ledger` +
`fake_physical` (+ stdlib) — no `expos.eval.harness_record`, no `expos.eval.*`, no
`expos.mcl`. The invariant is declared in the module's top docstring
("HARNESS-SEPARATION INVARIANT"). Add the symmetric AST-level guard test (mirroring
the truth-blind guard): parse the module, assert no imported module name contains
`harness` / starts with `expos.eval` / `expos.mcl`. (Verified passing at handoff.)
```
imported modules: ['__future__', 'action_ledger', 'collections.abc', 'dataclasses', 'fake_physical']
```
