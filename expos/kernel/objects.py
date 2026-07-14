"""内核对象 schema（权威定义：docs/ARCHITECTURE.md §4）。

内核只有两个持久科学对象：ExperimentObject 与 ObservationObject。
DecisionRecord 是事件日志载荷（`kind="decision"`），**不是第三个内核对象**。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- 枚举

class ExpStatus(str, Enum):
    DESIGNED = "DESIGNED"
    EXECUTED = "EXECUTED"
    QC_DONE = "QC_DONE"
    ROUTED = "ROUTED"
    CLOSED = "CLOSED"


class TrustLevel(str, Enum):
    PENDING = "PENDING"
    TRUSTED = "TRUSTED"
    SUSPECT = "SUSPECT"
    FAILED = "FAILED"


class Routing(str, Enum):
    TO_RESPONSE_MODEL = "TO_RESPONSE_MODEL"
    TO_FAILURE_MODEL = "TO_FAILURE_MODEL"
    QUARANTINE = "QUARANTINE"
    REMEASURE = "REMEASURE"
    REPEAT_CANDIDATE = "REPEAT_CANDIDATE"


class ActionType(str, Enum):
    NEW_CANDIDATES = "NEW_CANDIDATES"
    REMEASURE = "REMEASURE"
    DISAMBIGUATION_REPEAT = "DISAMBIGUATION_REPEAT"
    REPEAT_CANDIDATE = "REPEAT_CANDIDATE"
    ADD_CONTROLS = "ADD_CONTROLS"
    NONE = "NONE"


class Actor(str, Enum):
    AGENT = "agent"
    PLANNER = "planner"
    HUMAN = "human"


class HypothesisStatus(str, Enum):
    """Knowledge-face hypothesis status (M16 minimal — no graph).

    OPEN=posed, no decisive evidence yet; SUPPORTED/REJECTED=compiled from claim
    evidence (see kernel.knowledge.compile_knowledge); SUPERSEDED=replaced by a
    later hypothesis (terminal, sticky — never recomputed from claims).
    """

    OPEN = "OPEN"
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class DecisionKind(str, Enum):
    GOAL_TRANSLATION = "goal_translation"
    PRIOR_PROPOSAL = "prior_proposal"
    QC_EXPLANATION = "qc_explanation"
    ATTRIBUTION_EXPLANATION = "attribution_explanation"
    ROUND_RATIONALE = "round_rationale"
    ACTION_PROPOSAL = "action_proposal"
    ACCEPTANCE = "acceptance"
    REJECTION = "rejection"
    OVERRIDE = "override"


#: 提案类决策：必须有配对的 acceptance/rejection 才可能影响后续设计（§4.5 审计不变量）
PROPOSAL_KINDS = frozenset(
    {
        DecisionKind.GOAL_TRANSLATION,
        DecisionKind.PRIOR_PROPOSAL,
        DecisionKind.ACTION_PROPOSAL,
    }
)


class KernelModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------- 实验侧

class VariableDef(KernelModel):
    name: str
    kind: Literal["continuous", "categorical"] = "continuous"
    low: float | None = None
    high: float | None = None
    transform: Literal["linear", "log"] = "linear"
    choices: list[Any] | None = None
    unit: str = ""
    # M20 domain-swappability (INDEX_M19_DOMAIN2 §5): optional per-variable
    # {level: {coord: value}} map from a categorical level to physical
    # coordinate(s). Additive-optional (absent => the legacy solvent path,
    # byte-identical). It is what lets the generic wet leg + mcl bindings map a
    # discrete option to a mixable coordinate WITHOUT a hardcoded per-domain table.
    descriptors: dict[str, dict[str, float]] | None = None

    @model_validator(mode="after")
    def _check(self) -> "VariableDef":
        if self.kind == "continuous":
            if self.low is None or self.high is None or not self.low < self.high:
                raise ValueError(f"continuous 变量 {self.name} 需要 low < high")
            if self.transform == "log" and self.low <= 0:
                raise ValueError(f"log 变量 {self.name} 需要 low > 0")
        else:
            if not self.choices:
                raise ValueError(f"categorical 变量 {self.name} 需要非空 choices")
        if self.descriptors is not None:
            # M20 validation: descriptors belong on a categorical variable, declare
            # >= 1 level, each level's coord map is nonempty, and ALL levels share
            # the same coord keys (a discrete option map with drifting coordinate
            # axes is a loud error, never a silent gap).
            if self.kind != "categorical":
                raise ValueError(
                    f"变量 {self.name}: descriptors 仅用于 categorical 变量（当前 {self.kind}）"
                )
            if not self.descriptors:
                raise ValueError(f"变量 {self.name}: descriptors 需 >= 1 个 level")
            coord_keys: frozenset[str] | None = None
            for level, coords in self.descriptors.items():
                if not coords:
                    raise ValueError(
                        f"变量 {self.name}: descriptors[{level!r}] 坐标映射不可为空"
                    )
                keys = frozenset(coords)
                if coord_keys is None:
                    coord_keys = keys
                elif keys != coord_keys:
                    raise ValueError(
                        f"变量 {self.name}: descriptors 各 level 坐标键不一致——"
                        f"level {level!r} 为 {sorted(keys)}，另一 level 为 "
                        f"{sorted(coord_keys)}（同一变量所有 level 须共享坐标键）"
                    )
        return self


class DesignSpace(KernelModel):
    name: str
    variables: list[VariableDef]

    @model_validator(mode="after")
    def _unique_names(self) -> "DesignSpace":
        # J-3：变量重名会产生"看似正常"的幻影维度（var() 只取首个、to_unit 维数错位）。
        names = [v.name for v in self.variables]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"DesignSpace {self.name} 变量名重复: {dupes}")
        return self

    def var(self, name: str) -> VariableDef:
        for v in self.variables:
            if v.name == name:
                return v
        raise KeyError(name)


class Objective(KernelModel):
    name: str
    metric: str
    direction: Literal["maximize", "minimize"] = "maximize"
    description: str = ""


class Constraint(KernelModel):
    name: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class Candidate(KernelModel):
    cand_id: str = Field(default_factory=lambda: new_id("cand"))
    params: dict[str, Any]
    source: str = "manual"
    rationale: str = ""
    placement_hint: str | None = None  # center_only / edge_center_pair
    parent_obs_id: str | None = None


class Control(KernelModel):
    control_id: str = Field(default_factory=lambda: new_id("ctrl"))
    kind: Literal["sentinel", "negative", "positive"] = "sentinel"
    params: dict[str, Any] = Field(default_factory=dict)
    expected_band: tuple[float, float] | None = None


class ReplicatePlan(KernelModel):
    n_replicates: int = 2
    strategy: str = "across_blocks"


class WellAssignment(KernelModel):
    well_id: str
    row: int
    col: int
    cand_id: str | None = None
    control_id: str | None = None
    is_edge: bool = False
    block_id: str = ""

    @model_validator(mode="after")
    def _exactly_one(self) -> "WellAssignment":
        if (self.cand_id is None) == (self.control_id is None):
            raise ValueError(f"well {self.well_id}: cand_id 与 control_id 必须二选一")
        return self


class LayoutAssignment(KernelModel):
    rows: int
    cols: int
    seed: int
    wells: list[WellAssignment] = Field(default_factory=list)


class Budget(KernelModel):
    wells_total: int
    wells_used: int = 0
    rounds_total: int
    rounds_used: int = 0


class ExecutionReq(KernelModel):
    adapter: str
    params: dict[str, Any] = Field(default_factory=dict)
    n_solution_batches: int = 1


class DesignProvenance(KernelModel):
    generator: str
    acquisition: str | None = None
    model_snapshot: str | None = None
    based_on_obs: int = 0
    actions_consumed: list[str] = Field(default_factory=list)
    rationale: str = ""
    #: 风险图**消费侧**取证（O3DV C2 修复；加性非必填=非 ABI 破坏）：由 build_experiment
    #: 从它实收并交给 LayoutPlanner 的 risk_map 参数计算——"转手断线"（给 layout 传 None
    #: 但活性事件照发产出侧摘要）在此显形。None=旧版/外部构造路径未产该证据。
    risk_map_summary: dict[str, Any] | None = None
    #: VNext ② Protocol-fingerprint anchor (additive, zero-migration — same
    #: precedent as risk_map_summary above). sha256 over
    #: canonical_json(ProtocolSpec) || compiler-source-sha, produced by
    #: expos.protocol.compiler.compile(). It pins the exact declarative protocol
    #: + compiler version that generated this design. Q1 (RESEARCH_OS_VNEXT Part
    #: IV): Protocol enters as a PROVENANCE FACET, not a first-class kernel
    #: object; promotion to first-class is gated on the ">=2 independent
    #: consumers" rule. The loop/experiment build side is intentionally NOT wired
    #: here yet: W3 (dry) and W4 (wet) stamp this when they consume the compiled
    #: plans. None = design built without a compiled protocol (legacy / direct
    #: construction). Wired by W3/W4.
    protocol_fingerprint: str | None = None


class ExperimentObject(KernelModel):
    exp_id: str = Field(default_factory=lambda: new_id("exp"))
    round_id: int
    domain: str
    objective: Objective
    design_space: DesignSpace
    active_vars: list[str] = Field(default_factory=list)
    restrictions: list[Constraint] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    controls: list[Control] = Field(default_factory=list)
    replicate_plan: ReplicatePlan = Field(default_factory=ReplicatePlan)
    layout: LayoutAssignment | None = None
    budget: Budget
    execution_req: ExecutionReq
    provenance: DesignProvenance
    status: ExpStatus = ExpStatus.DESIGNED
    created_at: str = Field(default_factory=utc_now)


# ---------------------------------------------------------------- 观测侧

class MeasuredResult(KernelModel):
    metric: str
    value: float | None = None
    uncertainty: float | None = None  # 测量不确定度估计（借鉴 LAP MeasurementResult）
    secondary: dict[str, float] = Field(default_factory=dict)
    unit: str = ""


class RawDataRef(KernelModel):
    uri: str = ""
    kind: str = "sim"
    sha256: str | None = None


class LayoutMeta(KernelModel):
    well_id: str
    row: int
    col: int
    is_edge: bool = False
    block_id: str = ""


class MaterialMeta(KernelModel):
    solution_batch: str = ""
    additive_lot: str = ""
    prep_order: int = 0


class InstrumentMeta(KernelModel):
    instrument_id: str = "sim"
    exposure: float = 1.0
    illumination: float = 1.0
    capture_index: int = 0
    #: Provenance three-tuple, position 3/3 (letter 051, additive / zero-migration —
    #: same precedent as DesignProvenance.risk_map_summary). The engine that produced
    #: the measurement (e.g. "pyscf", "opentrons"); None = legacy / unknown engine.
    #: Lets the dry adapter's InstrumentProvenance sidecar (engine field) land on a
    #: first-class kernel position instead of being smuggled into instrument_id.
    engine: str | None = None


class QCCheck(KernelModel):
    name: str
    level: Literal["hard", "reference", "structural"]
    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)  # 嫌疑分 [0,1]，越高越可疑（Q-3）
    evidence: dict[str, Any] = Field(default_factory=dict)


class QCReport(KernelModel):
    checks: list[QCCheck] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    # 汇总嫌疑分 [0,1]；0 表示"由裁决函数按 checks 自行汇总"（Q-3）
    suspicion: float = Field(default=0.0, ge=0.0, le=1.0)


class FailureHypothesis(KernelModel):
    cause: str
    score: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    remedy: ActionType = ActionType.NONE


class FailureAttribution(KernelModel):
    hypotheses: list[FailureHypothesis] = Field(default_factory=list)
    top_cause: str | None = None
    confidence: float = 0.0


class RecommendedAction(KernelModel):
    action: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ObservationObject(KernelModel):
    obs_id: str = Field(default_factory=lambda: new_id("obs"))
    exp_id: str
    round_id: int
    cand_id: str | None = None
    control_id: str | None = None
    is_control: bool = False
    result: MeasuredResult
    raw_ref: RawDataRef = Field(default_factory=RawDataRef)
    layout_meta: LayoutMeta
    material_meta: MaterialMeta = Field(default_factory=MaterialMeta)
    instrument_meta: InstrumentMeta = Field(default_factory=InstrumentMeta)
    qc: QCReport | None = None
    trust: TrustLevel = TrustLevel.PENDING
    # VNext batch-1 semantic narrowing (facet: trust.confidence). This field is
    # ADJUDICATION confidence only. Writers: lifecycle.adjudicate (backfills the
    # in-band suspicion for QUARANTINE) and lifecycle.reclassify (human/planner
    # certainty = 1.0). It must NEVER be read as a learning weight: learning
    # weights travel exclusively as the explicit per_point_alpha vector returned
    # by AggregationPolicy.prepare (Part IV Q2: semantics via facet, transport
    # via explicit parameter, covert channel deleted).
    trust_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_attr: FailureAttribution | None = None
    routing: Routing | None = None
    next_action: RecommendedAction | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _consistent(self) -> "ObservationObject":
        if (self.cand_id is None) == (self.control_id is None):
            raise ValueError(f"obs {self.obs_id}: cand_id 与 control_id 必须二选一")
        if self.is_control != (self.control_id is not None):
            raise ValueError(f"obs {self.obs_id}: is_control 与 control_id 不一致")
        return self


# ---------------------------------------------------------------- 决策载荷

class DecisionRecord(KernelModel):
    """事件日志载荷（kind="decision"）——不是第三个内核对象。

    审计不变量：actor=agent 且 kind∈PROPOSAL_KINDS 的记录，必须存在一条
    kind=acceptance/rejection 且 refs 含其 decision_id 的记录，才可能影响后续设计
    （机器检查见 lifecycle.unresolved_proposals / accepted_proposals）。
    """

    decision_id: str = Field(default_factory=lambda: new_id("dec"))
    round_id: int
    actor: Actor
    kind: DecisionKind
    refs: list[str] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    accepted: bool | None = None
    validator: str | None = None
    created_at: str = Field(default_factory=utc_now)


# ---------------------------------------------------------------- 知识面（M16 最小）

class HypothesisObject(KernelModel):
    """Minimal knowledge-face hypothesis (M16 — no knowledge graph).

    A hypothesis carries a natural-language statement plus a list of claim_id
    references into the claim ledger (claims/ledger.json — see
    scripts/claim_compiler.py). Its effective status is COMPILED from that
    evidence by kernel.knowledge.compile_knowledge (knowledge is a compiled
    product, never hand-authored — same discipline as the claim ledger). The
    stored ``status`` seeds/records the persisted state; SUPERSEDED is terminal
    (a superseded hypothesis is never recomputed from claims).
    """

    hypothesis_id: str = Field(default_factory=lambda: new_id("hyp"))
    statement: str
    status: HypothesisStatus = HypothesisStatus.OPEN
    #: claim_id references into the claim ledger — the evidence this hypothesis
    #: is compiled against (order-insensitive; compile_knowledge canonicalizes).
    evidence_refs: list[str] = Field(default_factory=list)
    updated_utc: str = Field(default_factory=utc_now)
