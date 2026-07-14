"""Domain-local smoke for M29: typed protocol -> device_ir -> fake dispatch.

Deterministic. Builds a typed device-neutral protocol (transfer template into a well,
incubate, read fluorescence), lowers it to the device IR, dispatches the op stream on a
fake liquid handler + fake plate reader, and asserts every op committed (sensed
CONFIRMED). Reuses the M23 action-ledger state/outcome vocabulary READ-ONLY. SIMULATION
only -- no real hardware, no real readings.
"""

from __future__ import annotations

from protocols.objects import Protocol, Transfer, Incubate, ReadPlate
from device_ir.ir import lower, Opcode
from expos.adapters.physical import FakeLiquidHandler, FakePlateReader, dispatch
from expos.adapters.wet.action_ledger import SensedOutcome, ActionState


def smoke() -> int:
    proto = Protocol(
        protocol_id="cfe_expression_v01",
        steps=(
            Transfer(source="reservoir_A", destination="B2", volume_ul=10.0),
            Transfer(source="reservoir_B", destination="B2", volume_ul=5.0),
            Incubate(temperature_c=30.0, minutes=120.0),
            ReadPlate(channel="fluorescence", wells=("B2",)),
        ),
    )

    ops = lower(proto)
    # 2 transfers -> 4 (aspirate+dispense), + incubate + measure = 6 ops.
    assert len(ops) == 6, f"expected 6 lowered ops, got {len(ops)}"
    assert ops[-1].opcode is Opcode.MEASURE

    receipts = dispatch(ops, [FakeLiquidHandler(), FakePlateReader()])
    assert all(r.outcome is SensedOutcome.CONFIRMED for r in receipts), \
        [(r.op_id, r.outcome.value) for r in receipts]
    assert all(r.final_state is ActionState.COMMITTED for r in receipts)

    measure = next(r for r in receipts if r.opcode is Opcode.MEASURE)

    print(f"[M29 smoke] protocol {proto.protocol_id!r} -> {len(ops)} device_ir ops")
    print(f"[M29 smoke] dispatched to backends: "
          f"{sorted({r.backend_id for r in receipts})}")
    print(f"[M29 smoke] all {len(receipts)} ops COMMITTED (sensed CONFIRMED); "
          f"plate read={measure.reading}")
    print("[M29 smoke] PASS (typed protocol -> device_ir -> fake dispatch)")
    return 0


if __name__ == "__main__":
    raise SystemExit(smoke())
