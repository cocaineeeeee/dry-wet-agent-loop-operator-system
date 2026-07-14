"""SIM3 [P2] regression: coating (second domain) os-mode QC/attribution activation.

Context (red_to_blue/026_sim3-simulator-science.md, finding 3): before this file,
the only coating coverage was ``test_coating_naive_runs_without_kernel_change`` in
test_loop_e2e.py, which runs *naive* mode (no QC/attribution pipeline engaged at
all -- see test_naive_marks_all_trusted). That is "domain swap = YAML swap" at
naive-smoke evidence level only.

The red team ran coating in **os** mode by hand (domains/coating.yaml ships an
artifact_scenario with a persistent edge_evaporation artifact plus a round-2-only
batch_shift injection) and observed that the *same* attribution engine used for
the crystal domain -- wired purely through YAML, zero coating-specific code --
correctly discriminates the two failure modes: the transient batch_shift is
attributed with confidence 1.0 in round 2, and is kept separate from the
persistent edge artifact rather than being lumped into it. This file fixes that
manual finding into an automated regression (report section B3, three-assertion
spec), lifting the "domain swap = YAML swap" claim from naive smoke to
cross-domain empirical evidence.

Note on trust thresholds (finding 4, [note]): domains/coating.yaml currently
reuses the crystal trust thresholds (suspect_high/quarantine_low) uncalibrated
for coating (default scenario runs near an 83% SUSPECT operating point). Per the
red team's finding, **trust thresholds are per-domain calibration items, not
domain-invariant constants** -- this file does not touch or re-tune them; it
only pins down the attribution *discrimination* behavior, which is orthogonal
to where the trust cutoffs happen to sit.
"""

from pathlib import Path

import pytest

from expos.kernel.store import RunStore
from expos.loop import run_loop

ROOT = Path(__file__).resolve().parent.parent
COATING = ROOT / "domains" / "coating.yaml"

# Round in which domains/coating.yaml injects the one-off batch_shift artifact
# (simulator.artifact_scenario: {round: 2, injector: batch_shift, ...}).
_BATCH_SHIFT_ROUND = 2


@pytest.fixture(scope="module")
def coating_os_run(tmp_path_factory):
    """4-round coating os run, same seed (5) as the red team's manual SIM3 run.

    Uses domains/coating.yaml as shipped (persistent edge_evaporation +
    round-2 batch_shift) -- no new fixture YAML needed, this *is* the scenario
    the manual run exercised.
    """
    out = tmp_path_factory.mktemp("runs") / "sim3_coating_os"
    summary = run_loop(COATING, mode="os", rounds=4, seed=5, out_dir=out)
    return out, summary


def _attribution_by_round(store):
    """round_id -> list of attribution event payloads."""
    by_round: dict[int, list] = {}
    for e in store.read_events("attribution"):
        p = e["payload"]
        by_round.setdefault(p["round_id"], []).append(p)
    return by_round


def test_coating_os_activates_qc_attribution_domain_agnostically(coating_os_run):
    """B3 assertion 1: os mode actually engages the QC/attribution pipeline on
    coating (unlike naive mode, which marks everything TRUSTED and never calls
    attribute()). This is the minimum bar the naive smoke test could not clear:
    domain swap via YAML alone reaches the same failure-aware machinery."""
    out, summary = coating_os_run
    assert summary["domain"] == "coating"
    assert summary["rounds_completed"] == 4
    assert summary["n_suspect"] > 0  # QC actually flagged something under the shipped scenario

    store = RunStore(out, create=False)
    assert len(store.read_events("qc_report")) == 4  # one per round, same as crystal os
    attribution_events = store.read_events("attribution")
    assert len(attribution_events) > 0  # attribute() was actually invoked, not bypassed


def test_coating_os_separates_transient_batch_shift_from_persistent_edge(coating_os_run):
    """B3 assertions 2 & 3: the round-2-only batch_shift injection is (a) attributed
    to batch_effect exclusively in round 2 -- never bleeding into the rounds where
    it was not injected -- while the persistent edge_evaporation cause keeps
    showing up in every round including round 2 (the two causes coexist without
    one crowding out the other); and (b) at least some round-2 batch_effect calls
    reach confidence 1.0, matching the red team's manual-run observation."""
    out, _ = coating_os_run
    store = RunStore(out, create=False)
    by_round = _attribution_by_round(store)

    for r in range(4):
        assert by_round.get(r), f"no attribution events recorded for round {r}"

    # Persistent artifact: edge_evaporation must be identified as top_cause in
    # every round, including the round with the extra batch_shift injection.
    for r in range(4):
        causes_r = {p["top_cause"] for p in by_round[r]}
        assert "edge_evaporation" in causes_r, f"round {r} lost the persistent edge cause"

    # Transient artifact: batch_effect must appear ONLY in the injected round.
    for r in range(4):
        causes_r = {p["top_cause"] for p in by_round[r]}
        if r == _BATCH_SHIFT_ROUND:
            assert "batch_effect" in causes_r, "batch_shift injection round produced no batch_effect attribution"
        else:
            assert "batch_effect" not in causes_r, (
                f"batch_effect leaked into round {r}, which had no batch_shift injection "
                "-- transient/persistent discrimination broke"
            )

    # Correct AND confident: round-2 batch_effect calls include high-confidence hits,
    # not just weak/uncertain guesses lumped in with everything else.
    r2_batch_conf = [
        p["confidence"] for p in by_round[_BATCH_SHIFT_ROUND] if p["top_cause"] == "batch_effect"
    ]
    high_conf = [c for c in r2_batch_conf if c >= 0.99]
    assert len(high_conf) >= 5, (
        f"expected multiple confidence>=0.99 batch_effect attributions in round "
        f"{_BATCH_SHIFT_ROUND}, got {len(high_conf)} out of {len(r2_batch_conf)}"
    )
