"""页：裁决日志 —— 提案/裁定配对着色 + ROUND_RATIONALE 叙述流 + 事件 tail。

数据来源：RunStore.export_view() 已把 events.jsonl 中 kind="decision" 的载荷解析为
DecisionRecord（本项目的「decisions.jsonl」即事件日志内的 decision 事件流）。
只读：仅经 _common 缓存读方法；不写、不读 truth/。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import streamlit as st  # noqa: E402

# 复用内核裁决语义（只读常量，非写句柄）：仅 planner/human 有裁决权（公理7）。
# UI 是 lifecycle._resolutions 口径的只读展示端——采纳状态从配对裁定推导，绝不信
# 提案自报的 accepted 字段（agent 自裁定会伪造它）。
from expos.kernel.lifecycle import ADJUDICATOR_ACTORS  # noqa: E402

# Actor(str, Enum)：其字符串值与 export_view 落盘的 actor 字段可直接比较。
_ADJUDICATOR_ACTOR_VALUES = frozenset(a.value for a in ADJUDICATOR_ACTORS)

_ACCEPT_BG = "background-color: rgba(46,125,50,0.18)"    # 绿：已采纳
_REJECT_BG = "background-color: rgba(198,40,40,0.18)"    # 红：已否决
_PEND_BG = "background-color: rgba(158,158,158,0.15)"    # 灰：未裁定

# 机制活性观测面（ARCH_V2 §2 只读活性观测面）：risk_map_applied / aggregation_alpha /
# config_drift 三个 kind，事件 tail 的「机制活性」快捷过滤项与人读摘要据此识别。
MECHANISM_ACTIVITY_KINDS = {"risk_map_applied", "aggregation_alpha", "config_drift"}


def _event_summary(e: dict) -> str:
    """为机制活性观测面事件生成一句人读摘要；其余 kind 返回空串（原样不展开）。"""
    kind = e.get("kind")
    payload = e.get("payload") or {}
    if kind == "risk_map_applied":
        if payload.get("is_none"):
            return "风险图缺席（is_none）"
        n_wells = payload.get("n_wells")
        n_distinct = payload.get("n_distinct")
        mean = payload.get("mean")
        mean_s = f"{mean:.3f}" if isinstance(mean, (int, float)) else "?"
        return f"风险图：{n_wells} 孔 / {n_distinct} 个不同值 / 均值 {mean_s}"
    if kind == "aggregation_alpha":
        entries = payload.get("entries") or []
        agg = payload.get("aggregation") or "?"
        return f"alpha 条目 {len(entries)} 条（{agg}）"
    if kind == "config_drift":
        stored = (payload.get("stored_fingerprint") or "")[:12]
        current = (payload.get("current_fingerprint") or "")[:12]
        return f"配置指纹漂移：{stored}… → {current}…"
    return ""


def _resolution(proposals, verdicts) -> dict[str, str]:
    """把每个提案 decision_id 映射到 采纳/否决/未裁定（复用 lifecycle._resolutions 口径）。

    采纳状态**只从配对裁定推导**，绝不读提案自报的 accepted 字段——否则 agent 自裁定
    会被渲染成"采纳"（公理7：agent 无裁决权）。内核口径：
    - 仅采信 actor∈ADJUDICATOR_ACTORS（planner/human）的 acceptance/rejection；
    - 一裁一案（Q-8）：合法裁定 refs 恒为单元素，refs≠1 的畸形裁定一律忽略
      （UI 只读、不复现内核对畸形记录的响亮抛错）；
    - override 是观测 trust 改判语义、不裁提案，故不计入提案采纳。
    """
    verdict_by_ref: dict[str, str] = {}
    for v in verdicts:
        if v.get("actor") not in _ADJUDICATOR_ACTOR_VALUES:
            continue
        if v.get("kind") not in ("acceptance", "rejection"):
            continue
        refs = v.get("refs") or []
        if len(refs) != 1:
            continue
        verdict_by_ref[refs[0]] = v.get("kind")
    res: dict[str, str] = {}
    for p in proposals:
        did = p.get("decision_id")
        vk = verdict_by_ref.get(did)
        if vk == "acceptance":
            res[did] = "采纳"
        elif vk == "rejection":
            res[did] = "否决"
        else:
            res[did] = "未裁定"
    return res


st.title("裁决日志（提案/裁定配对 · 叙述流 · 事件 tail）")

run_root = C.sidebar_run_selection()
if run_root is None:
    st.info("请在左侧选择一个有效的 run。")
    st.stop()

view = C.load_view_for(run_root)
decisions = view["decisions"]
events = view["events"]

proposals = [d for d in decisions if d.get("kind") in C.PROPOSAL_KINDS]
verdicts = [d for d in decisions if d.get("kind") in C.VERDICT_KINDS]
rationales = [d for d in decisions if d.get("kind") == "round_rationale"]
others = [
    d
    for d in decisions
    if d.get("kind") not in C.PROPOSAL_KINDS
    and d.get("kind") not in C.VERDICT_KINDS
    and d.get("kind") != "round_rationale"
]

# ------------------------------------------------------------- 提案/裁定配对
st.subheader("提案 / 裁定配对")
if proposals or verdicts:
    import pandas as pd

    res = _resolution(proposals, verdicts)
    rows = []
    for p in sorted(proposals, key=lambda d: (d.get("round_id", 0), d.get("created_at", ""))):
        content = p.get("content") or {}
        rows.append(
            {
                "轮次": p.get("round_id"),
                "decision_id": p.get("decision_id"),
                "actor": p.get("actor"),
                "kind": p.get("kind"),
                "裁定": res.get(p.get("decision_id"), "未裁定"),
                "动作/理由": content.get("action") or content.get("reason") or "",
                "refs": ", ".join(p.get("refs") or [])[:60],
            }
        )
    df = pd.DataFrame(rows)

    def _row_style(r):
        verdict = r["裁定"]
        bg = _ACCEPT_BG if verdict == "采纳" else _REJECT_BG if verdict == "否决" else _PEND_BG
        return [bg] * len(r)

    st.caption("行底色：绿=采纳 · 红=否决 · 灰=未裁定（§4.5 审计不变量：提案需配对裁定才生效）")
    try:
        st.dataframe(df.style.apply(_row_style, axis=1), width="stretch", height=320)
    except Exception:
        st.dataframe(df, width="stretch", height=320)

    if verdicts:
        st.markdown("**独立裁定记录（acceptance / rejection / override）**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "轮次": v.get("round_id"),
                        "kind": v.get("kind"),
                        "actor": v.get("actor"),
                        "validator": v.get("validator"),
                        "refs": ", ".join(v.get("refs") or []),
                    }
                    for v in verdicts
                ]
            ),
            width="stretch",
        )
else:
    st.caption("暂无提案/裁定记录——决策层落地前多为空，属预期。")

# ------------------------------------------------------------- ROUND_RATIONALE 叙述流
st.subheader("ROUND_RATIONALE 叙述流")
if rationales:
    for r in sorted(rationales, key=lambda d: d.get("round_id", 0)):
        content = r.get("content") or {}
        rid = content.get("round_id", r.get("round_id"))
        narrative = content.get("narrative") or "(无叙述文本)"
        with st.expander(f"第 {rid} 轮叙述", expanded=False):
            st.markdown(f"> {narrative}")
            meta = {
                k: content.get(k)
                for k in ("n_trusted", "n_suspect", "top_cause", "top_cause_count",
                          "best_trusted_value", "n_queued_actions")
                if k in content
            }
            if meta:
                st.json(meta)
else:
    st.caption("暂无 ROUND_RATIONALE 叙述（agent 叙述半落地前为空，属预期）。")

if others:
    st.subheader("其他决策记录")
    import pandas as pd

    st.dataframe(
        pd.DataFrame(
            [{"轮次": d.get("round_id"), "kind": d.get("kind"), "actor": d.get("actor")}
             for d in others]
        ),
        width="stretch",
    )

# ------------------------------------------------------------- 事件 tail
st.subheader("事件 tail（events.jsonl）")
if events:
    import pandas as pd

    kinds = sorted({e.get("kind") for e in events})
    # 切换到 kind 全集不同的 run（如换选另一 run）时重置过滤选择，避免过滤器沿用
    # 上一个 run 的陈旧 kind 集合、悄悄漏掉新 kind（含下方机制活性三 kind）。
    if st.session_state.get("_event_kinds_universe") != kinds:
        st.session_state["_event_kinds_universe"] = kinds
        st.session_state["event_kinds"] = kinds
        st.session_state.pop("event_kind_quick_mech", None)
    mech_kinds_present = sorted(set(kinds) & MECHANISM_ACTIVITY_KINDS)

    def _apply_mech_quick_filter():
        if st.session_state.get("event_kind_quick_mech"):
            st.session_state["event_kinds"] = mech_kinds_present
        else:
            st.session_state["event_kinds"] = kinds

    if mech_kinds_present:
        st.checkbox(
            "机制活性",
            key="event_kind_quick_mech",
            help="快捷过滤：仅看 risk_map_applied / aggregation_alpha / config_drift",
            on_change=_apply_mech_quick_filter,
        )
    # 初始值/切换 run 后的重置值已由上面写入 session_state["event_kinds"]，此处不再传
    # default=（避免 Streamlit "default 与 Session State 同时设置"告警）。
    chosen = st.multiselect("kind 过滤器", kinds, key="event_kinds")
    filtered = [e for e in events if e.get("kind") in chosen]
    tail = filtered[-200:]
    flat = [
        {
            "seq": e.get("seq"),
            "ts": e.get("ts"),
            "kind": e.get("kind"),
            "摘要": _event_summary(e),
            "payload": json.dumps(e.get("payload", {}), ensure_ascii=False),
        }
        for e in tail
    ]
    st.dataframe(pd.DataFrame(flat), width="stretch", height=360)
    st.caption(f"共 {len(events)} 条事件，显示过滤后尾部 {len(tail)} 条。")
else:
    st.info("暂无事件。")

st.info("人工改判入口：只读仪表盘不提供写入口（§13.13 UI 零写句柄）。")
