"""M29 ``expos.adapters.physical`` -- v0.1 SKELETON fake physical backends.

A fake liquid handler + fake plate reader that execute ``device_ir`` ops and return
sensed receipts. They speak the M23 action-ledger vocabulary (``ActionState`` /
``SensedOutcome``) READ-ONLY so a later pass can wire real commit/rollback/resume through
the existing transaction machinery without re-deciding the state model. No real hardware;
no real firmware. Explicitly a simulation.
"""

from expos.adapters.physical.fake_backends import (
    FakeLiquidHandler,
    FakePlateReader,
    DispatchReceipt,
    Fault,
    dispatch,
    pick_backend,
)
from expos.adapters.physical.orchestrator import (
    ProtocolExecutor,
    RunLog,
    UnitResult,
    run_ops,
)

__all__ = [
    "FakeLiquidHandler", "FakePlateReader", "DispatchReceipt", "Fault",
    "dispatch", "pick_backend",
    "ProtocolExecutor", "RunLog", "UnitResult", "run_ops",
]
