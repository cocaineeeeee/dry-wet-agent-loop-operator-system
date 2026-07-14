"""页：三臂对比 —— best-so-far 曲线叠加 + regret 表（读各 run 的 report/score.json）。

范围：扫描所选 run 的**同级目录**下所有 run 的 report/score.json。
从 run 目录名解析 scenario（确定性命名 `scenario__arm__seed`）后**按 scenario 分面**。
若同级仅一个 run（或都无 score.json），显示指引文案而非报错。
只读：仅经 _common 缓存读方法（report/score.json 直读）；不写、不读 truth/。

Simpson 型偏差防护（红队 I-1 活证据：robust 只覆盖自选 r3 场景、从不碰 S4，跨场景
按 arm 池化会让"最优"只是覆盖偏差的假象）：默认按 scenario 分面比较；仅当各 arm 场景
覆盖一致时池化才可信，否则显著警示并标注各 arm 覆盖差异。
"""

from __future__ import annotations

import os
import sys
from statistics import mean, pstdev

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import streamlit as st  # noqa: E402

st.title("三臂对比（best-so-far 叠加 · regret 表 · 按场景分面）")


def _scenario_of(run_dir_name: str) -> str:
    """从 run 目录名解析 scenario（确定性命名 scenario__arm__seed，取第一段）。"""
    parts = run_dir_name.split("__")
    return parts[0] if len(parts) >= 2 and parts[0] else "(未解析)"


run_root = C.sidebar_run_selection()
if run_root is None:
    st.info("请在左侧选择一个有效的 run。")
    st.stop()

siblings = C.sibling_runs(run_root)
scored = [(d, s) for d in siblings if (s := C.score_for(d)) is not None]

st.caption(f"扫描目录：{run_root.parent} · 同级 run {len(siblings)} 个 · 含 score.json {len(scored)} 个")

if len(scored) < 2:
    st.info(
        "本页需要「run 根目录含多个带 report/score.json 的 run」才能做三臂对比。\n\n"
        "指引：\n"
        "- 当前所选 run 的同级目录下未发现 ≥2 个已评分 run。\n"
        "- 请把侧栏「runs 根目录」指向一个 sweep 输出目录（例如 "
        "`runs/pilot_sweep/runs` 或 `runs/full_sweep/runs`），其中每个 arm×seed×scenario "
        "子目录各含 `report/score.json`，再选择其中任一 run。\n"
        "- 单臂/单 run 或尚未评分时本页仅显示本提示（不报错）。"
    )
    st.stop()

import pandas as pd

# --------------------------------------------------- 解析 scenario + 覆盖矩阵
tagged = [(d, s, _scenario_of(d.name)) for d, s in scored]

arm_scen: dict[str, set[str]] = {}
by_arm_scn: dict[tuple[str, str], list[dict]] = {}
for _d, s, sc in tagged:
    arm = s.get("arm") or "unknown"
    arm_scen.setdefault(arm, set()).add(sc)
    by_arm_scn.setdefault((arm, sc), []).append(s)

arms = sorted(arm_scen)
scenarios = sorted({sc for scs in arm_scen.values() for sc in scs})
uniform = len({frozenset(v) for v in arm_scen.values()}) <= 1

# 覆盖矩阵（scenario × arm 的 cell 计数）——覆盖不齐一眼可见。
cov_rows = []
for sc in scenarios:
    row = {"scenario": sc}
    for arm in arms:
        row[arm] = len(by_arm_scn.get((arm, sc), []))
    cov_rows.append(row)
cov_df = pd.DataFrame(cov_rows).set_index("scenario")

if not uniform:
    st.error(
        "⚠ 各 arm 的 scenario 覆盖**不一致**——禁止跨场景按 arm 池化比较！\n\n"
        "跨场景池化会引入 Simpson 型覆盖偏差：某臂 regret 更低可能只因它覆盖了更少/更易的"
        "场景（如 robust 只跑自选 r3 场景、从不碰 S4）。本页默认按 scenario 分面比较；"
        "下方「池化 arm 表」仅在覆盖一致时才可信，此处已标注各臂覆盖场景数，跨场景读数请谨慎。"
    )
else:
    st.success("各 arm 的 scenario 覆盖一致，跨场景池化比较可信。")

with st.expander(f"场景覆盖矩阵（{len(scenarios)} scenario × {len(arms)} arm，格内为 cell 数）",
                 expanded=not uniform):
    st.dataframe(cov_df, width="stretch")
    st.caption("0 表示该 arm 未覆盖该 scenario——存在 0 即覆盖不齐，跨场景池化会误导。")

# --------------------------------------------------- best-so-far 叠加（按场景）
st.subheader("best-so-far 叠加（各 arm 逐轮平均 best_true_so_far）")
_POOL_OPT = "＊全部场景（混池·覆盖不齐时慎读）"
scn_choice = st.selectbox(
    "场景（分面）", scenarios + [_POOL_OPT], key="compare_curve_scenario",
    help="默认按单一 scenario 比较各 arm（Simpson 安全）；选混池仅在覆盖一致时可信。",
)
if scn_choice == _POOL_OPT:
    if not uniform:
        st.warning("当前为跨场景混池且覆盖不齐——曲线可能反映覆盖差异而非臂优劣，仅供参考。")
    curve_runs = {
        arm: [s for sc in scenarios for s in by_arm_scn.get((arm, sc), [])]
        for arm in arms
    }
else:
    curve_runs = {arm: by_arm_scn.get((arm, scn_choice), []) for arm in arms}

curve_cols: dict[str, dict[int, float]] = {}
for arm, runs in curve_runs.items():
    per_round: dict[int, list[float]] = {}
    for s in runs:
        for rd in s.get("rounds") or []:
            v = rd.get("best_true_so_far")
            if v is not None:
                per_round.setdefault(rd.get("round"), []).append(v)
    curve_cols[arm] = {r: mean(vs) for r, vs in per_round.items() if vs}

if any(curve_cols.values()):
    all_rounds = sorted({r for m in curve_cols.values() for r in m})
    curve_df = pd.DataFrame(
        {arm: [m.get(r) for r in all_rounds] for arm, m in sorted(curve_cols.items()) if m},
        index=all_rounds,
    )
    curve_df.index.name = "round"
    st.line_chart(curve_df)
    label = "全部场景混池" if scn_choice == _POOL_OPT else f"场景 {scn_choice}"
    st.caption(f"每条线为该 arm 在【{label}】下所有 seed×scenario run 逐轮 best_true_so_far 均值。")
else:
    st.caption("所选场景下 score.json 内无逐轮 rounds[].best_true_so_far，无法绘制曲线。")


def _regret_row(runs: list[dict]) -> dict:
    regrets = [s.get("final_regret") for s in runs if s.get("final_regret") is not None]
    contam = [
        s.get("contaminated_in_training")
        for s in runs
        if s.get("contaminated_in_training") is not None
    ]
    wrong = sum(1 for s in runs if s.get("wrong_optimum_hit_any"))
    return {
        "n_cells": len(runs),
        "mean_final_regret": round(mean(regrets), 4) if regrets else None,
        "sd_final_regret": round(pstdev(regrets), 4) if len(regrets) > 1 else 0.0,
        "mean_contaminated": round(mean(contam), 3) if contam else None,
        "wrong_optimum_hits": wrong,
    }


# --------------------------------------------------- regret 表（默认：按场景分面）
st.subheader("regret 表 · 按 scenario 分面（Simpson 安全 · 默认）")
faceted_rows = []
for sc in scenarios:
    for arm in arms:
        runs = by_arm_scn.get((arm, sc), [])
        if not runs:
            continue
        faceted_rows.append({"scenario": sc, "arm": arm, **_regret_row(runs)})
st.dataframe(pd.DataFrame(faceted_rows), width="stretch")
st.caption(
    "同一 scenario 内跨 arm 比较才免于覆盖偏差。mean_final_regret 越小越好；"
    "mean_contaminated=训练集中被污染观测占比；wrong_optimum_hits=命中错误最优的 run 数。"
)

# --------------------------------------------------- 池化 arm 表（覆盖一致才可信）
st.subheader("池化 arm 表（跨场景聚合 · 仅覆盖一致时可信）")
if not uniform:
    st.warning(
        "各 arm 覆盖不齐——本表跨场景池化，读数可能是 Simpson 型覆盖偏差的假象，"
        "已附 n_scenarios / covered_scenarios 供核对，请以上方分面表为准。"
    )
pooled_rows = []
for arm in arms:
    runs = [s for sc in arm_scen[arm] for s in by_arm_scn.get((arm, sc), [])]
    pooled_rows.append(
        {
            "arm": arm,
            "n_scenarios": len(arm_scen[arm]),
            **_regret_row(runs),
            "covered_scenarios": ", ".join(sorted(arm_scen[arm])),
        }
    )
st.dataframe(pd.DataFrame(pooled_rows), width="stretch")
st.caption("对齐族6 §T3。n_scenarios/covered_scenarios 暴露覆盖差异——覆盖不齐时勿据此判臂优劣。")
