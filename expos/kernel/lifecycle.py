"""生命周期状态机 + 信任裁决/路由 + 改判 + 提案配对语义（docs/ARCHITECTURE.md §3/§4.5/§7.4）。

裁决与处置是两个决策：trust 由 QC 证据裁定，routing 决定拿它做什么。
模型更新由 trust 驱动（TRUSTED→响应模型；SUSPECT/FAILED→失败模型正例），
与 routing 处置正交——此处只落枚举与事件，模型本身在 M4/M6 实现。

裁决函数签名只接受 QC 证据与策略，**不接受任何 agent 产物**（公理 7）。
"""

from __future__ import annotations

from dataclasses import dataclass

from expos.kernel.objects import (
    Actor,
    DecisionKind,
    DecisionRecord,
    ExperimentObject,
    ExpStatus,
    ObservationObject,
    PROPOSAL_KINDS,
    QCReport,
    Routing,
    TrustLevel,
)
from expos.kernel.store import RunStore, StoreError


from expos.errors import ExposError


class LifecycleError(ExposError):
    user_facing = False  # 非法迁移/裁决误用多为编程 bug，CLI 保留 traceback



#: 有裁决权的 actor（公理 7：agent 只有建议权）。改判与提案裁定都只认这两个，
#: 且 _resolutions 在事件日志上按 actor 过滤——伪造的 agent 自裁定记录即使被写入
#: 日志也不会被采信（不变量是日志上可机器检查的，不依赖"agent 没有句柄"的带外假设）。
ADJUDICATOR_ACTORS = frozenset({Actor.PLANNER, Actor.HUMAN})


# ---------------------------------------------------------------- 轮次状态机

VALID_TRANSITIONS: dict[ExpStatus, frozenset[ExpStatus]] = {
    ExpStatus.DESIGNED: frozenset({ExpStatus.EXECUTED}),
    ExpStatus.EXECUTED: frozenset({ExpStatus.QC_DONE}),
    ExpStatus.QC_DONE: frozenset({ExpStatus.ROUTED}),
    ExpStatus.ROUTED: frozenset({ExpStatus.CLOSED}),
    ExpStatus.CLOSED: frozenset(),
}


def advance_status(
    store: RunStore, exp: ExperimentObject, new_status: ExpStatus
) -> ExperimentObject:
    """状态迁移，**log-before-data**（WAL 纪律）：先 append_event 再 save_experiment。
    崩溃于两步间时日志领先视图（可重放修复），而非视图领先日志（审计丢失）。事件 payload
    仅引用入参与旧状态（old/new_status/exp_id/round_id），翻转写序不改变其内容。"""
    if new_status not in VALID_TRANSITIONS[exp.status]:
        raise LifecycleError(f"非法状态迁移: {exp.status.value} → {new_status.value}")
    old = exp.status
    exp.status = new_status
    store.append_event(
        "status_transition",
        {"exp_id": exp.exp_id, "round_id": exp.round_id, "from": old.value, "to": new_status.value},
    )
    store.save_experiment(exp)
    return exp


# ---------------------------------------------------------------- 信任裁决

@dataclass(frozen=True)
class TrustPolicy:
    """阈值来自域配置（domains/*.yaml，M3 接线）。"""

    suspect_high: float = 0.6   # ≥ 此值：SUSPECT + TO_FAILURE_MODEL
    quarantine_low: float = 0.3  # [low, high)：SUSPECT + QUARANTINE


def adjudicate(
    qc: QCReport, policy: TrustPolicy = TrustPolicy()
) -> tuple[TrustLevel, Routing, float]:
    """QC 证据 → (trust, routing, confidence)。纯函数，唯一的裁决入口。"""
    if any(c.level == "hard" and not c.passed for c in qc.checks):
        return TrustLevel.FAILED, Routing.TO_FAILURE_MODEL, 1.0
    suspicion = qc.suspicion
    if suspicion <= 0.0:
        suspicion = max((c.score for c in qc.checks if not c.passed), default=0.0)
    if suspicion >= policy.suspect_high:
        return TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL, suspicion
    if suspicion >= policy.quarantine_low:
        return TrustLevel.SUSPECT, Routing.QUARANTINE, suspicion
    if not qc.checks:
        # Q-4：空 QCReport（无检查证据）不得裁为 TRUSTED conf=1.0——"无证据即满分信任"
        # 是审计漏洞（公理 2：观测默认待裁决，须有证据方能信任）。响亮拒绝，无静默。
        raise LifecycleError(
            "空 QCReport（qc.checks 为空）不得裁决为 TRUSTED：无检查证据即无信任依据"
            "（公理 2；naive 对照臂的全信是显式设计、不走本裁决函数）"
        )
    return TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL, 1.0 - suspicion


def route_observation(
    store: RunStore,
    obs: ObservationObject,
    policy: TrustPolicy = TrustPolicy(),
) -> ObservationObject:
    """按 QC 证据裁决并落盘 + 写路由事件。obs.qc 缺失即拒绝（公理 2：观测默认待裁决）。

    **log-before-data**（WAL 纪律）：先 append_event("routing") 再 save_observation。
    崩溃于两步间时日志领先视图（可重放修复），而非视图领先日志（trust 落盘却无事件解释）。
    事件 payload 仅引用裁决局部量（trust/routing/conf/obs_id/round_id），翻转写序不改内容。"""
    if obs.qc is None:
        raise LifecycleError(f"obs {obs.obs_id} 无 QCReport，不能路由")
    if obs.trust != TrustLevel.PENDING:
        # Q-2：route_observation 只做 PENDING 观测的首判。已裁决观测重路由会静默回滚
        # （如 human FAILED 改判后再 route 回 TRUSTED、零留痕）——响亮拒绝，重判走 reclassify。
        raise LifecycleError(
            f"obs {obs.obs_id} trust={obs.trust.value} 非 PENDING，"
            "route_observation 仅首判 PENDING 观测；已裁决观测的改判须走 reclassify（留审计痕迹）"
        )
    trust, routing, conf = adjudicate(obs.qc, policy)
    obs.trust = trust
    obs.routing = routing
    obs.trust_confidence = conf
    store.append_event(
        "routing",
        {
            "obs_id": obs.obs_id,
            "round_id": obs.round_id,
            "trust": trust.value,
            "routing": routing.value,
            "confidence": conf,
        },
    )
    store.save_observation(obs)
    return obs


# ---------------------------------------------------------------- 信任转移合法性（STRESS_TEST_R1 P2「reclassify 绕状态机」修复）

#: adjudicate 只产出的 (trust, routing) 组合——改判与人工 override 也只允许落在这张表里。
#: 单一事实来源：overrides.py 消费端校验直接引用本表，勿在别处复制。
LEGAL_TRUST_ROUTING: dict[TrustLevel, frozenset[Routing]] = {
    TrustLevel.TRUSTED: frozenset({Routing.TO_RESPONSE_MODEL}),
    TrustLevel.SUSPECT: frozenset({Routing.TO_FAILURE_MODEL, Routing.QUARANTINE}),
    TrustLevel.FAILED: frozenset({Routing.TO_FAILURE_MODEL}),
}

_HUMAN_ONLY = frozenset({Actor.HUMAN})

#: 信任转移合法性表：(from_trust, to_trust) → 允许发起的 actor 集合。
#: 不在表内的转移一律非法——特别地，任何涉及 PENDING 的转移都不在表内：
#: PENDING 观测的首判只能走 route_observation/adjudicate（QC 证据裁决），不可用改判
#: "补裁决"；已裁决观测也不可撤回 PENDING（历史在事件日志里，状态只前进）。
#: 升级警戒方向（TRUSTED→SUSPECT/FAILED 等）planner/human 均可；
#: 翻案方向（提升信任，如 FAILED→TRUSTED）为高危，仅 human 可发起，
#: 且要求 reason 非空并额外落 reclassification_conflict 强留痕事件。
TRUST_TRANSITION_TABLE: dict[tuple[TrustLevel, TrustLevel], frozenset[Actor]] = {
    # 同级改判（只调整 routing 处置，如 SUSPECT: TO_FAILURE_MODEL ↔ QUARANTINE）
    (TrustLevel.TRUSTED, TrustLevel.TRUSTED): ADJUDICATOR_ACTORS,
    (TrustLevel.SUSPECT, TrustLevel.SUSPECT): ADJUDICATOR_ACTORS,
    (TrustLevel.FAILED, TrustLevel.FAILED): ADJUDICATOR_ACTORS,
    # 升级警戒（更保守方向）
    (TrustLevel.TRUSTED, TrustLevel.SUSPECT): ADJUDICATOR_ACTORS,
    (TrustLevel.TRUSTED, TrustLevel.FAILED): ADJUDICATOR_ACTORS,
    (TrustLevel.SUSPECT, TrustLevel.FAILED): ADJUDICATOR_ACTORS,
    # 翻案（提升信任，高危）：仅 human
    (TrustLevel.SUSPECT, TrustLevel.TRUSTED): _HUMAN_ONLY,
    (TrustLevel.FAILED, TrustLevel.SUSPECT): _HUMAN_ONLY,
    (TrustLevel.FAILED, TrustLevel.TRUSTED): _HUMAN_ONLY,
}

#: 高危翻案集合（= 表中仅 human 可发起的转移）：reason 必须非空 + 强留痕。
HIGH_RISK_TRANSITIONS: frozenset[tuple[TrustLevel, TrustLevel]] = frozenset(
    k for k, v in TRUST_TRANSITION_TABLE.items() if v == _HUMAN_ONLY
)


def check_trust_transition(
    from_trust: TrustLevel,
    to_trust: TrustLevel,
    to_routing: Routing,
    actor: Actor,
    reason: str,
) -> bool:
    """校验一次改判的合法性（转移表 + trust×routing 组合 + 高危 reason 要求）。

    非法一律响亮抛 LifecycleError；合法则返回是否高危翻案（调用方据此落强留痕）。
    这是 reclassify 与 overrides 消费端共用的唯一守卫入口。"""
    allowed = TRUST_TRANSITION_TABLE.get((from_trust, to_trust))
    if allowed is None:
        raise LifecycleError(
            f"非法信任转移: {from_trust.value} → {to_trust.value}"
            "（PENDING 只能经 route_observation 首判、不可 reclassify 亦不可作为改判目标；"
            "合法转移见 TRUST_TRANSITION_TABLE）"
        )
    if actor not in allowed:
        raise LifecycleError(
            f"actor={actor.value} 无权发起 {from_trust.value} → {to_trust.value} 改判"
            f"（允许: {sorted(a.value for a in allowed)}——翻案类高危转移仅 human）"
        )
    legal_routing = LEGAL_TRUST_ROUTING[to_trust]
    if to_routing not in legal_routing:
        raise LifecycleError(
            f"组合非法: trust={to_trust.value} 不允许 routing={to_routing.value}"
            f"（合法: {sorted(r.value for r in legal_routing)}，对照 adjudicate 产出表）"
        )
    high_risk = (from_trust, to_trust) in HIGH_RISK_TRANSITIONS
    if high_risk and not reason.strip():
        raise LifecycleError(
            f"高危翻案 {from_trust.value} → {to_trust.value} 要求 reason 非空（审计不变量）"
        )
    return high_risk


# ---------------------------------------------------------------- 改判 / 翻案

def reclassify(
    store: RunStore,
    obs_id: str,
    new_trust: TrustLevel,
    new_routing: Routing,
    actor: Actor,
    reason: str,
    refs: list[str] | None = None,
) -> ObservationObject:
    """改判：观测 JSON 是"当前状态"的物化视图，允许更新；
    历史裁决保留在事件日志里（追加 reclassification 事件引用旧状态，永不覆盖），
    并同步落一条 OVERRIDE DecisionRecord 供审计。改判属裁决语义，仅 planner/human 可发起。

    转移合法性（STRESS_TEST_R1 P2）：改判不再是"直改落盘"——必须过
    check_trust_transition 守卫（TRUST_TRANSITION_TABLE + LEGAL_TRUST_ROUTING）：
    PENDING 观测（含 qc=None）不可改判（首判走 route_observation）；翻案方向仅 human，
    且 reason 非空并额外落 reclassification_conflict 强留痕事件。

    **log-before-data**（WAL 纪律）：payload 在改判**前**从旧状态构造（from_trust/from_routing
    取旧值），随后先 append_event + append_decision（两条日志），最后才 save_observation。
    崩溃于日志与视图之间时日志领先视图（可重放修复），而非改判落盘却无事件/审计（审计丢失）。
    翻转写序不改 payload 内容——它只引用入参与改判前的旧状态。"""
    if actor not in ADJUDICATOR_ACTORS:
        raise LifecycleError(f"actor={actor.value} 无裁决权，不能改判（公理 7）")
    obs = store.load_observation(obs_id)
    if obs.qc is None:
        raise LifecycleError(
            f"obs {obs_id} 无 QCReport——未经裁决的观测不可改判（首判走 route_observation），"
            "改判也不得把 qc=None 观测直送响应模型"
        )
    high_risk = check_trust_transition(obs.trust, new_trust, new_routing, actor, reason)
    payload = {
        "obs_id": obs_id,
        "round_id": obs.round_id,
        "from_trust": obs.trust.value,
        "to_trust": new_trust.value,
        "from_routing": obs.routing.value if obs.routing else None,
        "to_routing": new_routing.value,
        "from_confidence": obs.trust_confidence,  # Q-5：记旧置信供审计（人工裁决前的机器置信）
        "actor": actor.value,
        "reason": reason,
    }
    obs.trust = new_trust
    obs.routing = new_routing
    # Q-5：人工改判=确定性裁决，trust_confidence 置 1.0；否则旧机器置信（如 FAILED conf=1.0）
    # 会残留成新级别的语义（os-soft 把 trust_confidence 当 suspicion 读，1.0 语义翻转）。
    obs.trust_confidence = 1.0
    store.append_event("reclassification", payload)
    if high_risk:
        # resolution_conflict 风格的强留痕：高危翻案（如 FAILED→TRUSTED）单独可检索。
        store.append_event(
            "reclassification_conflict",
            {
                "obs_id": obs_id,
                "from_trust": payload["from_trust"],
                "to_trust": new_trust.value,
                "actor": actor.value,
                "reason": reason,
            },
        )
    store.append_decision(
        DecisionRecord(
            round_id=obs.round_id,
            actor=actor,
            kind=DecisionKind.OVERRIDE,
            refs=[obs_id] + (refs or []),
            content=payload,
        )
    )
    store.save_observation(obs)
    return obs


# ---------------------------------------------------------------- 提案配对语义（§4.5 审计不变量）

def submit_proposal(store: RunStore, record: DecisionRecord) -> DecisionRecord:
    if record.kind not in PROPOSAL_KINDS:
        raise LifecycleError(f"{record.kind.value} 不是提案类决策")
    return store.append_decision(record)


def validate_proposal(
    store: RunStore,
    proposal: DecisionRecord,
    accepted: bool,
    actor: Actor,
    reason: str = "",
) -> DecisionRecord:
    """规划器/人类对提案的裁定：追加 acceptance/rejection 记录（refs 指向提案），
    不修改原提案记录（append-only）。

    守卫：① 仅 planner/human 有裁决权；② 已裁定的提案不得静默翻盘——
    再次裁定只允许 human（override 语义），并落 conflict 事件留痕；
    ③ 幽灵裁定拒斥（Q-8）：被裁提案必须已在事件日志中提交入库——否则裁定的 refs 指向
    不存在的 decision_id，却仍被 _resolutions 收录（凭空产出一条"已接受/已拒绝"的裁定）。"""
    if actor not in ADJUDICATOR_ACTORS:
        raise LifecycleError(f"actor={actor.value} 无裁决权，不能裁定提案（公理 7）")
    if proposal.kind not in PROPOSAL_KINDS:
        raise LifecycleError(f"{proposal.kind.value} 不是提案类决策，无需裁定")
    submitted = {
        d.decision_id for d in store.list_decisions() if d.kind in PROPOSAL_KINDS
    }
    if proposal.decision_id not in submitted:
        raise LifecycleError(
            f"提案 {proposal.decision_id} 未提交入库，不能裁定（幽灵裁定，Q-8）——"
            "裁定 refs 必须指向日志中已存在的提案；先 submit_proposal 再裁定"
        )
    prior = _resolutions(store).get(proposal.decision_id)
    if prior is not None:
        if actor != Actor.HUMAN:
            raise LifecycleError(
                f"提案 {proposal.decision_id} 已被裁定为 {prior}；仅 human 可 override 翻转"
            )
        store.append_event(
            "resolution_conflict",
            {"proposal_id": proposal.decision_id, "prior_accepted": prior, "new_accepted": accepted},
        )
    return store.append_decision(
        DecisionRecord(
            round_id=proposal.round_id,
            actor=actor,
            kind=DecisionKind.ACCEPTANCE if accepted else DecisionKind.REJECTION,
            refs=[proposal.decision_id],
            content={"reason": reason},
            accepted=accepted,
            validator=actor.value,
        )
    )


def _resolutions(store: RunStore) -> dict[str, bool]:
    """proposal decision_id → 是否被接受（以最后一条**有效**裁定为准）。

    只采信 actor∈ADJUDICATOR_ACTORS 的 acceptance/rejection；actor=agent 的
    裁定记录即使出现在日志里也一律忽略——这是"agent 无裁决权"在日志层的机器强制。

    **一裁一案（Q-8）**：合法裁定（validate_proposal 产出）refs 恒为单元素。若某条
    adjudicator 裁定 refs 数 ≠ 1（多 refs 一票裁俩 / 空 refs 裁空），是被伪造/篡改的畸形
    记录——响亮抛 StoreError，绝不让一票静默裁定两个提案。"""
    out: dict[str, bool] = {}
    for d in store.list_decisions():
        if d.kind in (DecisionKind.ACCEPTANCE, DecisionKind.REJECTION) and d.actor in ADJUDICATOR_ACTORS:
            if len(d.refs) != 1:
                raise StoreError(
                    f"裁定 {d.decision_id}（{d.kind.value}）refs 数={len(d.refs)}≠1——"
                    "一条裁定只能配对一个提案（多 refs 一票裁俩，疑似伪造/篡改，Q-8）"
                )
            out[d.refs[0]] = d.kind == DecisionKind.ACCEPTANCE
    return out


def unresolved_proposals(store: RunStore) -> list[DecisionRecord]:
    """尚无 acceptance/rejection 配对的提案——审计不变量的机器检查入口。"""
    resolved = _resolutions(store)
    return [
        d
        for d in store.list_decisions()
        if d.kind in PROPOSAL_KINDS and d.decision_id not in resolved
    ]


def accepted_proposals(store: RunStore) -> list[DecisionRecord]:
    """规划器唯一可消费的提案集合：显式被 ACCEPTANCE 配对者。
    未裁定或被拒绝的提案对后续设计不可见（M7 规划器只从这里取）。"""
    resolved = _resolutions(store)
    return [
        d
        for d in store.list_decisions()
        if d.kind in PROPOSAL_KINDS and resolved.get(d.decision_id) is True
    ]
