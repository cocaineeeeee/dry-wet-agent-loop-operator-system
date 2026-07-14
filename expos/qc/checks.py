"""M5 三级 QC 检查器（docs/M5_DESIGN.md §2；配置 REFERENCE_MAP §11.4 / §14）。

三级检查（lazy 收集器：跑全部、每观测聚合成 QCReport，绝不首错中断）：

每检查块受保护（stop-the-bleed）：某检查计算崩溃 → 转 error-evidence 记录
（`QCCheck(passed=False, score=0.0, evidence={"error", "check_crashed": True})` +
进 flags + `logging.error`），既可见可审计又不劫持 max-fold 裁决——ERROR kind
（EVIDENCE_TYPING）落地前的过渡语义（见 `_crashed_check`）。

  * hard        —— 缺失/NaN/超量程、曝光/照度越界（逐观测，快失败也只是同一路径加短路）；
  * reference   —— 哨兵 vs 期望带、哨兵跨轮控制带（冷启动 record-only）、副本组 CV + 组内离群；
  * structural  —— 先 median polish 去趋势，再在残差上做：边缘配对、行/列梯度、Moran 筛查、
                    批次位移、时间漂移（单轮 record-only；跨轮哨兵 CUSUM 主判）、
                    glare 曝光通道、dust 颗粒通道。

设计红线（本层严守）：
  * 只消费 ObservationObject 的 OS 可见面（value/secondary/layout_meta/material_meta/
    instrument_meta）——**不读仿真 sidecar、不 import planner/agent/models**（依赖隔离）；
  * 逐孔归属：板级检查只给被牵连孔打分（边缘→d_edge≤1 且残差同向；批次→该批次孔；
    glare→曝光超标孔），不搞全板连坐——QC 税红线 ≤5%；
  * 嫌疑分统一校准到 [0,1]，跨检查取 max 写进 QCReport.suspicion（保守）；
  * 冷启动纪律：哨兵历史 <3 轮（控制带与漂移控制图同门槛）、时间漂移单轮 →
    armed=False（写 evidence 不产 suspicion）。

FPR 控制取舍（记入偏离）：REFERENCE_MAP §14 给的是特定配对估计量的绝对地板（edge>0.011、
batch t>0.35）。本实现把这些绝对量作**地板**，再叠加与板级噪声尺度挂钩的显著性 z 门限
（z>3，FPR≈0.3%/板），以在稀疏板（48 格仅 ~17 填充）上稳住 ≤5% 的 QC 税。
"""

from __future__ import annotations

import logging
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np


@contextmanager
def _quiet_nan_warnings():
    """静音稀疏网格 nanmedian 的 "All-NaN slice" 良性告警（全列 NaN 时预期发生）。"""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        warnings.filterwarnings("ignore", message="Mean of empty slice")
        yield

from expos.errors import ExposError
from expos.kernel.objects import (
    ExperimentObject,
    ObservationObject,
    QCCheck,
    QCReport,
)
from expos.qc.stats import (
    StatsError,
    cusum,
    median_polish,
    moran_permutation,
    queen_w,
    sbb_suspicion,
)

_E = float(np.e)

# ---- 阈值与校准常数（§11.4 / §14；显著性门限统一 3σ 以控 FPR，见模块 docstring）----
METRIC_RANGE_DEFAULT = (0.0, 1.2)
EXPOSURE_LO, EXPOSURE_HI = 0.3, 2.0        # 曝光/照度物理有效带（越界即 hard 失败）
# Edge floor — SCALE-AWARE (M24-B abstraction fix, letter 147). §14 gives ABSOLUTE floors on
# the edge paired-diff statistic (edge>0.011 family), empirically retuned here to
# FIRE/FULL = 0.018/0.045 for this domain's noise_sd=0.02: on a sparse board the robust scale is
# polluted by the artifact itself (edge hits most filled wells), so we use an empirical absolute
# floor + full-score point, and _ramp keeps the score ~0 near the floor to control FPR. Measured
# (30 seeds): edge statistic (d==0 vs d>=1) clean std~0.008, edge strength0.5~0.047.
#   THE LEAK (letter 147 §2): 0.018/0.045 are in RAW METRIC UNITS, implicitly calibrated to the
# chemistry measurement scale (metric_range span ~1.2 a.u., noise_sd~0.02). A domain that
# normalizes readouts to a DIFFERENT scale (biology percent-of-control, span 200) sees the same
# spatial noise amplified ~167x, so this absolute floor mis-fires (0.045 > 0.018 -> score 1.0),
# collapsing certification power. FIX: express the floor as a FRACTION of the metric span so it
# tracks the measurement scale (domain-neutral). The fraction is DERIVED from the chemistry
# calibration itself (_EDGE_REFERENCE_SPAN below), so at the reference span the effective floor
# equals the historical absolute value EXACTLY (byte-identical chemistry); at span 200 it scales
# up ~167x. The effective edge_fire/edge_full are derived from metric_range inside run_qc.
_EDGE_REFERENCE_SPAN = 1.2                    # chemistry metric_range span the §14 floors were calibrated on
EDGE_FIRE, EDGE_FULL = 0.018, 0.045          # effective floors AT the reference span (§14 grandfathered magnitudes)
# 批次位移：**身份无关**的乘性斜率估计量 shift_hat（见 batch 检查处的结构性教训注释）。
# M6 把批次改成空间棋盘格 (row+col)%n 后，旧的"去身份残差逐批组均值差"路径被结构性削弱
# ——同一候选的副本若同奇偶落同批，批次位移被候选去身份均值吸收；棋盘格下 straddle 率仅约
# 一半、且候选值域偏低（sobol 早期候选 quality_index≈0.05–0.2），绝对量纲的批组差信噪太弱。
# 改用逐 identity 的跨批对比 d_i=(B_hi−B_lo) 对 level_i 过原点加权回归 shift_hat=Σl·d/Σl²
# （量纲即乘性 shift，值域无关；哨兵作为同参数 identity 天然入池），配 WLS z。实测（20 种子
# 近满板 45 孔）：clean shift_hat +0.009±0.034、−0.18 −0.190±0.034 且 |z|>3 全中；17 孔玩具板
# （6 低值候选、仅 4 个跨批对）低于批次探测信息地板（记入交付触发表，诚实漏检）。
BATCH_MAG_FIRE, BATCH_MAG_FULL = 0.12, 0.20   # |shift_hat| 触发/满分点（score 与 flag 解耦控 QC 税）
BATCH_Z_FIRE = 3.0                             # WLS 显著性门限（近满板 −0.18 |z|≈4–10）
BATCH_MIN_PAIRS = 2                            # 跨批 identity 对最小守卫（<2 → record-only 诚实降级）
# 选批方向（R3 §1.1 P0 修复）：两批棋盘格下 batch_shifts 两批精确相反数（IEEE），旧
# max(key=abs) 恒平局落插入序首个（永远 B0=干净批）→ 100% 判反。改双锚定向：
#   主锚=哨兵绝对参考（每批哨兵均值 vs 冻结 expected_band 中心）——可分辨升高/降低型污染；
#   回退锚=shift_hat 符号（含定向信息，天然打破平局）——前提"低=异常"，见 _batch_fallback_pick。
SENTINEL_MIN_PER_BATCH = 2                      # 主锚每批哨兵最小数，<2 → 弃权改回退锚
SENTINEL_DIFF_Z = 2.0                           # 跨批哨兵均值差显著性门限（替代过宽 sentinel_band 的选批用途）
GRAD_FIRE = 4.0                             # 梯度斜率 t 触发门限（clean 板 |t| 实测≤3.3，留裕度）
GRAD_Z_FULL = 8.0
GRAD_CAP = 0.40                             # 梯度单轮弱嫌疑上限（主判警交跨轮，§14）
MORAN_CAP = 0.50                            # Moran 仅作筛查权重
GLARE_EXPOSURE = 1.25                       # §14 首选通道，FPR≈0
GLARE_SCORE = 0.90
DUST_RATIO_LO, DUST_RATIO_HI = 1.8, 3.0     # 同候选副本 grain_count 比值
CV_THRESH = 0.15                            # 副本组 CV 阈（离群闸门；分基于绝对 z）
REPLICATE_Z_FIRE, REPLICATE_Z_FULL = 4.0, 8.0   # 组内离群相对板噪声尺度（绝对量纲，防低值 CV 爆分）
SENTINEL_MIN_ROUNDS = 3                     # 控制带/漂移控制图 armed 所需最少历史轮数
# 跨轮时间漂移控制图（M5_DESIGN §2 cusum(k=0.5)；h 自规格 5 保守上调到 6——20 种子
# 干净多轮实测 armed 轮假警率 h=5 时 3.8%、h=6 时 2.5%，取 6 给 QC 税红线留裕度）：
# 哨兵轮均值序列对**冻结基线**（Phase-I：target 与 se 皆取 armed 前 3 轮，SPC 惯例——
# se 若滚动估计会被漂移轮自身散布抬大、信号自吞）跑双侧 CUSUM；z winsorize ±4 +
# "当前轮自身偏离 ≥1"闸（两者合力使单轮事件如 edge 的哨兵残影单独无法越 h，故不设
# edge 抑制、只记 edge_concurrent 证据——持续多轮的基线游走不该被并发检查静音）。
DRIFT_CUSUM_K, DRIFT_CUSUM_H = 0.5, 6.0
DRIFT_STAT_FULL = 9.0                       # score 满分点（_ramp(stat, h, full)）
DRIFT_Z_CLIP = 4.0                          # 单轮 z 贡献上限（winsorize）
DRIFT_Z_CURRENT = 1.0                       # 触发须当前轮自身偏离 ≥1（防陈年告警拖尾）
DRIFT_CAP = 0.40                            # 弱嫌疑上限（板级全局效应，主判警交跨轮累积）


class CheckError(ExposError):
    """QC 检查层的响亮失败（配置缺失、退化输入等）。lazy 收集器会捕获并降级为 record-only。"""


# ---------------------------------------------------------------- 跨轮哨兵容器

class QCHistory:
    """极简跨轮哨兵值容器：每轮一组哨兵测量值，供控制带冷启动判断与跨轮漂移控制图。

    ``append_round(sentinel_values)`` 追加一轮；``rounds`` 返回各轮值列表（只读拷贝）。
    """

    def __init__(self) -> None:
        self._rounds: list[list[float]] = []

    def append_round(self, sentinel_values: Any) -> None:
        vals = [float(v) for v in sentinel_values if v is not None and np.isfinite(v)]
        self._rounds.append(vals)

    @property
    def rounds(self) -> list[list[float]]:
        return [list(r) for r in self._rounds]


# ---------------------------------------------------------------- 板级上下文

@dataclass(frozen=True)
class PlateContext:
    """板级上下文（M6 归因直接消费——对抗审查要求的显式结构，OS 可见量一次性物化）。"""

    round_id: int
    residual_grid: np.ndarray               # 6×8 median polish 去趋势残差（NaN 孔保 nan）
    row_effects: np.ndarray
    col_effects: np.ndarray
    edge_paired_diff: float                  # 边缘−中心 候选去身份残差配对差
    gradient_slope_t: float                  # 行/列效应线性趋势 t（取主导轴）
    moran: dict[str, float]                  # {"I","EI","p_sim","z_sim"} 或 {}（退化）
    batch_shifts: dict[str, float]           # solution_batch -> 该批 vs 其余的标准化 t
    drift_corr: float                        # 残差–capture_index 相关
    sentinel_zscores: dict[str, float]       # well_id -> 哨兵稳健 z
    d_edge: dict[str, int]                   # well_id -> 到最近板边的孔数
    resid_scale: float = 0.0                 # 候选组残差稳健尺度（供 M6 τ_r）


# ================================================================ 小工具

def _finite(v: Any) -> bool:
    return v is not None and isinstance(v, (int, float)) and np.isfinite(v)


def _ramp(x: float, lo: float, hi: float) -> float:
    """线性斜坡：x≤lo→0，x≥hi→1，中间线性；退化区间时阶跃。"""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def _robust_scale(x: np.ndarray) -> float:
    """稳健尺度 1.4826·MAD；退化时回退 std，再回退极小正数（避免除零）。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return 1e-9
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    if mad > 0:
        return float(1.4826 * mad)
    sd = float(np.std(x))
    return sd if sd > 0 else 1e-9


def _sbb_cal(p: float) -> float:
    """SBB 校准嫌疑分（§2.2）：p≥1/e 夹取为 0，否则 1−2·α(p)，单调随 p↓ 而↑，∈[0,1]。

    stats.sbb_suspicion 返回原始 α(p)∈[0,0.5]（p=1/e 处峰 0.5）；直接 1−α 会有 0.5 地板
    淹没 z 类分并抬爆 QC 税，故线性拉伸到 [0,1] 并在弱证据尾区（p≥1/e）夹取。
    """
    p = float(p)
    if not (0.0 < p < 1.0):
        return 0.0
    if p >= 1.0 / _E:
        return 0.0
    alpha = sbb_suspicion(p)
    return float(max(0.0, 1.0 - 2.0 * alpha))


def _slope_t(y: np.ndarray) -> float:
    """y 对其自身整数索引 [0..n-1] 的最小二乘斜率 t 统计量；n<3 / 退化 → 0。"""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    n = y.size
    if n < 3:
        return 0.0
    x = np.arange(n, dtype=float)
    x -= x.mean()
    sxx = float(x @ x)
    if sxx == 0:
        return 0.0
    slope = float((x @ (y - y.mean())) / sxx)
    resid = y - (y.mean() + slope * x)
    dof = n - 2
    s2 = float(resid @ resid) / dof if dof > 0 else 0.0
    if s2 <= 0:
        return 0.0
    se = np.sqrt(s2 / sxx)
    return float(slope / se) if se > 0 else 0.0


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3:
        return 0.0
    a, b = a[m], b[m]
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _d_edge(row: int, col: int, rows: int, cols: int) -> int:
    return int(min(row, col, rows - 1 - row, cols - 1 - col))


def _history_rounds(history: Any) -> list[list[float]]:
    """把 history 归一化为"每轮哨兵值列表"。

    容忍三种入参：QCHistory（用 .rounds）、ObservationObject 列表（policy 传的既往观测，
    按 round_id 分组取 is_control 的 value）、或 None。
    """
    if history is None:
        return []
    if hasattr(history, "rounds"):
        return [list(r) for r in history.rounds]
    # 观测对象列表：按轮聚合哨兵值
    by_round: dict[int, list[float]] = {}
    try:
        for o in history:
            if getattr(o, "is_control", False) and _finite(getattr(o.result, "value", None)):
                by_round.setdefault(int(o.round_id), []).append(float(o.result.value))
    except TypeError:
        return []
    return [by_round[k] for k in sorted(by_round)]


# ---------------------------------------------------------------- 选批方向双锚（R3 §1.1 P0）

def _batch_fallback_pick(batch_shifts: dict[str, float], shift_hat: float) -> str | None:
    """回退锚：用 shift_hat **符号**给"哪批异常"定向（哨兵不足/不显著时用）。

    前提"低=异常"（降低型污染，本域主要注入形态）：``shift_hat < 0`` ⇒ 异常批 = 残差组均值
    最低批（``argmin batch_shifts``）。镜像逻辑（升高型污染）：``shift_hat > 0`` ⇒ 异常批 =
    最高批（``argmax``）。**为何用符号而非 abs**：两批空间棋盘格下 ``batch_shifts`` 两批精确
    相反数（IEEE），``abs`` 恒平局、``max`` 落插入序首个（永远选中干净批 B0）；而 ``shift_hat``
    是身份无关 WLS 斜率、带定向信息（实测 −0.19），读其符号即天然打破平局。``shift_hat≈0``
    （无方向）或退化 → None（交由主锚或降级）。升高型污染须靠主锚哨兵绝对参考才能分辨，此
    回退锚在纯升高场景会判反——故仅作哨兵不可用时的过渡锚，冲突时响亮降级 record-only。
    """
    if not batch_shifts or not np.isfinite(shift_hat) or shift_hat == 0.0:
        return None
    if shift_hat < 0:
        return min(batch_shifts, key=lambda b: batch_shifts[b])
    return max(batch_shifts, key=lambda b: batch_shifts[b])


def _batch_sentinel_pick(
    sent_devs_by_batch: dict[str, list[float]], half_width: float
) -> tuple[str | None, str]:
    """主锚：哨兵绝对参考。每批哨兵均值 vs 冻结 target（各哨兵 expected_band 中心，已折算为
    deviation，target=0），偏离显著侧 = 异常批。返回 (picked_batch|None, reason)。

    区别于过宽的 ``sentinel_band`` in/out 判据（[0.05,0.45] 对 −18% 位移仍在带内、选批无力）：
    这里用**跨批哨兵均值差的显著性**（相对哨兵池尺度的标准化差 ≥ ``SENTINEL_DIFF_Z``）判是否
    可选批，并用"离 target 更远者"定异常批——可分辨升高/降低型污染（不依赖"低=异常"前提）。

    弃权（返回 None，交回退锚）三种：① 有效批（每批哨兵 ≥ ``SENTINEL_MIN_PER_BATCH``）<2；
    ② target 不可信——作为干净参照的次偏离批本身出带（|dev|>half_width，说明冻结 target 偏离真
    值、绝对参考不成立）；③ 跨批差不显著（哨兵太少/太噪，标准化差 < 门限）。
    """
    usable = {b: np.asarray(v, float) for b, v in sent_devs_by_batch.items()
              if len(v) >= SENTINEL_MIN_PER_BATCH}
    if len(usable) < 2:
        return None, "sentinel_insufficient"
    means = {b: float(v.mean()) for b, v in usable.items()}
    # 最偏离 target 者=异常候选，次偏离者=干净参照
    order = sorted(usable, key=lambda b: abs(means[b]), reverse=True)
    hi, lo = order[0], order[1]
    if abs(means[lo]) > max(half_width, 0.0):
        return None, "target_unreliable"          # 干净参照批也出带 → target 不可信
    # 组**内**残差尺度（去各批均值后合并）——绝不用跨批合并 std，否则批间分离本身抬大尺度、自吞信号
    within = np.concatenate([usable[b] - means[b] for b in usable])
    dof = max(within.size - len(usable), 1)
    sd = float(np.sqrt(float(within @ within) / dof)) if within.size > len(usable) else 0.0
    if sd <= 0:
        sd = max(half_width, 1e-9)                 # 方差退化 → 保守用带半宽
    se = sd * float(np.sqrt(1.0 / usable[hi].size + 1.0 / usable[lo].size))
    z = abs(means[hi] - means[lo]) / se if se > 0 else 0.0
    if z < SENTINEL_DIFF_Z:
        return None, "sentinel_not_significant"
    return hi, "sentinel"


def _batch_select(
    sent_devs_by_batch: dict[str, list[float]], half_width: float,
    batch_shifts: dict[str, float], shift_hat: float,
) -> tuple[str | None, dict[str, Any]]:
    """双锚合成"哪批异常"：主锚（哨兵绝对参考）优先，回退锚（shift_hat 符号）兜底，两锚冲突
    响亮降级 record-only（不选批、不连坐，诚实不猜）。返回 (fired_batch|None, evidence_detail)。

    * 主锚出批 + 回退缺席/同意 → 选主锚；
    * 主锚弃权 + 回退出批 → 选回退锚；
    * 主锚与回退出批但**不一致** → None（``conflict_record_only``）；
    * 两锚皆无 → None（``no_anchor``）。
    """
    sent_pick, sent_reason = _batch_sentinel_pick(sent_devs_by_batch, half_width)
    fb_pick = _batch_fallback_pick(batch_shifts, shift_hat)
    if sent_pick is not None and fb_pick is not None and sent_pick != fb_pick:
        top_b, anchor = None, "conflict_record_only"
    elif sent_pick is not None:
        top_b = sent_pick
        anchor = "sentinel_and_fallback_agree" if fb_pick == sent_pick else "sentinel"
    elif fb_pick is not None:
        top_b, anchor = fb_pick, "shift_hat_sign"
    else:
        top_b, anchor = None, "no_anchor"
    detail = {"select_anchor": anchor, "sentinel_pick": sent_pick,
              "sentinel_reason": sent_reason, "fallback_pick": fb_pick}
    return top_b, detail


# ================================================================ 主入口

def _crashed_check(name: str, level: str, evidence: dict[str, Any]) -> QCCheck:
    """Downgrade a crashed check to explicit error-evidence instead of letting the
    exception tear down the whole QC round.

    Transitional semantics until the ERROR evidence kind (EVIDENCE_TYPING) lands:
    ``score`` stays at 0.0 (record-only) so the crash neither hides (it is surfaced via
    ``evidence.check_crashed`` and the flag list) nor hijacks the max-fold suspicion and
    force-isolates the well. ``passed=False`` guarantees ``name`` reaches
    ``QCReport.flags`` (see the flags comprehension in the aggregation loop). This mirrors
    the repo norm of promoting a failed computation to a loud, auditable record rather
    than silently swallowing the error.
    """
    return QCCheck(
        name=name, level=level, passed=False, score=0.0,
        evidence={**evidence, "check_crashed": True},
    )


def run_qc(
    exp: ExperimentObject,
    obs_list: list[ObservationObject],
    history: Any | None = None,
    seed: int = 0,
    metric_range: tuple[float, float] = METRIC_RANGE_DEFAULT,
    moran_perm: int = 9999,
) -> tuple[dict[str, QCReport], PlateContext]:
    """三级 QC 检查：逐观测产 QCReport（suspicion=跨检查 max），并物化板级 PlateContext。

    lazy 收集器：所有板级统计与逐观测检查各自 try/except，单点退化只降级为 record-only
    证据，绝不中断其他检查（一个 hard 失败不阻止其余检查跑）。
    """
    if exp.layout is None:
        raise CheckError(f"exp {exp.exp_id} 无 layout，无法 QC")
    rows, cols = exp.layout.rows, exp.layout.cols

    # ---- Scale-aware edge floor (M24-B, letter 147) ----
    # Express the §14 absolute edge floor as a fraction of the measurement span so it tracks the
    # readout scale (domain-neutral). At the chemistry reference span (1.2) the factor is exactly
    # 1.0 -> effective floor == the historical 0.018/0.045 (byte-identical); a normalized readout
    # on a wider span (bio percent-of-control, span 200) scales the floor up proportionally so
    # drift-amplified spatial noise no longer mis-fires, while a genuinely large edge artifact at
    # that scale still trips it. All the OTHER structural checks (batch shift_hat = multiplicative
    # slope, row/col gradient t-stat, drift/replicate z-scores, Moran) are already dimensionless
    # and thus scale-invariant; edge is the sole absolute-metric-scale floor.
    lo_m, hi_m = metric_range
    metric_span = float(hi_m) - float(lo_m)
    _edge_scale = metric_span / _EDGE_REFERENCE_SPAN if metric_span > 0 else 1.0
    edge_fire = EDGE_FIRE * _edge_scale
    edge_full = EDGE_FULL * _edge_scale

    # ---- 布局与观测映射 ----
    well_pos: dict[str, tuple[int, int]] = {}
    d_edge: dict[str, int] = {}
    for w in exp.layout.wells:
        well_pos[w.well_id] = (w.row, w.col)
        d_edge[w.well_id] = _d_edge(w.row, w.col, rows, cols)

    obs_by_well = {o.layout_meta.well_id: o for o in obs_list}
    control_band = {c.control_id: c.expected_band for c in exp.controls}
    control_params = {
        c.control_id: tuple(sorted((k, float(v)) for k, v in c.params.items()
                                   if isinstance(v, (int, float))))
        for c in exp.controls
    }

    # ---- 分组（去候选/去哨兵身份）：候选按 cand_id，控制按参数签名聚合 ----
    def group_key(o: ObservationObject) -> Any:
        if o.is_control:
            return ("CTRL", control_params.get(o.control_id, ()))
        return ("CAND", o.cand_id)

    groups: dict[Any, list[ObservationObject]] = {}
    for o in obs_list:
        groups.setdefault(group_key(o), []).append(o)

    group_mean: dict[Any, float] = {}
    for k, members in groups.items():
        vals = [o.result.value for o in members if _finite(o.result.value)]
        group_mean[k] = float(np.mean(vals)) if vals else float("nan")

    # ---- 候选去身份残差 resid_raw（保留空间结构、剥离候选真值差）----
    resid_raw: dict[str, float] = {}
    grid_raw = np.full((rows, cols), np.nan)
    for o in obs_list:
        wid = o.layout_meta.well_id
        gm = group_mean[group_key(o)]
        if _finite(o.result.value) and np.isfinite(gm):
            r = float(o.result.value) - gm
            resid_raw[wid] = r
            rr, cc = well_pos[wid]
            grid_raw[rr, cc] = r

    # ---- median polish 去趋势 → 行/列效应 + 去趋势残差 r_dt ----
    # 稀疏板（48 格仅 ~17 填充）上整列全 NaN 会触发 nanmedian 的良性 RuntimeWarning，静音
    try:
        with np.errstate(all="ignore"), _quiet_nan_warnings():
            mp = median_polish(grid_raw)
        row_effects = np.asarray(mp["row"], dtype=float)
        col_effects = np.asarray(mp["col"], dtype=float)
        detr = np.asarray(mp["residuals"], dtype=float)
    except StatsError:
        row_effects = np.zeros(rows)
        col_effects = np.zeros(cols)
        detr = grid_raw.copy()

    r_dt: dict[str, float] = {}
    for wid, (rr, cc) in well_pos.items():
        v = detr[rr, cc]
        if np.isfinite(v):
            r_dt[wid] = float(v)

    # 板噪声尺度取自 resid_raw（与 edge/batch/drift 用的量同尺度——否则 median polish
    # 在稀疏板上缩小残差、令 se 偏小把 t 抬爆，clean 板假警连坐整批）
    resid_scale = _robust_scale(
        np.array(list(resid_raw.values())) if resid_raw else np.array([0.0])
    )

    # ---- 跨轮哨兵历史（漂移控制图与控制带共用；冷启动纪律见各消费点）----
    hist_rounds = _history_rounds(history)

    # ============================================================ 板级结构检查
    # 每个检查返回 (per_well_scores, board_evidence)；每块受 try/except 保护：崩溃 →
    # error-evidence 记录（check_crashed），绝不让单检查异常直穿崩掉整轮 QC（见 _crashed_check）。

    # ---- 边缘配对（去身份残差：最外圈 d==0 vs 内部 d≥1，实证最优对比）----
    edge_scores: dict[str, float] = {}
    edge_paired_diff = 0.0
    # `fire`/`full` are the SCALE-AWARE effective floors (span-scaled from the §14 magnitudes);
    # `metric_span` + `fire_frac` are recorded for audit of the scale derivation.
    edge_ev: dict[str, Any] = {"fire": edge_fire, "full": edge_full, "fired": False,
                               "fire_frac": EDGE_FIRE / _EDGE_REFERENCE_SPAN,
                               "metric_span": metric_span}
    try:
        e_vals = np.array([resid_raw[w] for w in resid_raw if d_edge[w] == 0])  # lint: allow-domain-literal(P4 legacy: crystal plate edge-band width hardcode, grandfathered pending Domain Profile extraction (Q3))
        c_vals = np.array([resid_raw[w] for w in resid_raw if d_edge[w] >= 1])  # lint: allow-domain-literal(M5 legacy: edge-effect radius (d_edge<=1) is a crystal board-domain assumption, grandfathered pending Domain Profile extraction (Q3))
        if e_vals.size >= 1 and c_vals.size >= 2:
            edge_paired_diff = float(e_vals.mean() - c_vals.mean())
            fired = abs(edge_paired_diff) > edge_fire
            edge_ev.update({"diff": edge_paired_diff, "fired": bool(fired),
                            "n_edge": int(e_vals.size), "n_inner": int(c_vals.size)})
            if fired:
                s = _ramp(abs(edge_paired_diff), edge_fire, edge_full)
                for w in resid_raw:                # 牵连 d≤1（边缘蒸发注入器足迹）全部孔
                    if d_edge[w] <= 1:  # lint: allow-domain-literal(M5 legacy: edge-effect radius (d_edge<=1) is a crystal board-domain assumption, grandfathered pending Domain Profile extraction (Q3))
                        edge_scores[w] = s
    except (StatsError, ValueError) as exc:
        edge_ev["error"] = str(exc)
    edge_fired = bool(edge_ev.get("fired"))

    # ---- 行/列梯度（median polish 效应的线性趋势；单轮弱嫌疑，capped）----
    grad_scores: dict[str, float] = {}
    gradient_slope_t = 0.0
    grad_ev: dict[str, Any] = {"fired": False, "cap": GRAD_CAP}
    try:
        t_row, t_col = _slope_t(row_effects), _slope_t(col_effects)
        gradient_slope_t = t_row if abs(t_row) >= abs(t_col) else t_col
        grad_axis = "row" if abs(t_row) >= abs(t_col) else "col"
        grad_ev.update({"t_row": float(t_row), "t_col": float(t_col), "axis": grad_axis})
        if abs(gradient_slope_t) > GRAD_FIRE:
            grad_ev["fired"] = True
            s = min(GRAD_CAP, _ramp(abs(gradient_slope_t), GRAD_FIRE, GRAD_Z_FULL))
            eff = row_effects if grad_axis == "row" else col_effects
            thr = 2.0 * _robust_scale(eff)
            for w, (rr, cc) in well_pos.items():            # 牵连梯度极端行/列的孔
                idx = rr if grad_axis == "row" else cc
                if abs(eff[idx]) > thr:
                    grad_scores[w] = s
    except Exception as exc:  # noqa: BLE001 — one check must not crash the whole QC round
        logging.error("qc check 'row_col_gradient' crashed: %r", exc)
        grad_scores, gradient_slope_t = {}, 0.0
        grad_ev["error"] = repr(exc)
        grad_ev["check_crashed"] = True

    # ---- Moran 空间自相关（去趋势残差；筛查权重，score 不并入 suspicion）----
    moran_res: dict[str, float] = {}
    moran_ev: dict[str, Any] = {"role": "screening"}
    try:
        filled = sorted(rr * cols + cc for wid, (rr, cc) in well_pos.items() if wid in r_dt)
        drop = [i for i in range(rows * cols) if i not in filled]
        if len(filled) >= 3:
            W, keep = queen_w(rows, cols, drop=drop)
            y = detr.ravel()[keep]
            moran_res = moran_permutation(y, W, n_perm=moran_perm, seed=seed)
            moran_ev.update(moran_res)
            moran_ev["screen_score"] = min(MORAN_CAP, _sbb_cal(moran_res["p_sim"]))
    except StatsError as exc:
        moran_ev["error"] = str(exc)

    # ---- 批次位移（**身份无关** WLS 斜率 shift_hat；结构性教训见下）----
    #
    #   结构性教训（棋盘格 × 去身份残差的交互，务必留档）：M6 为把批次与 capture_index、
    #   边缘奇偶双解耦，把 solution_batch 改成空间棋盘格 R{r}-B{(row+col)%n}（M6 finding ②，
    #   修 29/40 误归因 batch_effect）。这一改动却结构性削弱了"在去身份残差 resid_raw 上比较
    #   批组均值"的旧路径：同一候选的两个副本若落同奇偶→同批，批次位移被该候选的去身份均值
    #   整体吸收，只有跨批（straddle）副本才留下痕迹；棋盘格下 straddle 率仅约一半、且早期
    #   sobol 候选值域偏低，令绝对量纲的批组差信噪太弱（实测 17 孔 ±0.0089 / 45 孔 ±0.0123，
    #   均 < 旧 FIRE 0.022 → 全不触发）。**教训：批次是乘性、身份相关的效应，必须用身份无关的
    #   估计器**——这里对每个 identity（候选按 cand_id；5 个同参数哨兵天然聚成一个 identity，即
    #   哨兵批差探针）取 within-identity 跨批对比 d_i=(mean B_hi − mean B_lo)，身份在 d_i 内严格
    #   消去；再对 level_i（该 identity 各批总均值）做过原点加权回归 shift_hat=Σ l·d/Σ l²，量纲
    #   即乘性 shift（值域无关，低值候选被 level² 自然降权、哨兵/高值候选主导），配 WLS 显著性 z。
    #   多估计器取证：shift_hat 定"是否触发/打分"，旧的逐批 resid_raw 组均值保留为第三证据并负责
    #   "判哪一批"（逐孔归属语义不变）。跨批对 <2 → record-only（诚实降级）。edge 触发时整段抑制。
    batch_scores: dict[str, float] = {}
    batch_shifts: dict[str, float] = {}     # batch -> 该批 vs 其余 的残差组均值差（归属 + 第三证据）
    batch_of: dict[str, str] = {}
    batch_ev: dict[str, Any] = {"mag_fire": BATCH_MAG_FIRE, "mag_full": BATCH_MAG_FULL,
                                "z_fire": BATCH_Z_FIRE, "fired": False}
    try:
        # 身份无关跨批对比（仅用跨批 identity；候选副本 + 哨兵组统一入池）
        levels: list[float] = []
        diffs: list[float] = []
        for k, members in groups.items():
            byb: dict[str, list[float]] = {}
            for o in members:
                b = o.material_meta.solution_batch
                if _finite(o.result.value) and b:
                    byb.setdefault(b, []).append(float(o.result.value))
            if len(byb) < 2:
                continue
            keys = sorted(byb)
            diffs.append(float(np.mean(byb[keys[-1]]) - np.mean(byb[keys[0]])))
            levels.append(float(np.mean([x for vv in byb.values() for x in vv])))
        # 旧路径：逐批 resid_raw 组均值（保留为第三证据 + 负责选被判批）
        by_batch: dict[str, list[float]] = {}
        for o in obs_list:
            wid = o.layout_meta.well_id
            b = o.material_meta.solution_batch
            if wid in resid_raw and b:
                by_batch.setdefault(b, []).append(resid_raw[wid])
                batch_of[wid] = b
        for b, vs in by_batch.items():
            va = np.array(vs)
            rest = np.array([v for bb, vv in by_batch.items() if bb != b for v in vv])
            if va.size >= 1 and rest.size >= 1:
                batch_shifts[b] = float(va.mean() - rest.mean())
        n_pairs = len(diffs)
        batch_ev.update({"n_pairs": n_pairs, "shifts": batch_shifts})
        if n_pairs >= BATCH_MIN_PAIRS:
            lev = np.array(levels)
            dd = np.array(diffs)
            denom = float(lev @ lev)
            if denom > 0:
                shift_hat = float((lev @ dd) / denom)
                resid = dd - shift_hat * lev
                dof = max(n_pairs - 1, 1)
                s2 = float(resid @ resid) / dof
                se = float(np.sqrt(s2 / denom)) if s2 > 0 else 0.0
                zc = float(shift_hat / se) if se > 0 else 0.0
                fired = (abs(shift_hat) >= BATCH_MAG_FIRE and abs(zc) >= BATCH_Z_FIRE
                         and not edge_fired)
                batch_ev.update({"shift_hat": shift_hat, "z": zc, "fired": bool(fired),
                                 "suppressed_by_edge": edge_fired})
                if fired and batch_shifts:
                    # 被判批：双锚定向（R3 §1.1 P0——旧 max(key=abs) 在棋盘格相反数下 100% 判反）。
                    # 主锚=哨兵绝对参考（每批哨兵均值 vs 冻结 expected_band 中心），可分辨升/降型污染；
                    # 回退锚=shift_hat 符号；两锚冲突 → 响亮降级 record-only（不选批、不连坐，诚实不猜）。
                    sent_devs_by_batch: dict[str, list[float]] = {}
                    half_widths: list[float] = []
                    for o in obs_list:
                        if not o.is_control or not _finite(o.result.value):
                            continue
                        band = control_band.get(o.control_id)
                        b = o.material_meta.solution_batch
                        if band is None or not b:
                            continue
                        lo_b, hi_b = band
                        center = (float(lo_b) + float(hi_b)) / 2.0
                        half_widths.append((float(hi_b) - float(lo_b)) / 2.0)
                        sent_devs_by_batch.setdefault(b, []).append(float(o.result.value) - center)
                    half_width = float(np.median(half_widths)) if half_widths else 0.0
                    top_b, detail = _batch_select(sent_devs_by_batch, half_width,
                                                  batch_shifts, shift_hat)
                    batch_ev.update(detail)
                    if top_b is not None:
                        batch_ev["fired_batch"] = top_b
                        s = _ramp(abs(shift_hat), BATCH_MAG_FIRE, BATCH_MAG_FULL)
                        for wid, bb in batch_of.items():
                            if bb == top_b:
                                batch_scores[wid] = s
        else:
            batch_ev["reason"] = "跨批 identity 对 <2 → record-only（棋盘格下证据不足，诚实降级）"
    except (StatsError, ValueError) as exc:
        batch_ev["error"] = str(exc)

    # ---- 时间漂移（单轮证据 record-only + 跨轮哨兵控制图 CUSUM 主判）----
    #
    #   结构性说明（压测 R1-2c 修复时留档）：本域执行面哨兵固定先采（capture 0..n-1）、
    #   同候选副本相邻成对 → 轮内 capture 斜坡在一切去身份对比里被结构性抵消（哨兵几乎
    #   不曝露、副本差分吃掉漂移——test_attribution"单轮不可辨识"的机理），故轮内
    #   corr 只能诚实 record-only。可判的是**跨轮基线游走**（仪器老化/标定漂移）：哨兵
    #   先采恰使其轮均值成为仪器基线的干净探针；序列 = 历史轮均值 + 当前轮均值，对前
    #   SENTINEL_MIN_ROUNDS 轮冻结基线跑双侧 CUSUM（stats.cusum，k/h 见常数注释）。
    #   注入器逐轮重置的恒参 AR(1) 常驻档轮均值可交换、低于该通道信息地板（诚实漏检，
    #   与 17 孔板批次地板同性质）。检出 → 全轮 capped 弱嫌疑（仪器级全局效应，基线
    #   漂移无空间足迹可逐孔归属——这是"逐孔归属"纪律的显式登记例外）；不设 edge
    #   抑制（winsorize + 当前轮闸已挡单轮事件残影），仅记 edge_concurrent 供归因参考。
    drift_corr = 0.0
    drift_scores: dict[str, float] = {}
    drift_ev: dict[str, Any] = {"armed": False, "fired": False,
                                "k": DRIFT_CUSUM_K, "h": DRIFT_CUSUM_H}
    try:
        caps, rs = [], []
        for o in obs_list:
            wid = o.layout_meta.well_id
            if wid in resid_raw:
                caps.append(o.instrument_meta.capture_index)
                rs.append(resid_raw[wid])
        drift_corr = _corr(np.array(caps), np.array(rs))
        drift_ev["corr"] = float(drift_corr)
    except (StatsError, ValueError) as exc:
        drift_ev["error"] = str(exc)
    try:
        cur_sent = [float(o.result.value) for o in obs_list
                    if o.is_control and _finite(o.result.value)]
        h_means = [float(np.mean(r)) for r in hist_rounds if len(r) >= 1]
        # Phase-I 冻结：基线均值与尺度都取 armed 前 SENTINEL_MIN_ROUNDS 轮
        # 【R2 核查结论】研究线警示"CUSUM 必须传冻结净基线 target、勿 self-start（否则漂移
        #   贯穿窗口时均值吸收漂移、sd 膨胀 → 自我脱敏、慢漂被自身淹没）"。此处已合规：
        #   target=mean(series[:3])（前 3 轮均值，随轮数增长恒取最早 3 轮 → 真冻结、非滚动），
        #   se 取 base_rounds 尺度，且 cusum(...) 显式传 target=0/sd=1 于已标准化 z（非默认
        #   self-starting 的 mean/std 全窗）。故无自脱敏，无需改动——仅登记核查通过。
        base_rounds = hist_rounds[:SENTINEL_MIN_ROUNDS]
        b_sds = [float(np.std(r, ddof=1)) for r in base_rounds if len(r) >= 2]
        b_ns = [len(r) for r in base_rounds if len(r) >= 2]
        drift_ev["rounds_seen"] = len(h_means)
        if len(h_means) >= SENTINEL_MIN_ROUNDS and cur_sent and b_sds:
            se = float(np.median(b_sds) / np.sqrt(float(np.median(b_ns))))
            if se > 0:
                series = np.array(h_means + [float(np.mean(cur_sent))])
                target = float(np.mean(series[:SENTINEL_MIN_ROUNDS]))
                z = np.clip((series - target) / se, -DRIFT_Z_CLIP, DRIFT_Z_CLIP)
                res = cusum(z, k=DRIFT_CUSUM_K, h=DRIFT_CUSUM_H, target=0.0, sd=1.0)
                stat = float(max(res["pos"][-1], res["neg"][-1]))
                fired = stat > DRIFT_CUSUM_H and abs(float(z[-1])) >= DRIFT_Z_CURRENT
                drift_ev.update({"armed": True, "cusum_stat": stat,
                                 "z_current": float(z[-1]), "target": target,
                                 "se": se, "fired": bool(fired),
                                 "edge_concurrent": edge_fired})
                if fired:
                    s = min(DRIFT_CAP, _ramp(stat, DRIFT_CUSUM_H, DRIFT_STAT_FULL))
                    for o in obs_list:
                        drift_scores[o.layout_meta.well_id] = s
        if not drift_ev["armed"]:
            drift_ev["reason"] = "跨轮哨兵历史不足（<3 轮）→ record-only（冷启动纪律）"
    except (StatsError, ValueError) as exc:
        drift_ev["cross_round_error"] = str(exc)

    # ---- glare 曝光通道（exposure>1.25 计数；FPR≈0 首选通道）----
    glare_scores: dict[str, float] = {}
    glare_ev: dict[str, Any] = {"threshold": GLARE_EXPOSURE, "count": 0}
    try:
        for o in obs_list:
            exp_v = o.instrument_meta.exposure
            if _finite(exp_v) and exp_v > GLARE_EXPOSURE:
                glare_scores[o.layout_meta.well_id] = GLARE_SCORE
        glare_ev["count"] = len(glare_scores)
    except Exception as exc:  # noqa: BLE001 — one check must not crash the whole QC round
        logging.error("qc check 'glare_channel' crashed: %r", exc)
        glare_scores = {}
        glare_ev["error"] = repr(exc)
        glare_ev["check_crashed"] = True

    # ---- dust 颗粒通道（同候选副本 grain_count 异常高）----
    dust_scores: dict[str, float] = {}
    dust_ev: dict[str, Any] = {"ratio_lo": DUST_RATIO_LO}
    try:
        for k, members in groups.items():
            if k[0] != "CAND":
                continue
            pairs = [(o.layout_meta.well_id, o.result.secondary.get("grain_count"))  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
                     for o in members]
            gvals = [(w, g) for w, g in pairs if _finite(g) and g > 0]
            if len(gvals) >= 2:
                gs = np.array([g for _, g in gvals])
                gmin = float(gs.min())
                if gmin > 0:
                    for w, g in gvals:
                        ratio = g / gmin
                        if g == gs.max() and ratio > DUST_RATIO_LO and gs.max() > gs.min():
                            dust_scores[w] = _ramp(ratio, DUST_RATIO_LO, DUST_RATIO_HI)
    except Exception as exc:  # noqa: BLE001 — one check must not crash the whole QC round
        logging.error("qc check 'dust_channel' crashed: %r", exc)
        dust_scores = {}
        dust_ev["error"] = repr(exc)
        dust_ev["check_crashed"] = True

    # ---- 副本组 CV + 组内离群（候选组）----
    replicate_scores: dict[str, float] = {}
    replicate_meta: dict[str, dict[str, Any]] = {}
    replicate_crashed = False
    replicate_error = ""
    try:
        for k, members in groups.items():
            if k[0] != "CAND":
                continue
            vals = [(o.layout_meta.well_id, o.result.value) for o in members
                    if _finite(o.result.value)]
            if len(vals) < 2:
                continue
            v = np.array([x for _, x in vals])
            mean = float(v.mean())
            cv = float(v.std() / abs(mean)) if mean != 0 else float("inf")
            # 组内离群：相对板噪声尺度的 z（去身份残差）
            devs = {w: abs(resid_raw.get(w, 0.0)) / resid_scale for w, _ in vals}
            max_z = max(devs.values()) if devs else 0.0
            meta = {"cv": cv, "max_z": float(max_z), "n_rep": len(vals),
                    "cv_thresh": CV_THRESH}
            # 分基于绝对离群 z（相对板噪声尺度）而非 CV——低值候选 CV 天然放大，用 CV 打分会爆假阳
            fired = cv > CV_THRESH and max_z > REPLICATE_Z_FIRE
            if fired:
                for w, _ in vals:
                    if devs[w] > REPLICATE_Z_FIRE:
                        replicate_scores[w] = _ramp(devs[w], REPLICATE_Z_FIRE, REPLICATE_Z_FULL)
            for w, _ in vals:
                replicate_meta[w] = meta
    except Exception as exc:  # noqa: BLE001 — one check must not crash the whole QC round
        logging.error("qc check 'replicate_cv' crashed: %r", exc)
        replicate_scores, replicate_meta = {}, {}
        replicate_crashed, replicate_error = True, repr(exc)

    # ---- 哨兵稳健 z（板级上下文）----
    sentinel_zscores: dict[str, float] = {}
    ctrl_obs = [o for o in obs_list if o.is_control and _finite(o.result.value)]
    if ctrl_obs:
        cvals = np.array([o.result.value for o in ctrl_obs])
        cmed = float(np.median(cvals))
        cscale = _robust_scale(cvals)
        for o in ctrl_obs:
            sentinel_zscores[o.layout_meta.well_id] = float(
                (o.result.value - cmed) / cscale
            )

    # ---- 跨轮控制带（冷启动：<3 轮 record-only）----
    pooled = np.array([v for r in hist_rounds for v in r]) if hist_rounds else np.array([])
    band_armed = len(hist_rounds) >= SENTINEL_MIN_ROUNDS and pooled.size >= SENTINEL_MIN_ROUNDS
    band_mean = float(pooled.mean()) if pooled.size else 0.0
    band_std = float(pooled.std()) if pooled.size > 1 else 0.0

    plate = PlateContext(
        round_id=exp.round_id,
        residual_grid=detr,
        row_effects=row_effects,
        col_effects=col_effects,
        edge_paired_diff=edge_paired_diff,
        gradient_slope_t=float(gradient_slope_t),
        moran=moran_res,
        batch_shifts=batch_shifts,
        drift_corr=float(drift_corr),
        sentinel_zscores=sentinel_zscores,
        d_edge=d_edge,
        resid_scale=float(resid_scale),
    )

    # ============================================================ 逐观测聚合
    reports: dict[str, QCReport] = {}
    # lo_m, hi_m already unpacked from metric_range at the top of run_qc (scale-aware edge floor).

    # 单观测聚合体：任何逐检查异常已在各检查块内转 error-evidence；此处再包一层
    # per-observation 兜底，保证任一观测的意外崩溃不拖垮整轮 QC（见下方 guarded loop）。
    def _report_for(o: ObservationObject) -> QCReport:
        wid = o.layout_meta.well_id
        checks: list[QCCheck] = []

        # ---------- hard（各自独立，快失败不阻断其余） ----------
        v = o.result.value
        miss = not _finite(v)
        checks.append(QCCheck(
            name="missing_nan", level="hard", passed=not miss,
            score=1.0 if miss else 0.0,
            evidence={"value": None if v is None else (float(v) if _finite(v) else "nan"),
                      "reason": "missing/NaN" if miss else "ok"},
        ))
        oor = (not miss) and (v < lo_m or v > hi_m)
        checks.append(QCCheck(
            name="out_of_range", level="hard", passed=not oor,
            score=1.0 if oor else 0.0,
            evidence={"value": float(v) if _finite(v) else None, "range": [lo_m, hi_m]},
        ))
        exp_v, ill_v = o.instrument_meta.exposure, o.instrument_meta.illumination
        bad_instr = (exp_v > EXPOSURE_HI or exp_v < EXPOSURE_LO
                     or ill_v > EXPOSURE_HI or ill_v < EXPOSURE_LO)
        checks.append(QCCheck(
            name="exposure_illumination", level="hard", passed=not bad_instr,
            score=1.0 if bad_instr else 0.0,
            evidence={"exposure": float(exp_v), "illumination": float(ill_v),
                      "band": [EXPOSURE_LO, EXPOSURE_HI]},
        ))

        # ---------- reference（哨兵 + 副本） ----------
        if o.is_control:
            try:
                band = control_band.get(o.control_id)
                if band is not None and _finite(v):
                    lo, hi = band
                    inband = lo <= v <= hi
                    hw = max((hi - lo) / 2.0, 1e-9)
                    dev = 0.0 if inband else max(lo - v, v - hi)
                    checks.append(QCCheck(
                        name="sentinel_band", level="reference", passed=inband,
                        score=0.0 if inband else min(1.0, dev / hw),
                        evidence={"band": [lo, hi], "value": float(v),
                                  "z": float(dev / hw)},
                    ))
            except Exception as exc:  # noqa: BLE001 — one check must not crash the whole QC round
                logging.error("qc check 'sentinel_band' crashed for %r: %r", wid, exc)
                checks.append(_crashed_check(
                    "sentinel_band", "reference", {"error": repr(exc)}))
            # 跨轮控制带
            try:
                if band_armed and _finite(v):
                    sd = band_std if band_std > 0 else 1e-9
                    z = (v - band_mean) / sd
                    out = abs(z) > 3.0
                    checks.append(QCCheck(
                        name="sentinel_control_band", level="reference", passed=not out,
                        score=_ramp(abs(z), 3.0, 9.0) if out else 0.0,
                        evidence={"band": [band_mean - 3 * sd, band_mean + 3 * sd],
                                  "value": float(v), "z": float(z),
                                  "rounds_seen": len(hist_rounds), "armed": True},
                    ))
                else:
                    checks.append(QCCheck(
                        name="sentinel_control_band", level="reference", passed=True,
                        score=0.0,
                        evidence={"rounds_seen": len(hist_rounds), "armed": False,
                                  "reason": "冷启动 record-only（<3 轮）"},
                    ))
            except Exception as exc:  # noqa: BLE001 — one check must not crash the whole QC round
                logging.error("qc check 'sentinel_control_band' crashed for %r: %r", wid, exc)
                checks.append(_crashed_check(
                    "sentinel_control_band", "reference", {"error": repr(exc)}))
        elif replicate_crashed:
            checks.append(_crashed_check(
                "replicate_cv", "reference", {"error": replicate_error}))
        else:
            rmeta = replicate_meta.get(wid, {})
            rscore = replicate_scores.get(wid, 0.0)
            checks.append(QCCheck(
                name="replicate_cv", level="reference", passed=rscore == 0.0,
                score=rscore, evidence=rmeta or {"n_rep": 1, "reason": "无副本"},
            ))

        # ---------- structural（板级检查逐孔归属） ----------
        es = edge_scores.get(wid, 0.0)
        checks.append(QCCheck(
            name="edge_effect", level="structural", passed=es == 0.0, score=es,
            evidence={**edge_ev, "d_edge": d_edge.get(wid),
                      "resid_raw": resid_raw.get(wid)},
        ))
        if grad_ev.get("check_crashed"):
            checks.append(_crashed_check("row_col_gradient", "structural", grad_ev))
        else:
            gs = grad_scores.get(wid, 0.0)
            checks.append(QCCheck(
                name="row_col_gradient", level="structural", passed=gs == 0.0, score=gs,
                evidence=grad_ev,
            ))
        checks.append(QCCheck(          # Moran 筛查：score 恒 0，仅记板级证据
            name="spatial_moran", level="structural", passed=True, score=0.0,
            evidence=moran_ev,
        ))
        bs = batch_scores.get(wid, 0.0)
        checks.append(QCCheck(
            name="batch_shift", level="structural", passed=bs == 0.0, score=bs,
            evidence={**batch_ev, "batch": o.material_meta.solution_batch},
        ))
        ds = drift_scores.get(wid, 0.0)
        checks.append(QCCheck(          # 单轮 record-only；跨轮哨兵 CUSUM 检出→全轮弱嫌疑
            name="temporal_drift", level="structural", passed=ds == 0.0, score=ds,
            evidence=drift_ev,
        ))
        if glare_ev.get("check_crashed"):
            checks.append(_crashed_check("glare_channel", "structural", glare_ev))  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
        else:
            gl = glare_scores.get(wid, 0.0)
            checks.append(QCCheck(
                name="glare_channel", level="structural", passed=gl == 0.0, score=gl,  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
                evidence={"exposure": float(exp_v), "threshold": GLARE_EXPOSURE,
                          "board_count": glare_ev["count"]},
            ))
        if dust_ev.get("check_crashed"):
            checks.append(_crashed_check("dust_channel", "structural", dust_ev))  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
        else:
            du = dust_scores.get(wid, 0.0)
            checks.append(QCCheck(
                name="dust_channel", level="structural", passed=du == 0.0, score=du,  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
            ))

        suspicion = max((c.score for c in checks), default=0.0)
        flags = [c.name for c in checks if not c.passed]
        return QCReport(checks=checks, flags=flags, suspicion=float(suspicion))

    for o in obs_list:
        try:
            reports[o.obs_id] = _report_for(o)
        except Exception as exc:  # noqa: BLE001 — one observation must not crash the whole round
            logging.error("qc aggregation crashed for obs %r: %r", o.obs_id, exc)
            reports[o.obs_id] = QCReport(
                checks=[_crashed_check(
                    "qc_aggregation", "hard", {"error": repr(exc)})],
                flags=["qc_aggregation"], suspicion=0.0,
            )

    return reports, plate
