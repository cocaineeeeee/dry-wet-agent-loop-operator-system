"""M29 domain-local end-to-end: intent -> typed protocol -> constraint check ->
device_ir -> simulated dispatch -> sensed-state -> commit/rollback -> observation.

Deterministic. Builds the safe cell-free protein-expression protocol, gate-checks it
against the labware/volume/ordering constraints, lowers it to the device IR (with a
provenance fingerprint), and dispatches every unit transactionally through the REAL M23
action-ledger over a scratch run directory -- so every unit reaches COMMITTED only through
the sensed-state gate (never a blind driver OK). Emits a machine report as JSON.

Run:  ``python -m protocols``

HONESTY (BIOLOGY_PROGRAM_2026 §5): this is **protocol-to-simulated-physical** against a
fake backend. NOT a physical autonomous laboratory; real hardware / wet-lab validation is
pending. The transaction machinery is real; the physics is faked.
"""

from __future__ import annotations

import json
import tempfile

from protocols.objects import cell_free_expression_protocol
from protocols.constraints import check_protocol
from device_ir.ir import group_units, ir_fingerprint, lower
from expos.adapters.physical import (
    FakeLiquidHandler,
    FakePlateReader,
    ProtocolExecutor,
)


def run_e2e(run_dir: str) -> dict:
    # 1. biological intent -> typed protocol
    proto = cell_free_expression_protocol()

    # 2. constraint check (LOUD gate: raises on a physically-unrunnable protocol)
    reservoirs = frozenset({"reservoir_lysate", "reservoir_dna", "reservoir_buffer"})
    report = check_protocol(proto, reservoirs=reservoirs).raise_if_bad()

    # 3. device-neutral lowering + provenance fingerprint
    ops = lower(proto)
    units = group_units(ops)
    ir_fp = ir_fingerprint(ops)

    # 4. transactional dispatch through the real M23 ledger (sensed-state gated)
    executor = ProtocolExecutor(
        run_dir,
        [FakeLiquidHandler(), FakePlateReader()],
        protocol_id=proto.protocol_id,
        spec_fingerprint=ir_fp,
        reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                    "reservoir_buffer": 1000.0},
    )
    log = executor.run(units)

    return {
        "milestone": "M29 protocol compilation & embodied wet execution v0.1",
        "validation_level": "protocol-to-simulated-physical (fake backend; "
                            "NOT a physical autonomous laboratory; real hardware pending)",
        "protocol_id": proto.protocol_id,
        "n_steps": len(proto.steps),
        "n_device_ops": len(ops),
        "n_units": len(units),
        "ir_fingerprint": ir_fp,
        "constraint_check": {
            "ok": report.ok,
            "filled_wells": sorted(report.filled_wells),
            "well_volumes": report.well_volumes,
            "warnings": report.warnings,
        },
        "dispatch": {
            "all_committed": log.all_committed,
            "n_committed": sum(1 for u in log.units if u.committed),
            "units": [
                {"unit_id": u.unit_id, "kind": u.kind, "state": u.final_state.value,
                 "outcome": u.outcome.value, "reading": u.reading}
                for u in log.units
            ],
            "readings": log.readings,
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="m29_e2e_") as run_dir:
        report = run_e2e(run_dir)
    print(json.dumps(report, indent=2))
    assert report["dispatch"]["all_committed"], "expected every unit COMMITTED on the " \
        "normal face"
    print("\n[M29 e2e] PASS -- intent -> typed protocol -> constraint check -> device_ir "
          f"({report['n_units']} units) -> sensed-state dispatch -> all COMMITTED")
    print("[M29 e2e] protocol-to-simulated-physical ONLY (fake backend; real hardware "
          "pending)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
