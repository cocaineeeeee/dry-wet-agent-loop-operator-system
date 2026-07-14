"""轮次编排器（docs/ARCHITECTURE.md §11）。

M5 版：裁决与聚合抽为**双策略注入点**（DEEP_REVIEW §3.2 红线——loop 主体零 mode
分支）：naive = NaivePolicy + PassthroughAggregation（对照组，全信）；
os = QCPolicy(三级检查→adjudicate) + ReplicateVarianceAggregation（副本方差→
逐点 alpha）。两臂只在策略对象上不同，其余代码完全共享（对比公平性主张的根基）。

每轮：设计 → 执行 → truth 不透明落盘 → ingest → verdict.judge（裁决+路由+事件）
→ aggregation.prepare → 响应模型重训 → 模型快照 → 检查点。断点续跑：
checkpoint.json 记录已完成轮数，模型与 QC 历史从观测存储重建。

红线：本模块把 truth_records 原样交给 store.save_truth（不透明透传），
**不解析任何真值字段**；响应模型与采样器同样禁触 truth（公理 6，测试断言）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from pathlib import Path
from typing import Any

import numpy as np

_log = logging.getLogger("expos.loop")

from expos.adapters.ingest import raw_to_observations
from expos.agent.policy import (
    LoopAgentPolicy,
    NullAgentPolicy,
    TemplateAgentPolicy,
)
from expos.design.budget import BudgetManager
from expos.design.layout import LayoutPlanner
from expos.domain import DomainConfig, build_adapter, config_fingerprint, load_domain
from expos.kernel.lifecycle import TrustPolicy, advance_status
from expos.kernel.overrides import consume_pending_overrides
from expos.planner.policy import (
    MIN_TRAIN_FOR_BO,  # noqa: F401  # 语义常量随规划器迁移，此处再导出供旧引用
    BaselinePlanner,
    PlanContext,
    PlannerPolicy,
    TrustAwarePlanner,
)
from expos.planner.promotion import NullPromotion, PromotionPolicy
from expos.qc.checks import QCHistory, run_qc
from expos.qc.policy import (
    AggregationPolicy,
    MedianAggregation,
    NaivePolicy,
    PassthroughAggregation,
    QCPolicy,
    ReplicateVarianceAggregation,
    SoftTrustAggregation,
    VerdictPolicy,
)
from expos.kernel.objects import (
    Budget,
    Candidate,
    Control,
    DesignProvenance,
    ExecutionReq,
    ExperimentObject,
    ExpStatus,
    Routing,
    TrustLevel,
)
from expos.kernel.store import RunStore
from expos.models.response_gp import ResponseModel
from expos.models.robust_gp import RobustResponseModel


from expos.errors import ExposError


class LoopError(ExposError):
    pass


def derive_seed(seed: int, *parts: Any) -> int:
    """稳定派生子种子：同 (seed, parts) 恒同值。"""
    text = f"{seed}|" + "|".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")


def _kappa_for_round(round_id: int, rounds_total: int) -> float:
    """UCB κ 短预算调度：3 线性降到 1（REFERENCE_MAP §11.1）。
    rounds_total 必须取域配置的 budget.rounds_total（campaign 视界，跨 resume 恒定）——
    绑定 CLI 的 rounds 参数会使"分段 resume"与"一次跑完"的采集调度不等价
    （M4 压力测试 finding A）。"""
    if rounds_total <= 1:
        return 2.0
    frac = min(1.0, round_id / (rounds_total - 1))
    return 3.0 - 2.0 * frac


def _n_candidates(cfg: DomainConfig) -> int:
    capacity = cfg.plate.rows * cfg.plate.cols
    n = (capacity - cfg.sentinel.n) // cfg.replicates
    if n < 1:
        raise LoopError("板容量不足以放下哨兵之外的任何候选")
    return n


def _canonical_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """仲裁器派生候选的相对顺序规范化（R1-5(c) 根因之一）。

    布局分配是"列表顺序 → 孔位"的种子确定映射；sobol/bo 候选的列表顺序本就
    种子确定，但 **source 以 'arbiter:' 开头的动作派生候选**（如 REMEASURE）
    经仲裁器按 item_uid（含随机 uuid 的 cand_id）tie-break 排序——同一组参数
    每次 run 落进不同孔，边缘/伪影暴露随之变化 → 同 seed 轨迹 run-to-run 分叉、
    resume 不等价（实测 seed=11 crystal os 第 1 轮起指纹逐轮发散、best 漂移）。

    修法：仅对 arbiter 派生候选**原位**按内容（params 规范 JSON）排序，其余
    候选顺序原样保留——不动 sobol/bo 的既有确定性轨迹（全列表排序实测会把
    轨迹推进布局贪心不可行的雷区）。同 source 同 params 的候选可互换（下游
    只消费 params），原位排序不改变多重集语义。"""
    out = list(candidates)
    idxs = [i for i, c in enumerate(out) if c.source.startswith("arbiter:")]
    if len(idxs) > 1:
        group = sorted(
            (out[i] for i in idxs),
            key=lambda c: json.dumps(c.params, sort_keys=True, default=str),
        )
        for i, c in zip(idxs, group):
            out[i] = c
    return out


def build_experiment(
    cfg: DomainConfig,
    round_id: int,
    candidates: list[Candidate],
    budget: Budget,
    seed: int,
    provenance: DesignProvenance,
    risk_map: dict[str, float] | None = None,
) -> ExperimentObject:
    candidates = _canonical_candidates(candidates)
    controls = [
        Control(kind="sentinel", params=cfg.sentinel.params,
                expected_band=cfg.sentinel.expected_band)
        for _ in range(cfg.sentinel.n)
    ]
    layout = LayoutPlanner(
        cfg.plate.rows, cfg.plate.cols, seed=derive_seed(seed, "layout", round_id)
    ).assign(candidates, controls, n_replicates=cfg.replicates, risk_map=risk_map)
    # 消费侧取证（O3DV C2 修复）：从本函数**实收**并交给 assign 的 risk_map 计算摘要
    # 挂 provenance——"转手 None 但事件照发产出侧摘要"的表演性构造在 grade 上显形为 absent。
    provenance = provenance.model_copy(
        update={"risk_map_summary": _risk_map_summary(risk_map)}
    )
    return ExperimentObject(
        round_id=round_id,
        domain=cfg.name,
        objective=cfg.objective,
        design_space=cfg.design_space,
        active_vars=[v.name for v in cfg.design_space.variables],
        restrictions=cfg.restrictions,
        candidates=candidates,
        controls=controls,
        layout=layout,
        budget=budget,
        execution_req=ExecutionReq(adapter=cfg.adapter, n_solution_batches=2),
        provenance=provenance,
    )


def _make_qc_runner(cfg: DomainConfig, seed: int):
    """把 checks.run_qc 适配成 QCPolicy 的 qc_runner 契约：
    先前各轮观测 → 跨轮哨兵 QCHistory（控制带冷启动依据），逐轮种子确定性。"""

    def qc_runner(exp, obs_list, history_obs):
        hist = QCHistory()
        by_round: dict[int, list[float]] = {}
        for o in history_obs:
            if o.is_control and o.result.value is not None:
                by_round.setdefault(o.round_id, []).append(o.result.value)
        for r in sorted(by_round):
            hist.append_round(by_round[r])
        return run_qc(  # (reports, PlateContext)——plate 供 M6 归因消费
            exp, obs_list, history=hist,
            seed=derive_seed(seed, "qc", exp.round_id),
            metric_range=cfg.metric_range,
        )

    return qc_runner


#: model_factory 契约：callable(cfg, seed) -> 响应模型实例（loop 的第五注入点，M9）。
#: 既有四臂用 ResponseModel（行为零变化）；rcgp 臂换 RobustResponseModel（模型层稳健）。
def _response_model_factory(cfg: DomainConfig, seed: int) -> ResponseModel:
    return ResponseModel(cfg.design_space, direction=cfg.objective.direction, seed=seed)


def _robust_model_factory(cfg: DomainConfig, seed: int) -> RobustResponseModel:
    return RobustResponseModel(cfg.design_space, direction=cfg.objective.direction, seed=seed)


def _rcgp_capacity_model_factory(cfg: DomainConfig, seed: int) -> ResponseModel:
    """os-lite 容量对齐工厂（M13 消融，R2 §1.2 / K-P1）：ResponseModel 但**关掉 ARD**
    （各向同性单标量 length_scale），把响应模型降到与 rcgp（RobustResponseModel 各向
    同性 5×3×3=45 格加权 LOO）**同容量档**。

    "容量税"实指——R2 §1.2 / K-P1 实锤：rcgp 用各向同性粗网格（1 个 length_scale），
    os/naive 用 ARD（逐维 length_scale）+ L-BFGS ML-II×10 restarts；同 40 点各向异性
    干净数据 naive RMSE 0.0122 vs rcgp 0.2261（18×）。本地复算证实主容量轴是 **ARD vs
    各向同性**（同优化预算下 ARD 0.023 vs ISO 0.393，~17×），而非 restart 次数——故容量
    对齐只需关 ARD 这一轴即忠实（连续 ML-II 优化器保留，不额外阉割）。

    方向决策（等评估次数口径——BO 界主口径是等函数评估非 wall-clock，见 botorch/Ax 对标）：
    容量对齐**给 os 降容量**（本臂）而非"给 rcgp 提容量"。理由：(1) 等评估口径下 headline
    os-vs-rcgp 对照应各自用最优代理（不阉割主结果）；os-lite 是**刻意**容量对齐的消融，
    用来把"路由层贡献"从"代理容量税"里剥出——os-lite vs rcgp 同各向同性容量，差异即
    路由层；os vs os-lite 差异即 ARD 容量税。(2) 给 rcgp 提 ARD 需把加权 LOO 网格改逐维
    坐标轮换（O(45+3d)，robust_gp.py 大改），是更重的独立跟进；本臂用授权范围内的
    ResponseModel(ard=False) 工厂即忠实覆盖同容量对照，是等评估口径下的最小充分实现。"""
    return ResponseModel(cfg.design_space, direction=cfg.objective.direction,
                         seed=seed, ard=False)


def _os_family_policies(
    cfg: DomainConfig, seed: int, *,
    soft: bool = False,
    model_factory: Any = _response_model_factory,
    enable_risk_map: bool = True,
    enable_arbiter: bool = True,
    enable_attribution: bool = True,
) -> tuple[VerdictPolicy, AggregationPolicy, PlannerPolicy, LoopAgentPolicy, Any,
           PromotionPolicy]:
    """os 家族五策略装配（os / os-soft / 四消融臂共用；策略对象参数化而非新类爆炸）。

    默认全开 = os 全栈。各消融臂只翻一个布尔/换一个工厂（DEEP_REVIEW §3.2 红线：
    此函数无 mode 字符串判定，判定全留在 _policies_for_mode 的分支里）：
    - soft=True → os-soft（QUARANTINE 软信任降权复归）
    - model_factory=_rcgp_capacity_model_factory → os-lite（容量对齐）
    - enable_risk_map=False → os-minus-riskmap（布局无风险避让）
    - enable_arbiter=False → os-minus-arbiter（动作仲裁空转）
    - enable_attribution=False → os-minus-attribution（QC 检出照常，不产归因/next_action）
    """
    from expos.qc.attribution import attribute, propose_action  # M6 注入点

    trust = TrustPolicy(
        suspect_high=cfg.trust.suspect_high,
        quarantine_low=cfg.trust.quarantine_low,
    )
    if enable_attribution:
        def attributor(obs, report, plate, exp):
            return attribute(obs, report, plate, exp,
                             seed=derive_seed(seed, "attr", exp.round_id))
        verdict = QCPolicy(_make_qc_runner(cfg, seed), trust,
                           attributor=attributor, action_proposer=propose_action)
    else:
        # os-minus-attribution：三级 QC 检出/路由照常，但 attributor=None → 不产
        # failure_attr/next_action（QCPolicy 现成注入点，见 qc/policy.py:165 守卫）。
        verdict = QCPolicy(_make_qc_runner(cfg, seed), trust,
                           attributor=None, action_proposer=None)
    aggregation = (
        SoftTrustAggregation(suspect_high=cfg.trust.suspect_high,
                             quarantine_low=cfg.trust.quarantine_low)
        if soft else ReplicateVarianceAggregation()
    )
    planner = TrustAwarePlanner(enable_risk_map=enable_risk_map,
                                enable_arbiter=enable_arbiter)
    # 第六注入点（M16 W7 Dry->Wet 晋升门）：os 家族现行注入 NullPromotion（decide()->None，
    # 零事件、零行为——既有五臂逐位不变）。EvidenceGatedPromotion 由 W9 mcl 合龙时接入
    # （见下方 run_loop 第六元接线点注释），本批不硬接。
    return (verdict, aggregation, planner, TemplateAgentPolicy(), model_factory,
            NullPromotion())


def _policies_for_mode(
    mode: str, cfg: DomainConfig, seed: int
) -> tuple[VerdictPolicy, AggregationPolicy, PlannerPolicy, LoopAgentPolicy, Any,
           PromotionPolicy]:
    """mode → (裁决, 聚合, 规划, agent, model_factory, promotion) 六策略。这是 loop 里
    唯一的 mode 判定点——此后主体零分支（DEEP_REVIEW §3.2）。

    第六元 promotion（M16 W7）现行全臂 NullPromotion：decide()->None、零事件、零行为，
    故既有五臂逐位不变（前五元未动，第六元惰性）。EvidenceGatedPromotion 的注入是 W9
    mcl（--loop mcl / M16）合龙时的事，不在本批（本批只落策略对象 + 发射辅助）。"""
    if mode == "naive":
        return (NaivePolicy(), PassthroughAggregation(), BaselinePlanner(),
                NullAgentPolicy(), _response_model_factory, NullPromotion())
    if mode == "robust":
        # robust-blind 臂（M9 三臂之二）：信任盲——不做 QC/路由，但副本中位数聚合
        # + 保守选（n=2 取劣者）吸收孤立伪影；结构性偏差救不了（这正是论点）
        return (NaivePolicy(), MedianAggregation(), BaselinePlanner(),
                NullAgentPolicy(), _response_model_factory, NullPromotion())
    if mode == "rcgp":
        # rcgp 臂（模型层稳健 vs 路由层稳健的对照，M9 扩展）：信任盲 + 直通聚合，
        # 稳健性长在模型层（RCGP Plateau-IMQ 后验软剪裁离群）——与 robust 臂的
        # 聚合层稳健、os 臂的路由层稳健三方对照。
        return (NaivePolicy(), PassthroughAggregation(), BaselinePlanner(),
                NullAgentPolicy(), _robust_model_factory, NullPromotion())
    # os 家族（全栈 + 消融）：每臂一分支，只翻一个开关/换一个工厂（M13 消融矩阵，
    # 装配细节共用 _os_family_policies——策略对象参数化而非新类爆炸）。os-soft 除聚合
    # 外与 os 全同（对照公平性）：软信任把 QUARANTINE 观测以膨胀 alpha 内存态降权复归。
    if mode in ("os", "os-soft"):
        return _os_family_policies(cfg, seed, soft=(mode == "os-soft"))
    if mode == "os-lite":
        # 容量对齐公平对照（R2 §1.2 / K-P1）：os 全栈 × 与 rcgp 同容量档（各向同性）
        # 模型工厂——把"路由层贡献"从"代理容量税"里剥出。
        return _os_family_policies(cfg, seed,
                                   model_factory=_rcgp_capacity_model_factory)
    if mode == "os-minus-riskmap":
        # P1 消融：os 全栈但 plan_round 不产 risk_map（布局无风险避让）。
        return _os_family_policies(cfg, seed, enable_risk_map=False)
    if mode == "os-minus-arbiter":
        # P1 消融：os 全栈但动作仲裁空转（_pending_actions 恒空/零动作消费）。
        return _os_family_policies(cfg, seed, enable_arbiter=False)
    if mode == "os-minus-attribution":
        # P1 消融：QC 检出照常但不做归因/不产 next_action（attributor=None）。
        return _os_family_policies(cfg, seed, enable_attribution=False)
    raise LoopError(
        f"未知 mode: {mode!r}（可用: naive / robust / rcgp / os / os-soft / "
        "os-lite / os-minus-riskmap / os-minus-arbiter / os-minus-attribution；"
        "compare 见 eval 侧编排）"
    )


def _route_naive(store: RunStore, obs_list: list) -> None:
    """M4 原始实现——保留为 NaivePolicy 逐字段等价性回归的参照
    （tests/test_qc_policy.py），run_loop 不再直接调用。"""
    for obs in obs_list:
        obs.trust = TrustLevel.TRUSTED
        obs.routing = Routing.TO_RESPONSE_MODEL
        obs.trust_confidence = 1.0
        store.save_observation(obs)
    store.append_event(
        "routing_bulk", {"mode": "naive", "n": len(obs_list),
                         "round_id": obs_list[0].round_id if obs_list else None},
    )


def _quarantined(store: RunStore) -> list:
    """本轮及历史落盘的 routing==QUARANTINE 可疑观测（软信任臂降权复归的入料）。
    对所有 mode 统一计算、统一传入 aggregation.prepare（零 mode 分支）——非软信任
    聚合策略按签名默认忽略之。"""
    return [
        o for o in store.list_observations(trust=TrustLevel.SUSPECT)
        if o.routing == Routing.QUARANTINE
    ]


def _risk_map_summary(risk_map: dict[str, float] | None) -> dict[str, Any]:
    """只读观测面（机制活性先行版，ARCH_V2 §2）：概括 plan_round 交给 LayoutPlanner
    消费的风险图——``build_experiment`` 把 ``risk_map`` 原样转手 ``LayoutPlanner.assign``
    （无变换、无 mode 分支），故本概括即 LayoutPlanner 实际吃到的图。``n_distinct``
    记不同风险层数：None/常数图（如生产接线断开→None、或空转恒常数）在此坍成 0/1，
    使环路级活性断言（tests/test_mechanism_activity.py::test_E_*）可击杀"风险图空转"。
    不改任何决策——纯派生量。"""
    if not risk_map:
        return {"is_none": risk_map is None, "n_wells": 0, "n_distinct": 0,
                "min": None, "max": None, "mean": None}
    vals = [float(v) for v in risk_map.values()]
    distinct = sorted({round(v, 12) for v in vals})
    return {"is_none": False, "n_wells": len(vals), "n_distinct": len(distinct),
            "min": min(vals), "max": max(vals),
            "mean": sum(vals) / len(vals)}


# 机制活性三态判档（O3-D 交接建议 1 + 收紧 1；k8s probe.Result 的 {Success,Failure,Warning}
# 同构）。发射/裁决解耦（收紧 1，k8s prober/results_manager 同构：worker 只 Set() 进缓存、
# 从不直接动容器）——本处的 grade **是派生事实、非裁决**：纯函数据本轮已产的事实字段判
# {active, warning, absent} 三态，红/黄的 CI 判档留给消费端（tests/test_mechanism_activity.py、
# eval/activity_budget.py）。故此处零 mode 分支、不读 truth、不改任何决策。
_SOFT_TRUST_ACTIVE_RATIO = 3.0  # soft 副本 alpha 中位显著超 TRUSTED 中位（正常≈13×、F-3≡1×）


def _grade_risk_map(summary: dict[str, Any]) -> str:
    """风险图活性三态（k8s probe：absent=liveness失败/warning=readiness降级/active=就绪）：
    is_none→absent（生产接线断开，变异 E 同形）；非 None 但 n_distinct≤1（空图/恒常数）
    →warning（发射了但效应恒等、空转嫌疑，sweep 级收口）；n_distinct≥2→active。"""
    if summary["is_none"]:
        return "absent"
    return "active" if summary["n_distinct"] >= 2 else "warning"


def _grade_aggregation(
    train_obs: list[Any], alpha: "np.ndarray | None", quar_ids: set[str]
) -> str:
    """聚合降权活性三态（纯函数，参数即本轮 prepare 的产物 + 落盘 QUARANTINE id 集）：
    无训练样本→absent；alpha=None（passthrough/median，本臂无逐点降权）或无软并入降权条目
    →warning（干净轮恒等合法）；有降权条目但其 alpha 中位≈TRUSTED 中位（比值≤阈值，F-3
    降权失效/假活性）→warning；软副本 alpha 中位显著>TRUSTED 中位→active。软副本在 fit
    守门层被合成为 TRUSTED（policy.py），故按 obs_id∈QUARANTINE 集辨识，与 F-3 消费断言同源。"""
    if not train_obs:
        return "absent"
    if alpha is None:
        return "warning"
    soft, trusted = [], []
    for o, a in zip(train_obs, alpha):
        (soft if o.obs_id in quar_ids else trusted).append(float(a))
    if not soft or not trusted:
        return "warning"
    soft_med, trust_med = statistics.median(soft), statistics.median(trusted)
    if trust_med > 0.0 and soft_med > _SOFT_TRUST_ACTIVE_RATIO * trust_med:
        return "active"
    return "warning"


def _best_so_far(store: RunStore, direction: str) -> dict[str, Any] | None:
    best = None
    sign = 1.0 if direction == "maximize" else -1.0
    for obs in store.list_observations(trust=TrustLevel.TRUSTED):
        if obs.result.value is None or obs.is_control:
            continue
        if best is None or sign * obs.result.value > sign * best["value"]:
            best = {"value": obs.result.value, "cand_id": obs.cand_id,
                    "obs_id": obs.obs_id, "round_id": obs.round_id}
    return best


def run_loop(
    domain_path: str | Path,
    mode: str,
    rounds: int,
    seed: int,
    out_dir: str | Path,
    resume: bool = False,
    allow_config_drift: bool = False,
) -> dict[str, Any]:
    if rounds < 1:
        raise LoopError("rounds 必须 ≥ 1")

    cfg = load_domain(domain_path)
    # 第六注入点 promotion（M16 W7 Dry->Wet 晋升门）——**wired by W9**：本批只解包不接线。
    # G5 mcl 合龙时在下方每轮循环里接：dry_view -> promotion.decide(dry_view, risk_map,
    # knowledge_fingerprint, budget) -> 非 None 时 emit_promotion_decision(store, round_id,
    # decision)（NullPromotion.decide()->None 静默、零事件、零 mode 分支）；resume 位刻意
    # 不重发（I4，同 learning_weight_assigned 的续跑静默）。现行全臂 NullPromotion → 惰性。
    verdict, aggregation, planner, agent, model_factory, promotion = _policies_for_mode(
        mode, cfg, seed)
    out = Path(out_dir)
    if not resume and (out / "checkpoint.json").exists():
        raise LoopError(f"{out} 已有运行检查点——续跑请加 --resume，全新运行换目录")
    # 单写者写锁（M-4b）：两个 --resume 同目录并发时后到者取锁失败、响亮拒绝。
    # 观测内存缓存（M-2 热径）：loop 是唯一写者（writer.lock 保证），故安全开缓存——
    # 每轮 9 次 list_observations 全量磁盘重扫降为 O(1) 命中，save_observation/reclassify/
    # reconcile_redo_rounds 同步维护/失效（store 内保证一致性）。非 loop 写者默认不开。
    store = RunStore(out, lock=True, cache_observations=True)

    _terminal_emitted = False
    try:
        if resume:
            ckpt = store.read_checkpoint()
            if ckpt is None:
                raise LoopError(f"--resume 但 {out} 无 checkpoint.json")
            stored_cfg = store.read_config() or {}
            for key, want in (("domain", cfg.name), ("mode", mode), ("seed", seed)):
                if stored_cfg.get(key) != want:
                    raise LoopError(
                        f"resume 配置不匹配: {key} 存储={stored_cfg.get(key)!r} 当前={want!r}"
                    )
            # 域配置全文指纹比对（R1 P2「域配置漂移放行」修复）：只查三键会放行
            # 阈值/注入器/预算漂移。旧 run 无指纹时按存储的 domain_config 现算兜底。
            current_fp = config_fingerprint(cfg)
            stored_fp = stored_cfg.get("config_fingerprint") or (
                config_fingerprint(stored_cfg["domain_config"])
                if stored_cfg.get("domain_config") else None
            )
            if stored_fp is not None and stored_fp != current_fp:
                if not allow_config_drift:
                    raise LoopError(
                        f"resume 域配置漂移: 存储指纹 {stored_fp[:12]}… ≠ 当前 {current_fp[:12]}…"
                        "——domain_config 全文已变（阈值/注入器/预算之一），续跑语义不再等价。"
                        "确认要带漂移续跑请加 --allow-config-drift（会落 config_drift 事件留痕）"
                    )
                store.append_event("config_drift", {
                    "stored_fingerprint": stored_fp, "current_fingerprint": current_fp,
                    "acknowledged": True,
                })
            start_round = int(ckpt["completed_rounds"])
            budget = Budget(**ckpt["budget"])
            # 崩溃窗口对账（R1-5(b)）：清掉 round_id ≥ completed_rounds 的孤儿观测/实验，
            # 必须先于下方"用已存观测重建模型"——否则重做轮双份观测直接喂进响应模型。
            store.reconcile_redo_rounds(start_round)
            store.append_event("resume", {"from_round": start_round})
        else:
            start_round = 0
            budget = Budget(**cfg.budget.model_dump())
            store.save_config(
                {"domain": cfg.name, "mode": mode, "seed": seed,
                 "domain_config": cfg.model_dump(mode="json"),
                 "config_fingerprint": config_fingerprint(cfg)}
            )
            # run 级开事件（event-model run_start 纪律：provably-first、metadata-first）。
            # resume 不重发——一个 run 一个 start；run_stop 缺席即"未正常收口"（对账信号）。
            store.append_event("run_start", {
                "domain": cfg.name, "mode": mode, "seed": seed,
                "rounds_target": rounds,
            })
        if start_round >= rounds:
            return _summarize(store, cfg, rounds)

        adapter = build_adapter(cfg)
        bm = BudgetManager(budget)
        model = model_factory(cfg, seed)  # 第五注入点：既有臂=ResponseModel / rcgp=RobustResponseModel
        n_cands = _n_candidates(cfg)

        # 续跑时用已存的可信观测重建模型（经同一聚合策略——resume 等价性）
        trusted = store.list_observations(trust=TrustLevel.TRUSTED)
        if trusted:
            train_obs, alpha = aggregation.prepare(
                trusted, store.list_experiments(), quarantine=_quarantined(store)
            )
            model.fit(train_obs, store.list_experiments(), per_point_alpha=alpha)
            # NOTE (VNext batch-1): the resume rebuild deliberately does NOT emit
            # learning_weight_assigned -- it reconstructs in-memory state from
            # persisted observations; emitting here would insert events a
            # straight-through run never produces at this position and break I4
            # resume equivalence. Emission lives at the per-round fit site only.


        planner.restore_state((store.read_checkpoint() or {}).get("planner", {}))

        # 物化视图故障隔离防静默（OS3 §一）：上面各 list_observations 已全量扫过 observations/；
        # 若隔离了坏 obs 文件（非 UTF-8/坏 JSON），首轮前响亮告警 + 落 view_quarantine 事件留痕
        # ——单坏文件不再 DoS 全 run，但绝不静默吞掉（缺失文件会使响应模型少喂样本，须可审计）。
        if store.quarantined_files:
            _log.warning(
                "view_quarantine: run %s 隔离了 %d 个无法解析的观测文件（跳过继续，未 DoS）：%s",
                store.root, len(store.quarantined_files),
                sorted(store.quarantined_files.keys()),
            )
            store.append_event("view_quarantine", {
                "n_quarantined": len(store.quarantined_files),
                "files": sorted(store.quarantined_files.keys()),
                "errors": store.quarantined_files,
            })

        for round_id in range(start_round, rounds):
            bm.start_round()
            # ---- 人类改判消费（§13.13 pending 通道，R1 P2「死投递」修复）：
            # 消费发生在规划前——本轮 plan_round 经 store 读到的观测 trust/routing
            # 已含人类改判结果，规划立即看到改判。
            consume_pending_overrides(store, store.root)
            # ---- 设计（第三策略注入点：naive=Baseline 原样 / os=阶段 FSM+仲裁+KB+风险图）
            plan = planner.plan_round(PlanContext(
                cfg=cfg, store=store, model=model, round_id=round_id,
                seed=seed, n_cands=n_cands,
                kappa=_kappa_for_round(round_id, cfg.budget.rounds_total),
            ))
            cands, prov = plan.candidates, plan.provenance
            exp = build_experiment(cfg, round_id, cands, bm.budget, seed, prov,
                                   risk_map=plan.risk_map)
            bm.charge_layout(exp.layout)
            exp.budget = bm.budget  # 记账后回填，exp 快照与 checkpoint 同源（审查 finding）
            store.save_experiment(exp)
            store.append_event("round_designed", {
                "round_id": round_id, "exp_id": exp.exp_id,
                "generator": prov.generator, "n_candidates": len(cands),
                "wells": len(exp.layout.wells),
            })
            # 只读活性观测面（机制活性先行版，ARCH_V2 §2）：登记本轮风险图概括
            # （is_none/n_distinct）——断线/恒常数空转在此显形，供环路级断言击杀。
            # 取证源=**消费侧**（O3DV C2 修复）：读 exp.provenance.risk_map_summary
            # （build_experiment 从实收参数计算），不读 plan.risk_map 产出侧——转手断线
            # （给 layout 传 None、事件照发产出侧摘要）时两者不同，消费侧才是
            # "LayoutPlanner 真吃到什么"。所有 mode 统一发射，零 mode 分支。
            _rm_summary = exp.provenance.risk_map_summary or _risk_map_summary(None)
            store.append_event("risk_map_applied", {
                "round_id": round_id, "exp_id": exp.exp_id,
                **_rm_summary, "grade": _grade_risk_map(_rm_summary),
            })

            # ---- 执行（truth 不透明落盘，本模块不解析）
            result = adapter.execute(exp, np.random.default_rng(derive_seed(seed, "exec", round_id)))
            advance_status(store, exp, ExpStatus.EXECUTED)
            if result.truth_records is not None:
                store.save_truth(round_id, result.truth_records)

            # ---- ingest + 裁决（策略注入点：naive 全信 / os 三级 QC → adjudicate）
            obs_list = raw_to_observations(exp, result.raw_results)
            advance_status(store, exp, ExpStatus.QC_DONE)
            verdict.judge(store, obs_list, exp)
            advance_status(store, exp, ExpStatus.ROUTED)

            # ---- agent 参与（第四策略注入点，M8）：观察本轮裁决 → ACTION_PROPOSAL
            # 入提案队列 + ROUND_RATIONALE 叙述落账；裁定发生在下一轮 plan_round 开场。
            agent.after_round(store, exp, round_id)

            # ---- 聚合 + 响应模型重训 + 快照（SUSPECT/FAILED 结构性进不了训练集；
            # 软信任臂另把 QUARANTINE 观测以膨胀 alpha 内存态降权复归——落盘路由不变）
            trusted = store.list_observations(trust=TrustLevel.TRUSTED)
            _quar = _quarantined(store)
            train_obs, alpha = aggregation.prepare(
                trusted, store.list_experiments(), quarantine=_quar
            )
            model.fit(train_obs, store.list_experiments(), per_point_alpha=alpha)
            # VNext batch-1: explicit learning-weight transport event (kind named
            # per letter 042 to avoid the "decision" vocabulary collision). Only
            # policies that soft-admit observations set the surface -> zero
            # mode-branch; payload born with pv=1 (REF-1 governance at birth).
            _lw = getattr(aggregation, "last_learning_weights", None)
            if _lw:
                store.append_event("learning_weight_assigned", {
                    "pv": 1, "round_id": round_id,
                    "aggregation": getattr(aggregation, "name", type(aggregation).__name__),
                    "entries": _lw,
                })
            # 只读活性观测面（机制活性先行版，ARCH_V2 §2）：登记本轮聚合策略产出的
            # per-point alpha（obs_id ↔ alpha 一一对应）。软信任降权是否真的发生，
            # 环路级断言据此取数（soft 合成副本的 obs_id 与落盘 QUARANTINE 观测同 id →
            # 测试侧交叉分类）。所有 mode 统一发射（passthrough alpha=None → alpha 记 null），
            # 零 mode 分支、纯派生量、不改任何决策。
            _alpha_list = None if alpha is None else [float(a) for a in alpha]
            _quar_ids = {o.obs_id for o in _quar}
            store.append_event("aggregation_alpha", {
                "round_id": round_id, "aggregation": aggregation.name,
                "grade": _grade_aggregation(train_obs, alpha, _quar_ids),
                "entries": [
                    {"obs_id": o.obs_id, "cand_id": o.cand_id,
                     "alpha": (None if _alpha_list is None else _alpha_list[i])}
                    for i, o in enumerate(train_obs)
                ],
            })
            snap = model.snapshot()
            (store.root / "models" / f"snapshot_r{round_id}.json").write_text(
                json.dumps({"round_id": round_id, "snapshot": snap,
                            "n_train": model.n_train}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            store.append_event("model_updated", {
                "round_id": round_id, "snapshot": snap, "n_train": model.n_train,
            })

            advance_status(store, exp, ExpStatus.CLOSED)
            store.write_checkpoint({
                "completed_rounds": round_id + 1, "domain": cfg.name, "mode": mode,
                "seed": seed, "budget": bm.budget.model_dump(),
                "planner": planner.checkpoint_state(),  # §13.4：只存名字与数据
            })

        # Run-level closing event. R5 REF-1 P1: exit_status is a real enum
        # {success, abort, fail}; absence remains the fourth state (crash --
        # the process died before any handler could run). Never fabricate
        # success on an exception path.
        store.append_event("run_stop", {
            "exit_status": "success", "completed_rounds": rounds,
            "n_events_hint": None,  # 收口断言留给对账工具（事件×产物双向对账，§18.2）
        })
        _terminal_emitted = True
        return _summarize(store, cfg, rounds)
    except BaseException as exc:
        # R5 REF-1 P1 fix: terminal-state semantics. Crash / user abort / logic
        # failure were previously indistinguishable in the event stream (all三者
        # simply lacked run_stop). Now: KeyboardInterrupt/SystemExit -> abort,
        # any other exception -> fail, both with a reason; a hard crash still
        # leaves no run_stop (absence == crash). The emission itself is guarded:
        # a broken store must never mask the original error.
        if _terminal_emitted:
            # success stop already landed (failure in post-terminal summarize);
            # never produce a contradictory second terminal event.
            raise
        status = "abort" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "fail"
        try:
            store.append_event("run_stop", {
                "exit_status": status,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
                "completed_rounds": None, "n_events_hint": None,
            })
        except Exception as emit_err:  # best-effort; original exception wins
            _log.warning("run_stop(%s) terminal event emission failed: %s "
                         "(original exception is re-raised unchanged)",
                         status, emit_err)
        raise
    finally:
        # F2 fix: lock acquired above must be released on every exit path
        # (incl. resume-validation raises and mid-round errors), not left to
        # process teardown — otherwise same-process retry misreads a leaked
        # fd as a concurrent writer. release_writer_lock is idempotent.
        store.release_writer_lock()


def _summarize(store: RunStore, cfg: DomainConfig, rounds: int) -> dict[str, Any]:
    best = _best_so_far(store, cfg.objective.direction)
    summary = {
        "domain": cfg.name,
        "rounds_completed": (store.read_checkpoint() or {}).get("completed_rounds", 0),
        "rounds_target": rounds,
        "n_observations": len(store.list_observations()),
        "n_trusted": len(store.list_observations(trust=TrustLevel.TRUSTED)),
        "n_suspect": len(store.list_observations(trust=TrustLevel.SUSPECT)),
        "n_failed": len(store.list_observations(trust=TrustLevel.FAILED)),
        "best_trusted": best,
    }
    (store.root / "report" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
