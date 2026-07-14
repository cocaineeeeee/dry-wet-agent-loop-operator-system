"""W4 — wet instrument stack (trusted simulation) for the M16 minimum loop.

Two constructs (see docs/M16_MIN_LOOP.md G3, docs/ADAPTER_ACTIONS.md):

1. Opentrons ``simulate`` protocol-execution leg (``ot_protocol``): the real
   protocol stack (labware / pipette / deck / volume validation) gate-keeps the
   liquid-handling protocol. Falls back to a same-interface deck/volume/labware
   validator when opentrons cannot be imported, reporting the degradation
   honestly via ``OT_BACKEND``.

2. A standalone plate-reader simulator (``sim_reader``) plus a client library
   (``driver.WetDriver``) that implements the ADAPTER_ACTIONS six-state machine
   (ACCEPTED -> EXECUTING -> {SUCCEEDED | ABORTED | CANCELED}) and the seven
   instrument concerns of G3: health / calibration / reservation / protocol
   validation / timeout+retry / device-failure handling / sample-identity chain.

This package is intentionally free of any ``expos`` import so it can be tested in
isolation; ``bridge.py`` documents (and optionally performs) the mapping onto the
expos ingestion contract when the kernel is importable.
"""

from .protocol_spec import (
    ProtocolSpec,
    SolventSample,
    CustodyChain,
    CustodyRecord,
)
from .ot_protocol import (
    OTProtocol,
    ValidationError,
    OT_BACKEND,
    compile_and_validate,
    execute_simulated,
)
from .driver import (
    WetDriver,
    GoalState,
    WetExecutionResult,
    WetDriverError,
    ValidationReport,
)
from .recovery import (
    RecoveryPolicy,
    RecoveryAction,
    FailureDetail,
    FailureMode,
    FAILURE_MODES,
    NeverRecover,
    WaitForRecovery,
    AssumeFalsePositive,
    failure_modes_catalogue,
)
from .labware import Labware, LabwareError, load_labware, load_labware_doc

__all__ = [
    "ProtocolSpec",
    "SolventSample",
    "CustodyChain",
    "CustodyRecord",
    "OTProtocol",
    "ValidationError",
    "OT_BACKEND",
    "compile_and_validate",
    "execute_simulated",
    "WetDriver",
    "GoalState",
    "WetExecutionResult",
    "WetDriverError",
    "ValidationReport",
    "RecoveryPolicy",
    "RecoveryAction",
    "FailureDetail",
    "FailureMode",
    "FAILURE_MODES",
    "NeverRecover",
    "WaitForRecovery",
    "AssumeFalsePositive",
    "failure_modes_catalogue",
    "Labware",
    "LabwareError",
    "load_labware",
    "load_labware_doc",
]
