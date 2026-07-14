"""AgentBackend 协议 + 确定性模板后端（docs/ARCHITECTURE.md §10.2，REFERENCE_MAP §13.6）。

接口形状借自 bluesky-adaptive 的 ingest/suggest（旧名 tell/ask），但**只借形状、不借写权**：
suggest 的产物是 DecisionRecord **返回值**（actor=agent、kind∈PROPOSAL_KINDS），
由 loop 走 lifecycle.submit_proposal 落账、由 planner 的 validate_proposal 仲裁——
agent 侧没有任何 item_add / direct_to_queue 式的写通道（对照 §13.6 的公理 7 缺口）。

结构性边界（公理 7）：本模块只消费 kernel/store.py 导出的 ReadOnlyRunView，
**不 import** RunStore 的写方法、lifecycle 裁决函数、adapters、planner 或 models，
自身也不定义/导出任何 save/append/write/delete/update/remove 型公有 API（守门测试强制）。
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, runtime_checkable

from expos.kernel.objects import (
    Actor,
    ActionType,
    DecisionKind,
    DecisionRecord,
)
from expos.kernel.store import ReadOnlyRunView
from expos.kernel.objects import TrustLevel


# 方向关键词（目标翻译用；确定性、无外部依赖）
_MAX_KW = ("最大化", "最大", "提高", "提升", "增大", "maximize", "max")
_MIN_KW = ("最小化", "最小", "降低", "减小", "减少", "minimize", "min")
# "N 轮 / N round(s)" 预算正则
_ROUNDS_RE = re.compile(r"(\d+)\s*(?:轮|rounds?)", re.IGNORECASE)


def _det_id(prefix: str, *parts: str) -> str:
    """从稳定输入派生确定性 id（同输入同 id）——替代 uuid 随机默认，保证可复现。"""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:10]}"


@runtime_checkable
class AgentBackend(Protocol):
    """agent 后端协议（§10.1 七职责的接口面）：

    - ingest 吸收只读证据；
    - translate_goal 自然语言目标 → GOAL_TRANSLATION 提案（职责 1）；
    - propose_priors 推理先验/规划理由 → PRIOR_PROPOSAL 提案（职责 2）；
    - explain_verdict QC/归因人类可读解释（职责 3）；
    - narrate_round 下轮理由叙述 ROUND_RATIONALE（职责 4 叙述半）；
    - suggest 下轮动作提案 ACTION_PROPOSAL（职责 4 提案半）。
    全部只**返回** DecisionRecord/文本，落盘由 loop 走内核统一入口（职责 5，§10.3）。
    """

    def ingest(self, view: ReadOnlyRunView) -> None: ...

    def translate_goal(self, text: str, domain_names: list[str]) -> DecisionRecord: ...

    def propose_priors(
        self, view: ReadOnlyRunView, round_id: int
    ) -> list[DecisionRecord]: ...

    def suggest(
        self, view: ReadOnlyRunView, round_id: int, batch_size: int = 3
    ) -> list[DecisionRecord]: ...

    def narrate_round(
        self, view: ReadOnlyRunView, round_id: int, n_submitted: int = 0
    ) -> DecisionRecord: ...

    def explain_verdict(self, view: ReadOnlyRunView, obs_id: str) -> str: ...


class TemplateBackend:
    """确定性规则模板后端（M8 §10.1 七职责全量版）。

    - ingest：无状态，纯占位（模板后端不累积记忆，同 view 同输出）。
    - translate_goal（职责 1）：关键词匹配域/方向 + 正则抓轮数预算 → GOAL_TRANSLATION；
      无法匹配域时给 needs_clarification=true 的提案（永不抛错，裁定权在 planner/human）。
    - propose_priors（职责 2）：从 experiments 的 design_space 变量 transform 推断先验建议
      （log 维→细扫），每域最多 2 条 PRIOR_PROPOSAL。
    - explain_verdict（职责 3）：证据填槽模板产中文叙述（引用 QCCheck 名/嫌疑分 + 归因结论）。
    - narrate_round（职责 4 叙述半）：本轮中文叙述（n_trusted/suspect、top 归因计数、
      最优可信值），引用真实数字，无数字时明说。Action-count clause reports two distinct
      numbers — identified suggestions vs. actually submitted proposals (n_submitted,
      supplied by the caller from its real submit_proposal count; NARR3 red-team fix,
      see narrate_round docstring) — the two can differ because submission is capped
      by batch_size.
    - suggest（职责 4 提案半）：对 SUSPECT 且带 next_action 的观测生成 ACTION_PROPOSAL。
    全部 decision_id 由内容派生、created_at 取 view/时钟无关量——同输入逐字段相等。离线、零外部依赖。
    """

    def ingest(self, view: ReadOnlyRunView) -> None:
        # 模板后端无内部状态：证据在每次调用时直接读 view。
        return None

    # ------------------------------------------------------------ 职责 1：目标翻译

    def translate_goal(self, text: str, domain_names: list[str]) -> DecisionRecord:
        """自然语言目标 → GOAL_TRANSLATION 提案。

        关键词匹配域名（domain_names 中第一个作为子串出现者）/方向（最大化/最小化/
        maximize/minimize）/预算数字（正则抓 "N 轮 / N rounds"）→ content={domain,
        direction, rounds, unmatched_terms}。无法匹配域 → content 带 needs_clarification=true
        （不抛错——翻译永远给出提案，裁定权在 planner/human）。
        """
        low = text.lower()

        domain = None
        for d in domain_names:
            if d and (d in text or d.lower() in low):
                domain = d
                break

        direction = None
        if any(k.lower() in low for k in _MAX_KW):
            direction = "maximize"
        elif any(k.lower() in low for k in _MIN_KW):
            direction = "minimize"

        m = _ROUNDS_RE.search(text)
        rounds = int(m.group(1)) if m else None

        # unmatched_terms：未被域/方向/轮数吸收的词元（确定性、保序去重）
        matched_bits: list[str] = []
        if domain:
            matched_bits.append(domain.lower())
        if direction:
            matched_bits.extend(k.lower() for k in (_MAX_KW if direction == "maximize" else _MIN_KW))
        if m:
            matched_bits.append(m.group(0).lower())
        tokens = re.findall(r"[\w一-鿿]+", low)
        unmatched: list[str] = []
        for tok in tokens:
            if any(tok in mb or mb in tok for mb in matched_bits if mb):
                continue
            if tok not in unmatched:
                unmatched.append(tok)

        content: dict = {
            "domain": domain,
            "direction": direction,
            "rounds": rounds,
            "unmatched_terms": unmatched,
        }
        if domain is None:
            content["needs_clarification"] = True

        return DecisionRecord(
            decision_id=_det_id("dec", "goal", text, "|".join(domain_names)),
            round_id=0,
            actor=Actor.AGENT,
            kind=DecisionKind.GOAL_TRANSLATION,
            refs=[],
            content=content,
            created_at="",
        )

    # ------------------------------------------------------------ 职责 2：先验提议

    def propose_priors(
        self, view: ReadOnlyRunView, round_id: int
    ) -> list[DecisionRecord]:
        """PRIOR_PROPOSAL：基于 view 的简单统计模板——从 experiments 的 design_space
        变量 transform 推断（log 维观测跨度大→建议细扫），每域最多 2 条。"""
        proposals: list[DecisionRecord] = []
        per_domain: dict[str, int] = {}
        seen: set[tuple[str, str]] = set()
        for exp in view.experiments:
            domain = exp.domain
            for var in exp.design_space.variables:
                if var.transform != "log":
                    continue
                key = (domain, var.name)
                if key in seen:
                    continue
                seen.add(key)
                if per_domain.get(domain, 0) >= 2:
                    continue
                per_domain[domain] = per_domain.get(domain, 0) + 1
                span = None
                if var.low is not None and var.high is not None and var.low > 0:
                    span = round(var.high / var.low, 3)
                reason = (
                    f"变量 {var.name} 为 log 尺度"
                    + (f"、量级跨度 ×{span}" if span is not None else "")
                    + "，观测跨度大，建议在 log 尺度上细扫以避免欠采样。"
                )
                content = {
                    "domain": domain,
                    "variable": var.name,
                    "transform": var.transform,
                    "suggestion": "log_fine_scan",
                    "span_ratio": span,
                    "reason": reason,
                }
                proposals.append(
                    DecisionRecord(
                        decision_id=_det_id("dec", "prior", domain, var.name),
                        round_id=round_id,
                        actor=Actor.AGENT,
                        kind=DecisionKind.PRIOR_PROPOSAL,
                        refs=[exp.exp_id],
                        content=content,
                        created_at=view.exported_at,
                    )
                )
        return proposals

    # ------------------------------------------------------------ 职责 4：下轮叙述

    def narrate_round(
        self, view: ReadOnlyRunView, round_id: int, n_submitted: int = 0
    ) -> DecisionRecord:
        """ROUND_RATIONALE（非提案类，actor=agent）：本轮中文叙述。

        引用真实数字：n_trusted/n_suspect、top 归因原因计数、最优可信值。无对应数字
        时明说（不编造）。同 view 同输出（确定性），除 n_submitted 外——n_submitted 由
        调用方（TemplateAgentPolicy.after_round）传入其本轮真实 submit_proposal 计数，
        非从 view 派生（NARR3 red-team fix, mailbox/red_to_blue/029）：view 里带有效
        next_action 的观测数（n_queued_actions）只是"识别出的建议动作数"，实际提交会被
        batch_size 封顶，二者可以不等——旧文案把 n_queued_actions 说成"已入队"是语义
        过报（28.6% 轮次叙述值 > 实际提交数，最极端 47 vs 3）。narrative 现分述两个数：
        identified suggestions vs. actually submitted proposals.
        """
        obs_round = [o for o in view.observations if o.round_id == round_id]
        trusted = [o for o in obs_round if o.trust == TrustLevel.TRUSTED]
        suspect = [o for o in obs_round if o.trust == TrustLevel.SUSPECT]
        n_trusted, n_suspect = len(trusted), len(suspect)

        # top 归因原因计数（跨 SUSPECT/FAILED 的 failure_attr.top_cause）
        cause_counts: dict[str, int] = {}
        for o in obs_round:
            fa = o.failure_attr
            if fa is not None and fa.top_cause:
                cause_counts[fa.top_cause] = cause_counts.get(fa.top_cause, 0) + 1
        top_cause = None
        top_cause_n = 0
        if cause_counts:
            top_cause, top_cause_n = sorted(
                cause_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )[0]

        # 最优可信值（可信观测中 value 最大者；无则明说）
        trusted_vals = [
            o.result.value for o in trusted if o.result.value is not None
        ]
        best_val = max(trusted_vals) if trusted_vals else None

        # 下一轮已入队动作数（带有效 next_action 的观测）
        n_queued = sum(
            1
            for o in obs_round
            if o.next_action is not None and o.next_action.action != ActionType.NONE
        )

        best_txt = f"{best_val:.4g}" if best_val is not None else "暂无可信观测值可报"
        cause_txt = (
            f"最常见归因为 {top_cause}（{top_cause_n} 例）"
            if top_cause is not None
            else "尚无可归因的可信原因"
        )
        # Action-count clause rewritten (NARR3 red-team fix, P2): n_queued is merely
        # "identified suggestions", not "enqueued actions" — real submission is capped
        # by TemplateAgentPolicy.batch_size, so it must be reported as its own number
        # (n_submitted, supplied by the caller) rather than derived/implied from n_queued.
        narrative = (
            f"第 {round_id} 轮：可信 {n_trusted} 条、嫌疑 {n_suspect} 条；"
            f"{cause_txt}；最优可信值 {best_txt}；"
            f"identified {n_queued} suggested action(s); submitted {n_submitted} "
            f"as pending proposal(s) this round."
        )

        content = {
            "round_id": round_id,
            "n_trusted": n_trusted,
            "n_suspect": n_suspect,
            "top_cause": top_cause,
            "top_cause_count": top_cause_n,
            "cause_counts": cause_counts,
            "best_trusted_value": best_val,
            "n_queued_actions": n_queued,
            "n_submitted": n_submitted,
            "narrative": narrative,
        }
        return DecisionRecord(
            decision_id=_det_id("dec", "narr", str(round_id), view.exported_at),
            round_id=round_id,
            actor=Actor.AGENT,
            kind=DecisionKind.ROUND_RATIONALE,
            refs=[o.obs_id for o in obs_round],
            content=content,
            created_at=view.exported_at,
        )

    def suggest(
        self, view: ReadOnlyRunView, round_id: int, batch_size: int = 3
    ) -> list[DecisionRecord]:
        proposals: list[DecisionRecord] = []
        for obs in view.observations_by_trust(TrustLevel.SUSPECT):
            action = obs.next_action
            if action is None or action.action == ActionType.NONE:
                continue
            target = obs.cand_id or obs.control_id or obs.obs_id
            reason = (
                action.reason
                or f"观测 {obs.obs_id} 判为 SUSPECT，建议对 {target} 执行 {action.action.value}。"
            )
            content = {
                "action": action.action.value,
                "target": target,
                "obs_id": obs.obs_id,
                "params": dict(action.params),
                "reason": reason,
            }
            proposals.append(
                DecisionRecord(
                    decision_id=_det_id("dec", obs.obs_id, str(round_id), action.action.value),
                    round_id=round_id,
                    actor=Actor.AGENT,
                    kind=DecisionKind.ACTION_PROPOSAL,
                    refs=[obs.obs_id],
                    content=content,
                    created_at=view.exported_at,
                )
            )
            if len(proposals) >= batch_size:
                break
        return proposals

    def explain_verdict(self, view: ReadOnlyRunView, obs_id: str) -> str:
        obs = next((o for o in view.observations if o.obs_id == obs_id), None)
        if obs is None:
            return f"未在当前视图中找到观测 {obs_id}，无法解释裁决。"
        if obs.qc is None or not obs.qc.checks:
            # EV3 fix (NARR3 red-team P3, mailbox/red_to_blue/029): the old text always
            # said "kept pending per Axiom 2" here, which self-contradicts once trust is
            # already TRUSTED/SUSPECT/FAILED with no check evidence recorded (a verdict
            # was reached; nothing is "pending"). That combination is unreachable on the
            # full corpus (0/90240 obs) but the text must still be self-consistent for it.
            if obs.trust == TrustLevel.PENDING:
                return (
                    f"observation {obs_id} has no QC evidence yet (trust="
                    f"{obs.trust.value}); per Axiom 2 it remains pending — no check "
                    f"items to cite."
                )
            return (
                f"observation {obs_id} was verdicted {obs.trust.value} with no QC "
                f"check evidence recorded — an unusual combination with nothing to "
                f"cite here."
            )
        parts = []
        for c in obs.qc.checks:
            verdict = "通过" if c.passed else "未通过"
            parts.append(f"{c.name}（{c.level} 级，{verdict}，嫌疑分 {c.score:.2f}）")
        routing = obs.routing.value if obs.routing else "未路由"
        base = (
            f"观测 {obs_id} 裁定为 {obs.trust.value}、处置 {routing}："
            f"依据 QC 检查 {'；'.join(parts)}。"
        )
        return base + self._attribution_clause(obs)

    def _attribution_clause(self, obs) -> str:
        """归因子句（职责 3 后半）：引用 failure_attr 的 top_cause/confidence，
        以及 top 假设的反驳器结论（evidence['refuter'] 键存在时）。无归因则空串。"""
        fa = obs.failure_attr
        if fa is None or not fa.hypotheses:
            return ""
        if fa.top_cause is None:
            return (
                f"归因未收敛（inconclusive，top 伪后验 {fa.confidence:.2f}）："
                f"证据不足或反驳器未过，按纪律不指认单一原因，建议消歧复现。"
            )
        top = fa.hypotheses[0]
        clause = f"失败归因：top_cause={fa.top_cause}（伪后验 {fa.confidence:.2f}）"
        refuter = top.evidence.get("refuter")
        if isinstance(refuter, dict):
            passed = refuter.get("passed")
            mode = refuter.get("mode", "placebo+subsample")
            if passed is not None:
                clause += (
                    f"，反驳器（{mode}）"
                    + ("通过、结论稳健" if passed else "未过、结论存疑")
                )
        return clause + "。"
