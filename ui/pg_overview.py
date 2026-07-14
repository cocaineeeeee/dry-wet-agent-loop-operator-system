"""页：运行总览 —— checkpoint / 预算 / 轮次 / 最优 / 信任计数。

只读：仅经 _common 的缓存读方法（RunStore create=False + JSON 直读），不写、不读 truth/。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import streamlit as st  # noqa: E402

st.title("运行总览（只读）")

run_root = C.sidebar_run_selection()
if run_root is None:
    st.info("请在左侧选择一个有效的 run。")
    st.stop()

view = C.load_view_for(run_root)
obs = view["observations"]
ckpt = view.get("checkpoint") or {}
direction = C.direction_of(view)

# ------------------------------------------------------------- 顶部指标卡
counts = C.trust_counts(obs)
best_val, best_obs = C.best_trusted(obs, direction)
budget = (ckpt.get("budget") or {}) if ckpt else {}
completed = ckpt.get("completed_rounds")

def _budget_pair(a, b) -> str:
    """预算分数渲染：缺键回退 '—'，绝不渲染字面 'None/None'（P3）。"""
    if a is None and b is None:
        return "—"
    return f"{'—' if a is None else a}/{'—' if b is None else b}"


c1, c2, c3, c4 = st.columns(4)
c1.metric("已完成轮次", completed if completed is not None else "—")
if budget:
    wu, wt = budget.get("wells_used"), budget.get("wells_total")
    # 有 total 显示 used/total；仅有 used 显示 used；都缺显示 —。
    c2.metric("孔位预算", _budget_pair(wu, wt) if wt else ("—" if wu is None else str(wu)))
    c3.metric("轮次预算", _budget_pair(budget.get("rounds_used"), budget.get("rounds_total")))
else:
    c2.metric("孔位预算", "—")
    c3.metric("轮次预算", "—")
c4.metric(f"最优可信值（{direction}）", f"{best_val:.4f}" if best_val is not None else "—")

if budget and budget.get("wells_total"):
    st.progress(min(1.0, (budget.get("wells_used") or 0) / budget["wells_total"]))

# ------------------------------------------------------------- 信任计数
st.subheader("信任计数（全部观测）")
t1, t2, t3, t4 = st.columns(4)
t1.metric("TRUSTED", counts.get("TRUSTED", 0))
t2.metric("SUSPECT", counts.get("SUSPECT", 0))
t3.metric("FAILED", counts.get("FAILED", 0))
t4.metric("PENDING", counts.get("PENDING", 0))
st.caption(f"观测总数 {len(obs)}；实验轮 {len(view['experiments'])} 个。")

if best_obs is not None:
    lm = best_obs.get("layout_meta") or {}
    st.caption(
        f"最优可信非对照观测：obs={best_obs.get('obs_id')} · "
        f"cand={best_obs.get('cand_id')} · 孔={lm.get('well_id')} · "
        f"轮={best_obs.get('round_id')}"
    )

# ------------------------------------------------------------- 规划器阶段
planner = ckpt.get("planner") if ckpt else None
if planner:
    st.subheader("规划器状态")
    st.json(planner)

# ------------------------------------------------------------- 逐轮曲线
st.subheader("逐轮进展")
stats = C.per_round_stats(obs, direction)
if not stats:
    st.info("该运行暂无观测数据。")
else:
    import pandas as pd

    df = pd.DataFrame(stats).set_index("round_id")
    a, b = st.columns(2)
    with a:
        st.markdown("**每轮 TRUSTED 非对照观测数**")
        st.line_chart(df[["n_trusted_noncontrol"]])
    with b:
        st.markdown(f"**best-so-far（{direction}）：naive 视角 vs trusted 视角**")
        st.line_chart(df[["naive_best_so_far", "trusted_best_so_far"]])
        st.caption(
            "naive=全部非对照观测（轻信一切，易被伪影抬高）；"
            "trusted=仅 TRUSTED 非对照（稳健视角，§13.7）。全 TRUSTED 时两线重合。"
        )

# ------------------------------------------------------------- 模型指纹
snapshots = C.snapshots_for(run_root)
st.subheader("模型指纹历史（models/snapshot_r*.json）")
if snapshots:
    import pandas as pd

    st.dataframe(pd.DataFrame(snapshots), width="stretch")
else:
    st.caption("暂无模型快照。")
