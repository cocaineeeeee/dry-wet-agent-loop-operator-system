"""执行 adapter 统一协议（docs/ARCHITECTURE.md §6）。

- ExecutionAdapter 消费 ExperimentObject，**不得修改它**（测试断言前后 model_dump 相等）。
- ExecutionResult.truth_records 是仿真真值 sidecar 载荷：只允许 adapters/sim_* 生成，
  loop 只做不透明落盘，qc/models/planner/agent 一律禁读（公理 6）。
- RawResult 是"OS 可见"的测量原始记录：**不含任何真值字段**；
  伪影注入的透明元数据只写进 truth_records（写进 RawResult 会让 QC 层"作弊"，
  破坏 naive vs OS 对比的公平性——这是有意的设计决策，见 CHECKPOINTS M3）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from expos.kernel.objects import ExperimentObject


from expos.errors import ExposError


class AdapterError(ExposError):
    pass


class RawResult(BaseModel):
    """单孔测量原始记录（OS 可见面）。"""

    model_config = ConfigDict(extra="forbid")

    well_id: str
    cand_id: str | None = None
    control_id: str | None = None
    metric: str
    value: float | None = None
    unit: str = ""
    secondary: dict[str, float] = Field(default_factory=dict)
    exposure: float = 1.0
    illumination: float = 1.0
    capture_index: int = 0
    solution_batch: str = ""
    additive_lot: str = ""
    # Provenance three-tuple (letter 051, additive / zero-migration): the raw
    # workdir product uri + its content sha256 + the producing engine. These flow
    # through raw_to_observations into ObservationObject.raw_ref (uri/sha256) and
    # InstrumentMeta.engine, so provenance is no longer dropped at ingestion (the
    # gap the dry adapter's DryRawResult sidecar was bridging). None = source
    # carried no provenance (legacy sim path).
    uri: str | None = None
    sha256: str | None = None
    engine: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "RawResult":
        if (self.cand_id is None) == (self.control_id is None):
            raise ValueError(f"raw {self.well_id}: cand_id 与 control_id 必须二选一")
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_results: list[RawResult]
    truth_records: list[dict[str, Any]] | None = None  # 仅 sim_* 生成；不透明 sidecar 载荷


@runtime_checkable
class ExecutionAdapter(Protocol):
    name: str

    def execute(
        self, exp: ExperimentObject, rng: np.random.Generator
    ) -> ExecutionResult: ...
