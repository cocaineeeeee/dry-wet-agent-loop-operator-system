"""M6 归因引擎（docs/M6_DESIGN.md v2；REFERENCE_MAP §13.5 反驳器 / §14 阈值）。

职责：为 SUSPECT/FAILED 观测**指认原因**——六假设签名评分 → 归一化伪后验 →
反驳器门控（DoWhy 纪律）→ FailureAttribution + next_action。

设计红线（本层严守，与 qc/checks.py、qc/stats.py 同级）：
  * 只读 **OS 可见证据**：QCReport 的 QCCheck 结论/证据、PlateContext 的板级系数与
    残差网格、obs 的 layout/material/instrument_meta 与 result.secondary、exp.layout
    的几何（现算 d_edge、批次标签、capture 顺序）——**绝不引用注入器内部参数或仿真真相**；
  * 不重算残差/系数：M5 已在 PlateContext 唯一物化，M6 只读、只在其上做判别回归（§6.6）；
  * 纯函数、只读、确定性（同 seed 同输出）；不写 store、不改 trust。

判别核心（§2.6）：edge vs gradient 用 **ΔR² 双回归**（谁解释残差方差多谁胜）为主判据，
对边符号只作辅助锚点——单轴梯度在垂直轴一对边上"对边同号"，纯符号判据会误读为 edge。

多重比较取舍（ΔR² 无 Bonferroni——R1 P3 核实）：六假设各带**独立硬显著门**（z/t≥SIG_Z=3，
p≈0.003）+ board_sig∧footprint 硬门，实测每孔**至多 1 个假设得正分**（互斥选择而非并行多检验，
见 test_clean_board_fpr_and_single_hypothesis_bounded）；ΔR² 仅在 edge/gradient 两个**都已过
显著门**的结构信号间**仲裁方向**，本身不是显著性判据。故选择族在门后有效大小=1，不作 Bonferroni
校正。影响界：20 种子干净板 720 孔实测家族误归因率 **2.4%**（17/720，全为 batch；edge/gradient=0），
该数已含全部多重性，无需再乘族大小。

反驳器合同（§2.4/§13.5，两判据方向相反）：结构推断类假设（edge/gradient/batch/drift）
的 top 须**同时**过 placebo（打乱标签→效应塌零）与 subsample（抽子样→效应稳定）才写
top_cause；直接测量类假设（glare/dust）由 M5 硬/参考通道（已 FPR 定标）自证，不走 DoWhy
置换（缺板级逐孔 exposure/grain 数组，且直接读数非因果推断）。未过 → 降级、top_cause=None。

subsample 门强度取舍（0.5 系数不收紧——R1 P3 核实）：subsample 判 std<0.5·|观测|
（系数在 stats.refute_subsample，工程标定）守的是**单孔高杠杆偶然效应**（抽样即散架，见
test_refuter_intercepts_coincidental_signal），**非**"随机分组恰有稳定均差"——子样保均差，
subsample 对后者结构性无力，故 batch 类假阳靠 placebo（洗标签→塌零）兜底。composite 门
（硬门∧placebo∧subsample∧FLOOR∧MARGIN）实测干净板 FPR 2.4%（结构类 edge/gradient=0，
残余 2.4% 全为 batch），已足紧——故保留 0.5 系数，取舍如实记此而非收紧。

inconclusive（§2.7）：p_top<FLOOR ∨ (p_top−p_second)<MARGIN ∨ top 反驳器未过
→ top_cause=None、remedy=DISAMBIGUATION_REPEAT（借鉴 A-Lab 模糊判定）。
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from expos.errors import ExposError
from expos.kernel.objects import (
    ActionType,
    ExperimentObject,
    FailureAttribution,
    FailureHypothesis,
    ObservationObject,
    QCReport,
    RecommendedAction,
)
from expos.qc.checks import PlateContext
from expos.qc.stats import refute_placebo, refute_subsample

# ---- 阈值（§14 / M6_DESIGN §2.3/§2.7；与 checks.py 显著性门限对齐）----
SIG_Z = 3.0
EDGE_Z_FULL = 8.0
GRAD_Z_FULL = 8.0
BATCH_Z_FULL = 8.0
GLARE_EXPOSURE = 1.25          # §14 首选曝光通道
GLARE_EXPOSURE_FULL = 1.6
DRIFT_LO, DRIFT_HI = 0.30, 0.70   # |残差–capture 相关| 显著带
DUST_Z_LO, DUST_Z_FULL = 2.0, 5.0
R2_GRAD_GATE = 0.05               # 梯度双回归解释残差方差的最低门（滤板级伪梯度）

FLOOR = 0.45                   # p_top 地板（§2.7）
MARGIN = 0.15                  # top−second 最小间隔（§2.7）

# 签名项权重（§2.3）——工程标定，如实记**三通道**实际计权（R1 P3 核实修正）：
#   QC 命中 0.4 / 主特征 0.3 / 判别锚点 0.3。
# 历史声称"锚点 0.2 + 辅助 0.1"四档，但辅助项在 _emit 里恒等于锚点（aux = anchor），
# 并无独立辅助信号——四档声明与实现不符。故合并为单一锚点权重 0.3（= 旧 0.2+0.1，
# score 逐位不变），杜绝"四档"虚假声明（文档不说谎）。batch 的锚点为常数 1.0（离散批
# 标签阶跃，见其签名块），即贡献 0.3 常数基线，非独立第四通道。
W_QC, W_MAIN, W_ANCHOR = 0.4, 0.3, 0.3

CAUSES = (
    "edge_evaporation",
    "thermal_gradient",
    "glare",  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
    "glare",  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
    "batch_effect",
    "instrument_drift",
)

# remedy 映射（§2.5）：(ActionType, semantics, 额外 params)
_REMEDY: dict[str, tuple[ActionType, str, dict[str, Any]]] = {
    "edge_evaporation": (ActionType.DISAMBIGUATION_REPEAT, "detour", {"placement_hint": "center_only"}),
    "thermal_gradient": (ActionType.ADD_CONTROLS, "addition", {}),
    "glare": (ActionType.REMEASURE, "detour", {"recapture": True}),  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
    "glare": (ActionType.REMEASURE, "detour", {"recapture": True}),  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
    "batch_effect": (ActionType.REPEAT_CANDIDATE, "addition", {"cross_batch": True}),
    "instrument_drift": (ActionType.REMEASURE, "detour", {"calibrate_flag": True}),
}
# 直接测量类假设（不走 DoWhy 置换，见模块 docstring）
_DIRECT = frozenset({"glare", "dust_contamination"})  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
_INCONCLUSIVE = (ActionType.DISAMBIGUATION_REPEAT, "detour", {"placement_hint": "center_only"})


class AttributionError(ExposError):
    """归因引擎的响亮失败（退化输入、非法证据面等）。"""


# ================================================================ 小工具

def _sig(x: float, lo: float, hi: float) -> float:
    """显著性斜坡 [lo,hi]→[0,1]（同 checks._ramp）。"""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def _finite(v: Any) -> bool:
    return v is not None and isinstance(v, (int, float)) and np.isfinite(v)


def _check(report: QCReport | None, name: str):
    if report is None:
        return None
    for c in report.checks:
        if c.name == name:
            return c
    return None


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if a.size < 3 or a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _raw_residual_grid(plate: PlateContext) -> np.ndarray:
    """从 PlateContext 重建**去趋势前**的候选去身份残差网格（OS 可见量的代数恢复）。

    median polish: grid ≈ overall + row + col + residuals；故
    raw ≈ row_effects[:,None] + col_effects[None,:] + residual_grid（overall 常数经中心化消去）。
    梯度信号被 median polish 吸入 row/col 效应，故判别必须在此重建的 raw 残差上做（§2.6）。
    """
    grid = np.asarray(plate.residual_grid, float)
    row = np.asarray(plate.row_effects, float)
    col = np.asarray(plate.col_effects, float)
    raw = grid + row[:, None] + col[None, :]
    if np.isfinite(raw).any():
        raw = raw - np.nanmean(raw)
    return raw


def _d_of(row: int, col: int, rows: int, cols: int) -> int:
    return int(min(row, col, rows - 1 - row, cols - 1 - col))


def _board_frame(plate: PlateContext, exp: ExperimentObject) -> dict[str, np.ndarray]:
    """逐孔证据帧（供判别回归与反驳器）：残差 + 位置 + 批次标签 + capture 顺序。

    批次/capture 全部由 exp.layout 的**枚举顺序几何**现算（sim_base 以枚举序 = capture 序、
    solution_batch=f"R{round}-B{(row+col)%n_batches}" 棋盘格），OS 可见、无真值。
    分组公式必须与 sim_base 严格一致：错用 capture 序 idx%n 时，观测自身常被排除出
    其批次组，真批次效应被稀释、t_batch 失真（缝隙审查实锤的失联 bug）。
    """
    raw = _raw_residual_grid(plate)
    rows, cols = exp.layout.rows, exp.layout.cols
    n_batches = max(1, exp.execution_req.n_solution_batches)
    ctrl_ids = {w.well_id for w in exp.layout.wells if w.control_id is not None}
    resid, rr, cc, dd, cap, ctrlf = [], [], [], [], [], []
    batch: list[str] = []
    for idx, w in enumerate(exp.layout.wells):
        if not (0 <= w.row < rows and 0 <= w.col < cols):
            continue
        v = raw[w.row, w.col]
        if not np.isfinite(v):
            continue
        resid.append(float(v))
        rr.append(w.row)
        cc.append(w.col)
        dd.append(_d_of(w.row, w.col, rows, cols))
        cap.append(idx)
        ctrlf.append(1.0 if w.well_id in ctrl_ids else 0.0)
        batch.append(f"R{exp.round_id}-B{(w.row + w.col) % n_batches}")  # lint: allow-domain-literal(M6 legacy: mirrors adapters/sim_base.py batch checkerboard formula for EXP003 cross-consistency, grandfathered pending Domain Profile extraction (Q3))
    return {
        "resid": np.array(resid, float),
        "row": np.array(rr, float),
        "col": np.array(cc, float),
        "d": np.array(dd, float),
        "capture": np.array(cap, float),
        "is_control": np.array(ctrlf, float),
        "batch": np.array(batch, dtype=object),
        "raw_grid": raw,
    }


def _group_t(resid: np.ndarray, mask: np.ndarray, scale: float) -> float:
    """两组均值差的标准化 t（供批次阶跃判据；效应量估计器复用 §2.3）。"""
    m = np.asarray(mask, bool)
    rest = ~m
    if m.sum() < 1 or rest.sum() < 1 or scale <= 0:
        return 0.0
    diff = float(resid[m].mean() - resid[rest].mean())
    se = scale * np.sqrt(1.0 / m.sum() + 1.0 / rest.sum())
    return diff / se if se > 0 else 0.0


def _corr_t(corr: float, n: int) -> float:
    """相关系数 → t 统计量：r·sqrt((n−2)/(1−r²))。"""
    if n < 3 or abs(corr) >= 1.0:
        return 0.0
    return float(corr * np.sqrt((n - 2) / (1.0 - corr * corr)))


def _sentinel_drift(frame: dict[str, np.ndarray]) -> float:
    """哨兵（控制组，跨 capture 的板级传感器）残差–capture 相关：单轮唯一可见的漂移信号。

    候选去身份残差在同候选副本对内几乎抵消漂移（副本 capture 相邻），故漂移只在哨兵组
    （单一控制组、贯穿 capture 全程）的去身份残差里显形（§2.2 时间维；layout：哨兵=板级传感器）。
    """
    ctrl = frame["is_control"] == 1.0
    if ctrl.sum() < 3:
        return 0.0
    return _safe_corr(frame["resid"][ctrl], frame["capture"][ctrl])


def _spatial_stats(frame: dict[str, np.ndarray], resid_scale: float) -> dict[str, Any]:
    """edge / gradient 判别量：ΔR² 双回归 + 对边符号锚点（§2.6）。"""
    resid = frame["resid"]
    d = frame["d"]
    row = frame["row"]
    col = frame["col"]
    raw = frame["raw_grid"]
    out: dict[str, Any] = {
        "edge_diff": 0.0, "edge_z": 0.0, "r2_edge": 0.0, "r2_grad": 0.0,
        "grad_axis": "row", "grad_corr": 0.0, "grad_t": 0.0,
        "edge_sign_consistency": 0.0, "grad_opposite": 0.0,
    }
    if resid.size < 3:
        return out
    em = d <= 1
    im = d >= 2
    tot = float(((resid - resid.mean()) ** 2).sum())
    if em.any() and im.any():
        me, mi = float(resid[em].mean()), float(resid[im].mean())
        out["edge_diff"] = me - mi
        ne, ni = int(em.sum()), int(im.sum())
        se = resid_scale * np.sqrt(1.0 / ne + 1.0 / ni) if resid_scale > 0 else 0.0
        out["edge_z"] = (me - mi) / se if se > 0 else 0.0
        if tot > 0:
            pred = np.where(em, me, mi)
            out["r2_edge"] = max(0.0, 1.0 - float(((resid - pred) ** 2).sum()) / tot)
    cr = _safe_corr(row, resid)
    cc_ = _safe_corr(col, resid)
    if cr * cr >= cc_ * cc_:
        out["r2_grad"], out["grad_axis"], out["grad_corr"] = cr * cr, "row", cr
    else:
        out["r2_grad"], out["grad_axis"], out["grad_corr"] = cc_ * cc_, "col", cc_
    out["grad_t"] = _corr_t(out["grad_corr"], resid.size)
    # 四边均值（对边符号结构）
    rows, cols = raw.shape

    def _edge_mean(sel: np.ndarray) -> float:
        s = raw[sel]
        s = s[np.isfinite(s)]
        return float(s.mean()) if s.size else 0.0

    ridx = np.arange(rows)[:, None] * np.ones((1, cols))
    cidx = np.ones((rows, 1)) * np.arange(cols)[None, :]
    top, bot = _edge_mean(ridx == 0), _edge_mean(ridx == rows - 1)
    left, right = _edge_mean(cidx == 0), _edge_mean(cidx == cols - 1)
    dom = np.sign(out["edge_diff"]) if out["edge_diff"] != 0 else 1.0
    same = sum(1 for e in (top, bot, left, right) if e != 0 and np.sign(e) == dom)
    out["edge_sign_consistency"] = same / 4.0
    if out["grad_axis"] == "row":
        out["grad_opposite"] = 1.0 if np.sign(top) * np.sign(bot) < 0 else 0.0
    else:
        out["grad_opposite"] = 1.0 if np.sign(left) * np.sign(right) < 0 else 0.0
    return out


# ================================================================ 反驳器绑定

def _refute_spatial_edge(frame, seed) -> dict[str, Any]:
    resid = frame["resid"]
    em = frame["d"] <= 1
    im = frame["d"] >= 2

    def diff(vals: np.ndarray) -> float:
        vals = np.asarray(vals, float)
        if not (em.any() and im.any()):
            return 0.0
        return float(vals[em].mean() - vals[im].mean())

    data = np.column_stack([resid, em.astype(float)])

    def diff_sub(a: np.ndarray) -> float:
        e = a[:, 1] == 1.0
        i = ~e
        if not (e.any() and i.any()):
            return 0.0
        return float(a[e, 0].mean() - a[i, 0].mean())

    pl = refute_placebo(diff, resid, n=999, seed=seed)
    ss = refute_subsample(diff_sub, data, frac=0.8, n=100, seed=seed)
    return {"placebo": pl, "subsample": ss, "passed": bool(pl["passed"] and ss["passed"])}


def _refute_spatial_grad(frame, axis, seed) -> dict[str, Any]:
    resid = frame["resid"]
    idx = frame[axis].astype(float)

    def slope(vals: np.ndarray) -> float:
        return _safe_corr(np.asarray(vals, float), idx)

    data = np.column_stack([resid, idx])

    def slope_sub(a: np.ndarray) -> float:
        return _safe_corr(a[:, 0], a[:, 1])

    pl = refute_placebo(slope, resid, n=999, seed=seed)
    ss = refute_subsample(slope_sub, data, frac=0.8, n=100, seed=seed)
    return {"placebo": pl, "subsample": ss, "passed": bool(pl["passed"] and ss["passed"])}


def _refute_batch(frame, obs_batch, seed) -> dict[str, Any]:
    resid = frame["resid"]
    flag = (frame["batch"] == obs_batch).astype(float)
    if flag.sum() < 1 or (1.0 - flag).sum() < 1:
        return {"passed": False, "reason": "batch 单组，无对照"}

    def diff(lab: np.ndarray) -> float:
        lab = np.asarray(lab, float)
        a, b = resid[lab == 1.0], resid[lab == 0.0]
        if a.size < 1 or b.size < 1:
            return 0.0
        return float(a.mean() - b.mean())

    data = np.column_stack([resid, flag])

    def diff_sub(a: np.ndarray) -> float:
        g1, g0 = a[a[:, 1] == 1.0, 0], a[a[:, 1] == 0.0, 0]
        if g1.size < 1 or g0.size < 1:
            return 0.0
        return float(g1.mean() - g0.mean())

    pl = refute_placebo(diff, flag, n=999, seed=seed)
    ss = refute_subsample(diff_sub, data, frac=0.8, n=100, seed=seed)
    return {"placebo": pl, "subsample": ss, "passed": bool(pl["passed"] and ss["passed"])}


def _refute_top(cause: str, frame, sp, obs, report, seed) -> dict[str, Any]:
    """按假设类型选反驳器；直接测量/传感器类由 M5 通道/控制证据自证（见模块 docstring）。"""
    if cause in _DIRECT:
        chk = _check(report, "glare_channel" if cause == "glare" else "dust_channel")  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
        strong = bool(chk is not None and chk.score > 0.0) or (
            cause == "glare" and obs.instrument_meta.exposure > GLARE_EXPOSURE  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
        )
        return {"mode": "direct_channel_evidence", "passed": bool(strong)}
    if cause == "instrument_drift":
        # 哨兵传感器（reference 级控制，FPR 已定标）测得的漂移相关：非因果推断，不走置换。
        return {"mode": "sentinel_sensor", "drift_sentinel": round(_sentinel_drift(frame), 4),
                "passed": bool(abs(_sentinel_drift(frame)) >= DRIFT_LO)}
    if cause == "edge_evaporation":
        return _refute_spatial_edge(frame, seed)
    if cause == "thermal_gradient":
        return _refute_spatial_grad(frame, sp["grad_axis"], seed)
    if cause == "batch_effect":
        return _refute_batch(frame, obs.material_meta.solution_batch, seed)
    return {"passed": False, "reason": "未知假设"}


# ================================================================ 签名评分

def _score_hypotheses(
    obs: ObservationObject, report: QCReport, plate: PlateContext,
    frame: dict[str, np.ndarray], sp: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """六假设签名分 + 逐项证据（§2.2/§2.3）。返回 cause -> {score, evidence}。"""
    sigma = float(plate.resid_scale) if plate.resid_scale > 0 else 1e-9
    tau = 0.5 * sigma
    rows, cols = plate.residual_grid.shape

    # r_i：候选去身份残差（优先读 QC 已物化的 resid_raw，回退重建网格）
    ec = _check(report, "edge_effect")
    r_i = ec.evidence.get("resid_raw") if ec is not None else None
    if not _finite(r_i):
        v = frame["raw_grid"][obs.layout_meta.row, obs.layout_meta.col]
        r_i = float(v) if np.isfinite(v) else 0.0
    r_i = float(r_i)

    d_well = _d_of(obs.layout_meta.row, obs.layout_meta.col, rows, cols)
    frac_r = obs.layout_meta.row / (rows - 1) if rows > 1 else 0.5
    frac_c = obs.layout_meta.col / (cols - 1) if cols > 1 else 0.5
    edge_wins = sp["r2_edge"] >= sp["r2_grad"]   # ΔR² 双回归：谁解释残差方差多（§2.6 主判据）
    grad_wins = sp["r2_grad"] > sp["r2_edge"]

    # 直接测量证据（glare 曝光越界 / dust 成核计数越限）——在场则**抑制**推断类空间/批次假设：
    # 读数异常已由测得的仪器/样品通道解释，不再向位置/批次做因果推断（对抗混叠，§2.2）。
    exposure = float(obs.instrument_meta.exposure)
    glare_hit = exposure > GLARE_EXPOSURE
    dc = _check(report, "dust_channel")  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
    dust_score = float(dc.score) if dc is not None else 0.0
    dust_fired = dust_score > 0.0
    direct = glare_hit or dust_fired

    res: dict[str, dict[str, Any]] = {}

    def _emit(cause, board_sig, foot, qc, main, anchor, effect, evidence):
        # 硬门控（§2.7 小样本纪律）：board_sig=板级效应显著、foot=本孔在伪影足迹内。
        # 二者缺一 → 该假设记 0，杜绝单孔噪声/足迹外孔/混叠假设被误判为高置信。
        # 三通道计权（无独立辅助信号，见 W_* 声明）：QC 命中 + 主特征 + 判别锚点。
        raw = W_QC * qc + W_MAIN * main + W_ANCHOR * anchor
        score = (1.0 if (board_sig and foot) else 0.0) * raw
        evidence.update({"board_sig": bool(board_sig), "in_footprint": bool(foot),
                         "effect": effect, "debias_hint": None})
        res[cause] = {"score": float(score), "evidence": evidence}

    # ---- edge_evaporation：M5 edge 检查命中（FPR 定标）∧ ΔR² edge 胜 + 本孔边界正抬升 ----
    # 方向假设（R1 P3 核实）：edge 签名硬编码 r_i>0（残差正抬升），与 EdgeEvaporation
    # 注入器语义**结构耦合**。核实结论=正向：该注入器为 measured*=1+strength·exp(−d/decay)，
    # strength>0（默认 0.25、coating/crystal 场景标定 0.3/0.5 全正）→ 边界读数只升；
    # 物理上 Deegan 接触线蒸发的浓缩沉积被成像误读为高质量，机制单向抬高（"假最优"来源）。
    # 故 r>0 门与注入器正向语义一致，是标定而非疏漏。★换域复核：若新域引入负 strength 或
    # 反向边界效应（边界读数走低），须改用 |r_i| 双向并重标 edge_foot/edge_diff/main 门与
    # FPR——当前方向门会漏检反向 edge（下方 edge_foot/main/edge_board 均设 r_i>0）。
    edge_fired = bool(ec is not None and ec.evidence.get("fired"))
    edge_z = ec.evidence.get("z") if ec is not None else None
    edge_z = float(edge_z) if _finite(edge_z) else float(sp["edge_z"])
    edge_diff = ec.evidence.get("diff") if ec is not None else None
    edge_diff = float(edge_diff) if _finite(edge_diff) else float(sp["edge_diff"])
    edge_board = edge_fired and edge_wins and edge_diff > 0 and not direct
    edge_foot = d_well <= 1 and r_i > 0
    qc = _sig(abs(edge_z), SIG_Z, EDGE_Z_FULL)
    main = _sig(r_i, tau, 4 * tau) if r_i > 0 else 0.0
    anchor = sp["edge_sign_consistency"]
    _emit("edge_evaporation", edge_board, edge_foot, qc, main, anchor, round(edge_diff, 5),
          {"edge_z": round(edge_z, 4), "edge_diff": round(edge_diff, 5),
           "r2_edge": round(sp["r2_edge"], 4), "r2_grad": round(sp["r2_grad"], 4),
           "d_edge": d_well, "r_i": round(r_i, 5), "edge_wins": bool(edge_wins),
           "edge_sign_consistency": sp["edge_sign_consistency"]})

    # ---- thermal_gradient：残差–位置斜率 t 显著 ∧ ΔR² grad 胜 + 本孔极端位符号一致 + 对边异号 ----
    grad_t = float(sp["grad_t"])
    grad_board = (abs(grad_t) >= SIG_Z and sp["r2_grad"] >= R2_GRAD_GATE
                  and grad_wins and not direct)
    extreme = frac_r < 0.15 or frac_r > 0.85 or frac_c < 0.15 or frac_c > 0.85
    pos = (frac_r - 0.5) if sp["grad_axis"] == "row" else (frac_c - 0.5)
    pred_sign = np.sign(sp["grad_corr"] * pos)
    grad_sign_ok = r_i != 0 and np.sign(r_i) == pred_sign and pred_sign != 0
    grad_foot = extreme and grad_sign_ok
    qc = _sig(abs(grad_t), SIG_Z, GRAD_Z_FULL)
    main = 1.0 if grad_sign_ok else 0.0
    anchor = sp["grad_opposite"]
    _emit("thermal_gradient", grad_board, grad_foot, qc, main, anchor,
          round(float(sp["grad_corr"]), 4),
          {"grad_t": round(grad_t, 4), "grad_axis": sp["grad_axis"],
           "r2_grad": round(sp["r2_grad"], 4), "r2_edge": round(sp["r2_edge"], 4),
           "grad_wins": bool(grad_wins), "grad_opposite": sp["grad_opposite"]})

    # ---- glare：曝光越界（直接测量，位置无关）+ 读数虚高 ----
    qc = _sig(exposure, GLARE_EXPOSURE, GLARE_EXPOSURE_FULL) if glare_hit else 0.0
    main = qc
    anchor = (0.5 + 0.5 * (1.0 if r_i >= 0 else 0.0)) if glare_hit else 0.0
    _emit("glare", glare_hit, glare_hit, qc, main, anchor, round(exposure - 1.0, 4),  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
          {"exposure": round(exposure, 4), "threshold": GLARE_EXPOSURE, "r_i": round(r_i, 5)})

    # ---- dust_contamination：成核计数高（直接测量通道）+ 读数下降 + 曝光正常 ----
    grain = obs.result.secondary.get("grain_count")  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
    dust_foot = r_i < 0
    qc = dust_score
    main = _sig(-r_i, tau, 4 * tau) if r_i < 0 else 0.0
    anchor = (0.5 if not glare_hit else 0.0) + 0.5 * (1.0 if dust_fired else 0.0)
    _emit("dust_contamination", dust_fired, dust_foot, qc, main, anchor, round(r_i, 5),  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
          {"dust_score": round(dust_score, 4),
           "grain_count": round(float(grain), 3) if _finite(grain) else None,  # lint: allow-domain-literal(M5/M6 legacy: crystal imaging/growth channel proper noun (glare/dust/grain_count), grandfathered pending Domain Profile extraction (Q3))
           "r_i": round(r_i, 5), "exposure": round(exposure, 4)})

    # ---- batch_effect：批标签阶跃（离散，vs drift 连续）+ 本孔属越限批、符号一致 ----
    #
    # 归因侧交叉守卫（R3 §1.1/ATT3 P1，第二道防线）：t_batch = 本孔所属批 vs 其余的组均差 t，
    # 对**被误标的干净批**系统为正号；而 QC 已算出符号正确的定向估计 shift_hat（batch_shift
    # check 证据，实测 ≈−0.185，异常批读**低**）。故加 sign(t_batch)==sign(shift_hat) 一致性门：
    # 符号相反（干净批那半）→ batch_effect 不给分，把误标批挡在归因门外，零额外计算（只读证据）。
    #   ① 定向含义：shift_hat 的符号即"异常批相对其余的偏移方向"。降低型污染 shift_hat<0 ⇒
    #      异常批 t_batch<0；升高型污染是**镜像**——shift_hat>0 ⇒ 异常批 t_batch>0，同一符号门
    #      自动适配（不写死"读低"，方向完全由 shift_hat 携带）。
    #   ② 定位：这是主锚（checks.py 哨兵绝对参考选批）之外的第二道防线——即便上游选批被绕过、
    #      干净批孔仍进到归因，此门仍拦截其 batch_effect 误落点；主锚配套后保留，冗余而非重复。
    # shift_hat 缺失（record-only/未触发）→ 不设门（回退旧行为），只在有定向信号时才据符号拦截。
    obs_batch = obs.material_meta.solution_batch
    t_batch = _group_t(frame["resid"], frame["batch"] == obs_batch, float(plate.resid_scale))
    bchk = _check(report, "batch_shift")
    # 仅当 batch_shift check **已触发**（幅度+显著门过 → shift_hat 是可信定向估计）才据其符号设门。
    # 未触发时 shift_hat 可能是噪声（尤 ≥3 批时 WLS 只比较标签极值批、漏中间被注入批，符号不稳），
    # 此时不设门、回退旧行为——守卫只针对"检出确凿、方向可能被上游选反"这一情形。
    batch_fired = bool(bchk is not None and bchk.evidence.get("fired"))
    shift_hat_ev = bchk.evidence.get("shift_hat") if bchk is not None else None
    shift_hat_sign_ok = True
    if batch_fired and _finite(shift_hat_ev) and shift_hat_ev != 0.0 and t_batch != 0.0:
        shift_hat_sign_ok = bool(np.sign(t_batch) == np.sign(shift_hat_ev))
    batch_board = abs(t_batch) >= SIG_Z and not direct and shift_hat_sign_ok
    batch_sign_ok = (r_i != 0 and np.sign(r_i) == np.sign(t_batch) and t_batch != 0
                     and shift_hat_sign_ok)
    qc = _sig(abs(t_batch), SIG_Z, BATCH_Z_FULL)
    main = 1.0 if batch_sign_ok else 0.0
    anchor = 1.0  # 离散批标签阶跃（与 drift 连续互斥；drift 单轮不可辨见下）
    _emit("batch_effect", batch_board, batch_sign_ok, qc, main, anchor, round(t_batch, 4),
          {"batch": obs_batch, "batch_t": round(t_batch, 4), "r_i": round(r_i, 5),
           "shift_hat": round(float(shift_hat_ev), 5) if _finite(shift_hat_ev) else None,
           "shift_hat_sign_ok": shift_hat_sign_ok})

    # ---- instrument_drift：单轮不可辨识（候选去身份残差抵消漂移、5 哨兵传感器信噪不足，
    #      §2.2/§6 record-only；漂移须跨轮累积才可归因）→ 单轮 board_sig 恒 False，落 inconclusive ----
    drift_sent = _sentinel_drift(frame)
    cap_extreme = 0.0
    _emit("instrument_drift", False, False, 0.0, 0.0, 0.0, round(drift_sent, 4),
          {"drift_sentinel": round(drift_sent, 4), "batch_t": round(t_batch, 4),
           "capture_index": obs.instrument_meta.capture_index,
           "single_round": "unidentifiable (needs cross-round, §2.2)"})
    return res


# ================================================================ 主入口

def attribute(
    obs: ObservationObject,
    report: QCReport,
    plate: PlateContext,
    exp: ExperimentObject,
    seed: int = 0,
) -> FailureAttribution:
    """为一条观测归因（纯函数、只读、确定性）。

    流程：签名评分（§2.2/§2.3）→ 归一化伪后验 → top 反驳器门控（§2.4）→
    FLOOR/MARGIN 判定（§2.7）。反驳未过或证据不足 → top_cause=None（inconclusive）。
    """
    if exp.layout is None:
        raise AttributionError(f"exp {exp.exp_id} 无 layout，无法归因")

    frame = _board_frame(plate, exp)
    sp = _spatial_stats(frame, float(plate.resid_scale))
    scored = _score_hypotheses(obs, report, plate, frame, sp)

    total = sum(v["score"] for v in scored.values())
    order = sorted(CAUSES, key=lambda c: scored[c]["score"], reverse=True)
    p = {c: (scored[c]["score"] / total if total > 0 else 0.0) for c in CAUSES}
    top, second = order[0], order[1]
    p_top, p_second = p[top], p[second]

    # top 反驳器门控（仅在 top 有正分时跑）
    refute: dict[str, Any] = {}
    passed = False
    if scored[top]["score"] > 0.0:
        refute = _refute_top(top, frame, sp, obs, report, seed)
        passed = bool(refute.get("passed", False))
    scored[top]["evidence"]["refuter"] = refute if refute else "not_run"

    inconclusive = (p_top < FLOOR) or (p_top - p_second < MARGIN) or (not passed)
    top_cause = None if inconclusive else top

    hypotheses = [
        FailureHypothesis(
            cause=c,
            score=round(float(p[c]), 6),
            evidence=scored[c]["evidence"],
            remedy=_REMEDY[c][0],
        )
        for c in order
    ]
    return FailureAttribution(
        hypotheses=hypotheses,
        top_cause=top_cause,
        confidence=round(float(p_top), 6),
    )


def propose_action(
    obs: ObservationObject, attribution: FailureAttribution | None
) -> RecommendedAction | None:
    """按归因结论装配内生动作（§2.5/§4.2；纯函数、只读，不写 store）。

    detour（REMEASURE/DISAMBIGUATION_REPEAT）→ semantics="detour"、supersedes=[obs_id]、
    顶替旧观测；addition（ADD_CONTROLS/REPEAT_CANDIDATE）→ semantics="addition"、纯扩展。
    DISAMBIGUATION_REPEAT 带 placement_hint="center_only"。inconclusive → DISAMBIGUATION_REPEAT。
    """
    if attribution is None:
        return None  # TRUSTED 观测无内生动作

    cause = attribution.top_cause
    if cause is None:
        action, semantics, extra = _INCONCLUSIVE
        reason = "inconclusive：证据不足/反驳未过，通用消歧（钉中心+跨批复测）"
    else:
        action, semantics, extra = _REMEDY[cause]
        reason = f"归因 top_cause={cause} → {action.value}（{semantics}）"

    detour = semantics == "detour"
    params: dict[str, Any] = {
        "semantics": semantics,
        "target_obs": obs.obs_id,
        "target_cand": obs.cand_id,
        "supersedes": [obs.obs_id] if detour else [],
        "created_by_action_id": None,  # ★反向账占位，衍生 obs 创建时由 M7 回填
    }
    params.update(extra)
    return RecommendedAction(action=action, params=params, reason=reason)
