"""expos.protocol -- the ProtocolObject + two-target compiler (M16 W2).

VNext (2): Protocol is a **provenance facet** (a sha256 fingerprint riding in
DesignProvenance), not a first-class kernel object; promotion is gated on the
">=2 independent consumers" rule (see spec.PROMOTION_RULE). This package holds:

- ``spec``     -- ProtocolSpec (the declarative protocol object) + canonical_json
- ``compiler`` -- compile(spec, domain_cfg) -> DryJobPlan / WetProtocolPlan +
                  the protocol_fingerprint anchor
"""

from __future__ import annotations

from expos.protocol.spec import (
    ALLOWED_OPS,
    PROTOCOL_CONSUMER_REGISTRY,
    CandidateBinding,
    ProtocolInputs,
    ProtocolSpec,
    ProtocolStep,
    canonical_json,
    promotion_due,
)
from expos.protocol.compiler import (
    CompileError,
    CompiledProtocol,
    DomainCompileConfig,
    DryJobPlan,
    WetProtocolPlan,
    compile,
    compiler_source_sha,
    default_solvent_screen_config,
    protocol_fingerprint,
)

__all__ = [
    "ALLOWED_OPS",
    "PROTOCOL_CONSUMER_REGISTRY",
    "CandidateBinding",
    "ProtocolInputs",
    "ProtocolSpec",
    "ProtocolStep",
    "canonical_json",
    "promotion_due",
    "compile",
    "CompiledProtocol",
    "DryJobPlan",
    "WetProtocolPlan",
    "DomainCompileConfig",
    "default_solvent_screen_config",
    "protocol_fingerprint",
    "compiler_source_sha",
    "CompileError",
]
