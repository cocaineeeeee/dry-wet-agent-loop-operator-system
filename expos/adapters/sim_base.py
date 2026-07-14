"""模拟器共享基座：真值 → 噪声 → 伪影注入 → RawResult + truth sidecar。

分层不变量（公理 6）：
- `true_value()` 与测量链严格分离：注入器只作用在测量值上；
- truth_records 只在这里生成（唯一合法产地是 adapters/sim_*）；
- RawResult 不含任何真值字段；伪影标签只进 truth_records；
- execute() **不修改** ExperimentObject。

crystal 与 coating 共用本基座——这就是"注入器框架与域无关"的结构性证明。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import qmc

from expos.adapters.artifacts import (
    WellContext,
    injectors_for_round,
    validate_scenario,
)
from expos.adapters.base import AdapterError, ExecutionResult, RawResult
from expos.kernel.objects import DesignSpace, ExperimentObject

_ALLOWED_CONFIG_KEYS = {"noise_sd", "artifact_scenario", "metric"}


class SimulatorBase:
    name = "sim_base"
    default_metric = "quality_index"
    unit = ""
    required_params: frozenset[str] = frozenset()

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = dict(config or {})
        unknown = set(cfg) - _ALLOWED_CONFIG_KEYS
        if unknown:
            raise AdapterError(f"{self.name} 未知 simulator 配置键: {sorted(unknown)}")
        self.noise_sd = float(cfg.get("noise_sd", 0.02))
        self.scenario = cfg.get("artifact_scenario") or []
        validate_scenario(self.scenario)
        self.metric = cfg.get("metric", self.default_metric)

    # ------------------------------------------------------------ 子类接口

    #: secondary 的相对测量噪声（对抗审查 #2：无噪声的 secondary/exposure 是完美
    #: 1-bit oracle，会让 QC 的伪影检出退化成阈值查表而非统计推断）
    secondary_noise: float = 0.05

    def true_value(self, params: dict[str, Any]) -> float:
        raise NotImplementedError

    @staticmethod
    def _num(
        params: dict[str, Any], key: str, lo: float, hi: float
    ) -> float:
        """true_value 公开面的参数守卫：缺键/非数值/超物理有效域一律 AdapterError
        （压力测试 finding：原先越界参数会静默返回貌似合理的值）。
        物理有效域比设计空间宽——它守的是函数本身的定义域，不是实验设计边界。"""
        if key not in params:
            raise AdapterError(f"true_value 缺参数 {key!r}")
        try:
            x = float(params[key])
        except (TypeError, ValueError):
            raise AdapterError(f"true_value 参数 {key}={params[key]!r} 非数值")
        if not lo <= x <= hi:
            raise AdapterError(f"true_value 参数 {key}={x} 超出物理有效域 [{lo}, {hi}]")
        return x

    def secondary(self, params: dict[str, Any]) -> dict[str, float]:
        """次级指标只允许依赖 params——签名上不给 true_value，
        真值泄漏在类型层面不可能（对抗审查 #1 的结构性修复）。"""
        return {}

    # ------------------------------------------------------------ 执行

    def _params_for(self, exp: ExperimentObject) -> dict[str, dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for c in exp.candidates:
            by_id[c.cand_id] = c.params
        for c in exp.controls:
            by_id[c.control_id] = c.params
        return by_id

    def _check_params(self, entry_id: str, params: dict[str, Any]) -> None:
        missing = self.required_params - set(params)
        if missing:
            raise AdapterError(f"{self.name}: 条目 {entry_id} 缺参数 {sorted(missing)}")

    def execute(
        self, exp: ExperimentObject, rng: np.random.Generator
    ) -> ExecutionResult:
        if exp.layout is None:
            raise AdapterError(f"{self.name}: ExperimentObject 无 layout，不能执行")
        if exp.objective.metric != self.metric:
            raise AdapterError(
                f"{self.name}: 目标指标 {exp.objective.metric!r} 与模拟器指标 "
                f"{self.metric!r} 不符（未知指标响亮失败）"
            )
        params_by_id = self._params_for(exp)
        injectors = injectors_for_round(self.scenario, exp.round_id)
        n_batches = max(1, exp.execution_req.n_solution_batches)
        wells = exp.layout.wells
        n = len(wells)
        raws: list[RawResult] = []
        truths: list[dict[str, Any]] = []

        for idx, w in enumerate(wells):  # 枚举序即 capture 序（drift 依赖递增）
            entry_id = w.cand_id if w.cand_id is not None else w.control_id
            if entry_id not in params_by_id:
                raise AdapterError(f"{self.name}: layout 引用未知条目 {entry_id!r}")
            params = params_by_id[entry_id]
            self._check_params(entry_id, params)

            tv = float(self.true_value(params))
            noise = float(rng.normal(0.0, self.noise_sd))
            measured = tv + noise

            # 批次按空间棋盘格 (row+col)%n：与 capture_index（防漂移混淆，审查 #3）
            # **且与 is_edge**（防边缘混淆——分层交替发射使 idx%n 与边缘奇偶对齐，
            # M6 联合端到端实测 29/40 误归因 batch_effect 后修正）双解耦
            batch = f"R{exp.round_id}-B{(w.row + w.col) % n_batches}"
            ctx = WellContext(
                well_id=w.well_id, row=w.row, col=w.col,
                rows=exp.layout.rows, cols=exp.layout.cols,
                is_edge=w.is_edge, block_id=w.block_id,
                solution_batch=batch, capture_index=idx, round_id=exp.round_id,
            )
            tags: list[str] = []
            for inj in injectors:
                measured, applied, tag = inj.apply(measured, ctx, rng)
                if applied:
                    tags.append(tag)
            measured = max(0.0, float(measured))

            # 次级指标：只依赖 params + 测量噪声（不是无噪声 oracle）
            sec = {
                k: float(v) * (1.0 + float(rng.normal(0.0, self.secondary_noise)))
                for k, v in self.secondary(params).items()
            }
            # 伪影在测量证据上的可观测足迹（QC 的线索，不是真值泄漏），同样带噪：
            exposure = (1.5 if "glare" in tags else 1.0) * (
                1.0 + float(rng.normal(0.0, 0.03))
            )
            if "dust_nucleation" in tags and "grain_count" in sec:
                sec["grain_count"] = sec["grain_count"] * 3.0

            raws.append(
                RawResult(
                    well_id=w.well_id, cand_id=w.cand_id, control_id=w.control_id,
                    metric=self.metric, value=measured, unit=self.unit,
                    secondary=sec, exposure=exposure, illumination=1.0,
                    capture_index=idx, solution_batch=batch, additive_lot="LOT-A",
                )
            )
            truths.append(
                {
                    "well_id": w.well_id,
                    "cand_id": w.cand_id,
                    "control_id": w.control_id,
                    "true_value": tv,
                    "noise": noise,
                    "measured_value": measured,
                    "artifacts": tags,
                }
            )
        return ExecutionResult(raw_results=raws, truth_records=truths)

    # ------------------------------------------------------------ 事后评分辅助

    def true_optimum(
        self, space: DesignSpace, n: int = 4096, seed: int = 0
    ) -> tuple[dict[str, Any], float]:
        """密集 Sobol 扫描估计真值最优（只供事后评分脚本用，OS 决策模块禁用）。"""
        from expos.design.space import dim, from_unit

        sobol = qmc.Sobol(d=dim(space), scramble=True, seed=seed)
        best_p: dict[str, Any] | None = None
        best_v = -np.inf
        for u in sobol.random(n):
            p = from_unit(space, u)
            v = self.true_value(p)
            if v > best_v:
                best_p, best_v = p, v
        assert best_p is not None
        return best_p, float(best_v)
