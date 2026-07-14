# dimrep handoff — wet multi-replicate substrate (M17 K-F prerequisite)

**From:** A (wet/domain side)  **To:** B (mcl/K-C wiring)  **Date:** 2026-07-13
**Re:** landing the multi-replicate wet substrate letter 085 asked for. K1's
decisive verdict is now REACHABLE; the plumbing is done on my side.

## One-line mcl wiring (the only change B needs)

In `expos/mcl.py::_run_round`, the wet-leg compile currently is:

```python
otp = compile_wet(wet_exp)
wet_exp = wet_exp.model_copy(update={"layout": layout_from_protocol(otp)})
```

Change ONLY the compile call to pass the replicate substrate:

```python
otp = compile_wet(wet_exp, n_replicates=cfg.replicates, interleave=True)
wet_exp = wet_exp.model_copy(update={"layout": layout_from_protocol(otp)})  # unchanged
```

- `layout_from_protocol(otp)` stays at its DEFAULTS — it mirrors the already-
  replicated deck 1:1. Do NOT also pass n_replicates to `layout_from_protocol`
  (that path is the standalone/synthetic-layout expansion; passing it here would
  double-expand). Expansion has exactly one owner in the loop: `compile_wet`,
  which owns the physical measurement order (→ reader capture sequence →
  `capture_index`) and the per-replicate chain of custody.
- `cfg.replicates` already exists in the schema (`domains/*.yaml`, default 2).
  **Recommend setting `replicates: 3`** in `solvent_screen.yaml` /
  `solvent_screen_flipped.yaml` before wiring: 3 replicates × the 2-solvent-per-arm
  polarity contrast = 6 paired obs/round, which crosses 1/alpha in two rounds.
  (2 replicates = 4 pairs → e-product ≈ 4.9² ≈ 24 also clears it, but 3 is the
  safe default with margin; a SINGLE candidate per arm needs ≥6 replicates because
  3 pairs cap the sign-flip e at ~1.0 — see numbers below.)
- `interleave=True` is a call-side kwarg (no schema field), same convention as
  `truth_profile`. Default kwarg shape: `interleave: bool = True` for the mcl loop
  (balanced plate order is the whole point); leave `False` only to reproduce the
  confounded negative control.

## Kwarg shapes (all landed in adapters/wet/screen.py)

```python
compile_wet(exp, *, total_volume_ul=200.0, n_replicates=1, interleave=False)
protocol_spec_from_experiment(exp, *, total_volume_ul=200.0, n_replicates=1, interleave=False)
layout_from_protocol(otp, *, seed=0, n_replicates=1, interleave=False)
```

Defaults (1 / False) are BIT-FOR-BIT the pre-K-F single-well plate (regression-
frozen; whole w8/w9/k suite green unchanged). Replicates share `cand_id` (same
aggregator arm) but get distinct `sample_id` (`SMP-CND-<cand>-r{k}`) → independent
4-segment custody per well. Interleave = per-replicate cyclic rotation of the
candidate order (Latin-square), giving `corr(capture_index, arm) ≈ 0` even when the
arms are contiguous in candidate order.

## Budget impact (96-well deck, one plate = one round)

- 4 candidates × 3 replicates = **12 wells** + sentinels (e.g. 2×3 = 6) = 18 wells,
  well under the 96 cap. Overflow (`candidates × replicates > 96`) is a loud
  `AdapterError`, not a silent truncation.
- Rounds are unaffected (still one plate/round). Reagent/tip cost scales ×
  n_replicates; still trivial at these counts.

## Reachability numbers (proven, tests/test_k_replicate_substrate.py)

Flipped face, 2 polar vs 2 nonpolar candidates, 3 replicates, interleaved, fed
straight to `aggregate_round`:

| round | round_e | e_product | status        |
|-------|---------|-----------|---------------|
| 0     | 4.573   | 4.573     | insufficient (rounds < r_min) |
| 1     | 4.573   | **20.910**| **rejected** (decisive, CONTRARY) |

e_threshold = 1/alpha = 20.0; effect ≈ −0.48 (polar arm lower → contradicts the
seeded "polar higher"); plate_order_balance = 0.0; confound_suspect = False.

Negative control (letter 085, single well / single-candidate arm): n_pairs=1 →
round_e = 0.0, e_product frozen at 1.0, `corr(capture,arm)=±1` → confound_suspect
True, ci None → insufficient forever. That is the ready-made "delete the guard and
it goes red" reference.
