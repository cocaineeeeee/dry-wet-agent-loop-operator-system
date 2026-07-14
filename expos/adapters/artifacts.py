"""伪影注入器（docs/ARCHITECTURE.md §6；定量依据 REFERENCE_MAP §11.3）。

分层原则（公理 6 的执行面）：
- 注入器作用在**测量层**：先算真值、再叠噪声、最后过注入器——真值与测量值全程分离；
- `apply` 返回 (被污染的测量值, 是否命中, 标签)：透明元数据**只写进 truth sidecar**，
  不进 RawResult——写进 OS 可见面会让 QC"作弊"、破坏对比公平（有意决策，见 CHECKPOINTS M3）；
- 本层不做 QC、不做归因——那是 M5/M6 的事。

各注入器的函数形式与默认幅度依据（详见 REFERENCE_MAP §11.2/§11.3）：
- EdgeEvaporation：Deegan 接触线蒸发增强的板阵列工程近似——随离边距离指数衰减，
  特征长度 ~1 孔（自由液滴理论已核实；孔阵列衰减长度为工程假设）；
- ThermalGradient：近似线性中心-边缘梯度（与蒸发机制不同，分开建模）；
- Glare / DustNucleation：Huber ε-contamination（惯例 ε=5–20%）；
- BatchShift：批次随机截距惯例（信号 SD 的 10–30%）；
- InstrumentDrift：AR(1)（φ≈1 弱均值回复）轮内漂移为默认、linear 作对照形式；
  resident 为真实仪器四分量跨轮持久漂移（老化趋势+会话间随机游走+会话内 AR(1)+温周期，
  确定性于 (seed,round) 故 resume 等价，见类 docstring；R2 §1.1 修跨轮检出恒 0 的病灶）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from expos.adapters.base import AdapterError


@dataclass(frozen=True)
class WellContext:
    """注入器可见的单孔上下文（只读）。"""

    well_id: str
    row: int
    col: int
    rows: int
    cols: int
    is_edge: bool
    block_id: str
    solution_batch: str
    capture_index: int
    round_id: int


class Injector:
    name: str = "base"

    def apply(
        self, value: float, ctx: WellContext, rng: np.random.Generator
    ) -> tuple[float, bool, str]:
        """返回 (测量值', 是否命中, 标签)。子类实现；基类拒绝直接使用。"""
        raise NotImplementedError


@dataclass
class EdgeEvaporation(Injector):
    """边缘蒸发增强：measured *= 1 + strength·exp(−d_edge/decay_wells)。
    d_edge = 到最近板边的孔数；方向为**抬高**读数（浓缩沉积被成像误读为高质量，
    工程近似——正是"假最优"的来源）。"""

    strength: float = 0.25
    decay_wells: float = 1.0
    max_range_wells: int = 1  # 只作用最外 1+max_range 圈——边界层限于板边附近（工程近似）
    name: str = "edge_evaporation"

    def apply(self, value, ctx, rng):
        d = min(ctx.row, ctx.col, ctx.rows - 1 - ctx.row, ctx.cols - 1 - ctx.col)
        if d > self.max_range_wells:
            return value, False, self.name
        boost = self.strength * float(np.exp(-d / self.decay_wells))
        return value * (1.0 + boost), True, self.name


@dataclass
class ThermalGradient(Injector):
    """线性温度梯度：沿 axis 从 −magnitude/2 到 +magnitude/2 的乘性偏差。"""

    axis: str = "row"  # row | col
    magnitude: float = 0.15
    name: str = "thermal_gradient"

    def apply(self, value, ctx, rng):
        if self.axis == "row":
            pos, n = ctx.row, ctx.rows
        elif self.axis == "col":
            pos, n = ctx.col, ctx.cols
        else:
            raise AdapterError(f"ThermalGradient 未知 axis: {self.axis!r}")
        frac = pos / (n - 1) if n > 1 else 0.5
        return value * (1.0 + self.magnitude * (frac - 0.5)), True, self.name


@dataclass
class Glare(Injector):
    """成像眩光：ε-contamination——以 prob 命中，读数虚高 boost 倍数。
    命中孔的 exposure 证据由模拟器同步抬高（QC 层的线索，非真值泄漏）。"""

    prob: float = 0.1
    boost: float = 0.35
    name: str = "glare"

    def apply(self, value, ctx, rng):
        if rng.random() < self.prob:
            return value * (1.0 + self.boost), True, self.name
        return value, False, self.name


@dataclass
class DustNucleation(Injector):
    """灰尘诱导额外成核：以 prob 命中，多而小的晶体使质量读数下降 drop 比例。"""

    prob: float = 0.05
    drop: float = 0.4
    name: str = "dust_nucleation"

    def apply(self, value, ctx, rng):
        if rng.random() < self.prob:
            return value * (1.0 - self.drop), True, self.name
        return value, False, self.name


@dataclass
class BatchShift(Injector):
    """溶液批次随机截距：命中 batch_suffix 的批次整体乘性偏移 shift（可正可负）。"""

    batch_suffix: str = "B1"
    shift: float = -0.15
    name: str = "batch_shift"

    def apply(self, value, ctx, rng):
        if ctx.solution_batch.endswith(self.batch_suffix):
            return value * (1.0 + self.shift), True, self.name
        return value, False, self.name


@dataclass
class InstrumentDrift(Injector):
    """仪器时间漂移。三种 mode:

    - ``mode="ar1"``（默认，行为不变）：d_t = phi·d_{t−1} + sigma·N(0,1)，按 capture_index
      顺序推进**轮内**状态（模拟器保证按序调用）；每轮由 injectors_for_round 建新实例 →
      状态每轮从 0 重启 → 实为"轮内 AR(1)"（跨轮不持久，与冻结基线正交，故跨轮 CUSUM
      对其恒 0 漏检——诚实盲区，见 test_resident_within_ar1_blind）。
    - ``mode="linear"``（对照，行为不变）：d_t = rate·capture_index。
    - ``mode="resident"``（R2 §1.1 新增，真实仪器四分量漂移）：
        multiplier = 1 + [ aging(r) + rw_baseline(r) + ar1_within(t) + periodic(r) ]
        · aging(r)   = rate_per_round · round_id            —— 确定性老化趋势（检测器/离子源灵敏度单调衰减，Grotti 2003）
        · rw_baseline(r)= sigma_between · Σ_{i≤r} N_i        —— 会话间随机游走级位漂移（重校准/换柱，van der Kloet 2009；Wehrens 2016）
        · ar1_within(t)= phi·prev + sigma·N(0,1)（每轮围绕当前 baseline 重启）—— 会话内自相关漂移（QC-RLSC，Dunn 2011）
        · periodic(r) = period_amp · sin(2π·round_id/period_rounds)  —— 可选日温周期（period_rounds=0 关闭；Rutan 1991）
      幅度锚点见 research_theory_drift.md ②节 / REFERENCE_MAP §11.3。

    跨轮状态持久 —— 采**确定性-按-(seed,round) 复原**而非序列化可变状态（守公理 6 + resume 等价）:
      · aging/periodic 是 round_id 的纯函数；
      · rw_baseline 由**独立稳定种子** rw_seed 播种的 Generator，每轮重抽 round_id+1 个正态取累和
        （同 (rw_seed, round_id) 恒同 → 真随机游走轨迹跨轮一致），**不依赖逐轮 exec-rng、不需实例跨轮持有**；
      · ar1_within 每轮围绕 baseline 重启、由 loop 逐轮 default_rng(derive_seed(seed,"exec",round_id))
        的 exec-rng 驱动（该 rng 本身确定性于 (seed,round)）。
      ⇒ 第 r 轮漂移只依赖 (round_id, rw_seed, 该轮 exec-rng)，全部确定性于 (seed,round)，
        故"跑 4 轮"与"跑 2 轮+resume 2 轮"逐孔测量值恒等（test_resident_resume_equivalence），
        无需把漂移真值写进 store/observation（公理 6：漂移真值全程只落 sim_base 的 truth_records）。
      本方案较"实例持有可变状态 + 序列化进 truth/drift_state.json + loop resume 重建"更干净：零 loop 改动、
      零新 store、resume 等价由确定性直接保证（研究笔记 ②节明示"按 (scenario,seed) 确定性也可"、建议按种子推进）。
    """

    mode: str = "ar1"
    phi: float = 0.95
    sigma: float = 0.01
    rate: float = -0.004
    # resident 专属四分量参数（对 ar1/linear 无效——旧模式行为零变化）：
    rate_per_round: float = 0.0      # aging 斜率 a（每轮乘性偏移，如 −0.02=−2%/会话）
    sigma_between: float = 0.0       # 会话间随机游走步长 sd（如 0.02=±2%/会话级位跳）
    period_rounds: float = 0.0       # 温周期长度（轮），0=关闭
    period_amp: float = 0.0          # 温周期幅度
    #: 会话间随机游走的稳定种子（跨轮确定性复原用）。
    #: 【RES3 P2 已知局限】默认值=固定 fixture：所有 run/档位共用同一游走轨迹实现
    #: （该轨迹为单侧正向平台，会与负 aging 部分相消；跨 seed MC 无法平均游走实现）。
    #: 正解=从 run seed 派生（derive_seed(seed,"rw")），但 adapter 现无 run seed 通道
    #: （execute 只收 rng），属 API 变更记 Backlog；场景 yaml 可显式逐档设不同 rw_seed。
    rw_seed: int = 20240607
    applied_eps: float = 0.005       # resident 的 applied 判据阈（替 1e-9，消全亮标签污染）
    name: str = "instrument_drift"
    _state: float = field(default=0.0, repr=False)
    _last_index: int = field(default=-1, repr=False)

    def resident_baseline(self, round_id: int) -> float:
        """resident 的**轮级**漂移基线（aging + 会话间随机游走 + 周期）——round_id 的纯函数。

        跨轮持久、确定性于 (rw_seed, round_id)：每次重抽前缀求累和即得一致的随机游走轨迹，
        故 resume 后同一 round_id 恒得同基线（无需序列化）。轮内 AR(1) 分量不含在此。
        """
        if round_id < 0:
            raise AdapterError(f"resident_baseline 需 round_id≥0，收到 {round_id}")
        aging = self.rate_per_round * round_id
        rw_baseline = 0.0
        if self.sigma_between != 0.0:
            rw_rng = np.random.default_rng([int(self.rw_seed), 0xD817])
            steps = rw_rng.standard_normal(round_id + 1)
            rw_baseline = self.sigma_between * float(np.sum(steps))
        periodic = 0.0
        if self.period_rounds > 0.0:
            # 【RES3 P3 备注】默认 period_rounds=24 时 8 轮窗只覆盖 1/4 周期——
            # 窗内为准线性升温段而非完整循环（无害，名不副实处如实记）。
            periodic = self.period_amp * float(
                np.sin(2.0 * np.pi * round_id / self.period_rounds)
            )
        return aging + rw_baseline + periodic

    def apply(self, value, ctx, rng):
        if self.mode == "linear":
            drift = self.rate * ctx.capture_index
        elif self.mode in ("ar1", "resident"):
            if ctx.capture_index <= self._last_index:
                raise AdapterError(
                    f"InstrumentDrift({self.mode}) 要求按 capture_index 严格递增调用"
                )
            steps = ctx.capture_index - self._last_index
            for _ in range(steps):
                self._state = self.phi * self._state + self.sigma * float(rng.standard_normal())
            self._last_index = ctx.capture_index
            if self.mode == "resident":
                drift = self.resident_baseline(ctx.round_id) + self._state
            else:  # ar1：轮内 AR(1)，跨轮不持久（旧行为）
                drift = self._state
        else:
            raise AdapterError(f"InstrumentDrift 未知 mode: {self.mode!r}")
        # applied 判据（RES3 P1 修订，mailbox/red_to_blue/007）：resident 只看**跨轮
        # 基线**分量 |resident_baseline(round)|>applied_eps——轮内 AR(1) 瞬态（稳态
        # sd≈0.032 ≫ eps=0.005）若计入总和，标签地板被抬到 ~0.85 且与档位无关，
        # training_injected 剂量响应被抹平（纯轮内 AR(1) 变体 applied 应恒 False）。
        # 测量值 drift 仍含全部分量（标签≠效应）；ar1/linear 保留 1e-9 行为零变化。
        if self.mode == "resident":
            applied = abs(self.resident_baseline(ctx.round_id)) > self.applied_eps
        else:
            applied = abs(drift) > 1e-9
        return value * (1.0 + drift), applied, self.name


# ---------------------------------------------------------------- 工厂与场景

INJECTOR_REGISTRY: dict[str, type[Injector]] = {
    "edge_evaporation": EdgeEvaporation,
    "thermal_gradient": ThermalGradient,
    "glare": Glare,
    "dust_nucleation": DustNucleation,
    "batch_shift": BatchShift,
    "instrument_drift": InstrumentDrift,
}


def build_injector(name: str, params: dict[str, Any] | None = None) -> Injector:
    """未知注入器名/非法参数一律响亮失败。"""
    if name not in INJECTOR_REGISTRY:
        raise AdapterError(
            f"未知注入器: {name!r}（可用: {sorted(INJECTOR_REGISTRY)}）"
        )
    try:
        return INJECTOR_REGISTRY[name](**(params or {}))
    except TypeError as e:
        raise AdapterError(f"注入器 {name!r} 参数非法: {e}") from e


def validate_scenario(scenario: list[dict[str, Any]] | None) -> None:
    """域配置加载时的场景预检（每条试构造一次），拼错的注入器名在加载期就炸。"""
    for item in scenario or []:
        unknown = set(item) - {"round", "injector", "params"}
        if unknown:
            raise AdapterError(f"artifact_scenario 含未知键: {sorted(unknown)}")
        if "injector" not in item:
            raise AdapterError(f"artifact_scenario 条目缺 injector: {item}")
        build_injector(item["injector"], item.get("params"))


def injectors_for_round(
    scenario: list[dict[str, Any]] | None, round_id: int
) -> list[Injector]:
    """取本轮生效的注入器（每次调用返回**新实例**——drift 内部状态按轮重置）。
    条目缺 round 键 = 每轮都生效。"""
    active = []
    for item in scenario or []:
        rd = item.get("round")
        if rd is None or rd == round_id:
            active.append(build_injector(item["injector"], item.get("params")))
    return active
