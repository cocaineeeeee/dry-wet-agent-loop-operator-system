"""裁决与聚合策略（docs/ARCHITECTURE.md §7.4，docs/DEEP_REVIEW.md §3.1/§3.2/§2B）。

两个正交的单一注入点，loop 主体因此零 mode 分支（DEEP_REVIEW §3.2 红线：
"两臂仅在裁决策略与聚合策略对象上不同"）：

- **VerdictPolicy**：观测 → trust/routing 裁决。最终判决必须走内核纯函数
  ``lifecycle.adjudicate``（公理 7：裁决不吃任何 agent 产物），本层只负责
  编排（收集 QC 证据、逐观测落盘、写事件），不复制裁决逻辑。
- **AggregationPolicy**：可信观测 → 响应模型训练样本（含逐点 alpha）。
  DEEP_REVIEW §3.1"信任二值、证据连续"的桥：副本方差 → per-point alpha。

依赖红线：本模块只依赖 kernel（lifecycle/objects/store）+ errors + numpy，
不反向依赖任何上层编排/外交/执行模块，**不触碰真值 sidecar**（公理 6）。
"""

from __future__ import annotations

import hashlib
import statistics
from typing import Callable, Protocol, runtime_checkable

import numpy as np

from expos.errors import ExposError
from expos.kernel.lifecycle import TrustPolicy, route_observation
from expos.kernel.objects import (
    ExperimentObject,
    MeasuredResult,
    ObservationObject,
    QCReport,
    Routing,
    TrustLevel,
)
from expos.kernel.store import RunStore


class PolicyError(ExposError):
    user_facing = False  # 缺 QC 报告/契约破坏多为编程 bug，CLI 保留 traceback


#: qc_runner 契约：实验 + 本轮观测 + 历史观测 → {obs_id: QCReport}。
#: 由构造器注入（loop 接线时传 checks.run_qc），解耦 checks 模块与裁决编排。
QCRunner = Callable[
    [ExperimentObject, "list[ObservationObject]", "list[ObservationObject]"],
    "dict[str, QCReport]",
]


# ================================================================ 裁决策略

@runtime_checkable
class VerdictPolicy(Protocol):
    """裁决策略——loop 的单一注入点，主体零 mode 分支（DEEP_REVIEW 红线）。"""

    name: str

    def judge(
        self,
        store: RunStore,
        obs_list: list[ObservationObject],
        exp: ExperimentObject,
    ) -> list[ObservationObject]:
        """就地裁决 + 落盘 + 写事件，返回更新后的观测。"""
        ...


class NaivePolicy:
    """对照组：一切 TRUSTED + TO_RESPONSE_MODEL（迁移自 loop._route_naive）。

    行为与 M4 ``_route_naive`` 逐字段一致——含 routing_bulk 事件的 payload 结构，
    对比公平性的回归红线（naive 与 os 只在策略对象上不同）。
    """

    name = "naive"

    def judge(
        self,
        store: RunStore,
        obs_list: list[ObservationObject],
        exp: ExperimentObject,
    ) -> list[ObservationObject]:
        for obs in obs_list:
            obs.trust = TrustLevel.TRUSTED
            obs.routing = Routing.TO_RESPONSE_MODEL
            obs.trust_confidence = 1.0
            store.save_observation(obs)
        store.append_event(
            "routing_bulk",
            {
                "mode": "naive",
                "n": len(obs_list),
                "round_id": obs_list[0].round_id if obs_list else None,
            },
        )
        return obs_list


class QCPolicy:
    """os 臂：QC 证据 → 逐观测走 ``lifecycle.adjudicate`` 裁决。

    ``qc_runner(exp, obs_list, history) -> {obs_id: QCReport}`` 由构造器注入
    （解耦 checks 模块——loop 接线时传入 ``checks.run_qc``）；随后逐观测把报告
    挂上 ``obs.qc``，走 ``route_observation``（内核纯函数 ``adjudicate`` + 落盘
    + routing 事件）。trust 阈值取自 ``trust_policy``（值来自域配置 ``cfg.trust``）。
    末尾写一条 ``qc_report`` 汇总事件。
    """

    name = "qc"

    def __init__(
        self,
        qc_runner: QCRunner,
        trust_policy: TrustPolicy | None = None,
        attributor=None,
        action_proposer=None,
    ):
        """attributor(obs, report, plate, exp) -> FailureAttribution 与
        action_proposer(obs, attribution) -> RecommendedAction|None 为 M6 注入点
        （loop 传 qc.attribution.attribute/propose_action）；qc_runner 可返回
        reports dict 或 (reports, plate) 二元组——无 plate 时跳过归因（向后兼容）。"""
        self.qc_runner = qc_runner
        self.trust_policy = trust_policy or TrustPolicy()
        self.attributor = attributor
        self.action_proposer = action_proposer

    def judge(
        self,
        store: RunStore,
        obs_list: list[ObservationObject],
        exp: ExperimentObject,
    ) -> list[ObservationObject]:
        # 历史观测（跨轮哨兵/副本上下文）——本轮观测尚未落盘，故这是先前各轮
        history = store.list_observations()
        runner_out = self.qc_runner(exp, obs_list, history)
        if isinstance(runner_out, tuple):
            reports, plate = runner_out
        else:  # 旧契约（仅 reports）——无板级上下文即不做归因
            reports, plate = runner_out, None

        n_trusted = n_suspect = n_failed = 0
        check_counts: dict[str, int] = {}
        for obs in obs_list:
            report = reports.get(obs.obs_id)
            if report is not None:
                obs.qc = report
            if obs.qc is None:
                raise PolicyError(
                    f"obs {obs.obs_id} 无 QCReport：qc_runner 未产报告且观测自身无 qc"
                )
            # 统计触发（未通过）的检查
            for chk in obs.qc.checks:
                if not chk.passed:
                    check_counts[chk.name] = check_counts.get(chk.name, 0) + 1
            # 唯一裁决入口：内核纯函数（公理 7）
            route_observation(store, obs, self.trust_policy)
            if obs.trust == TrustLevel.TRUSTED:
                n_trusted += 1
            else:
                if obs.trust == TrustLevel.FAILED:
                    n_failed += 1
                else:  # SUSPECT
                    n_suspect += 1
                # M6：可疑/失败观测的归因 + 下轮动作建议（证据不是决策——
                # 不改 trust，只写 failure_attr/next_action，动作 M7 仲裁消费）
                if self.attributor is not None and plate is not None:
                    attr = self.attributor(obs, obs.qc, plate, exp)
                    obs.failure_attr = attr
                    if self.action_proposer is not None:
                        obs.next_action = self.action_proposer(obs, attr)
                    # log-before-data（WAL 纪律，对齐 lifecycle.route_observation：
                    # 先 append_event 再 save_observation）。崩于两步间时日志领先视图
                    # （可重放修复），而非 failure_attr/next_action 落盘却无事件解释。
                    # 事件 payload 仅引用已算出的归因局部量，翻转写序不改内容。
                    store.append_event("attribution", {
                        "obs_id": obs.obs_id, "round_id": exp.round_id,
                        "top_cause": attr.top_cause, "confidence": attr.confidence,
                        "next_action": (obs.next_action.action.value
                                        if obs.next_action else None),
                    })
                    store.save_observation(obs)

        store.append_event(
            "qc_report",
            {
                "round_id": exp.round_id,
                "n_trusted": n_trusted,
                "n_suspect": n_suspect,
                "n_failed": n_failed,
                "check_counts": check_counts,
            },
        )
        return obs_list


# ================================================================ 聚合策略

@runtime_checkable
class AggregationPolicy(Protocol):
    """聚合策略——响应模型训练前的观测→训练样本变换（M9 三臂的第二注入点）。"""

    name: str

    def prepare(
        self,
        trusted: list[ObservationObject],
        experiments: list[ExperimentObject],
        quarantine: "list[ObservationObject]" = (),
    ) -> tuple[list[ObservationObject], np.ndarray | None]:
        """返回 (训练观测, per_point_alpha 或 None)；alpha 与观测一一对应。

        ``quarantine``：本轮 routing==QUARANTINE 的可疑观测（软信任臂用来内存态
        降权复归；其余策略忽略之——向后兼容，签名带默认值）。"""
        ...


def _direction_lut(experiments: list[ExperimentObject]) -> dict[str, str]:
    return {e.exp_id: e.objective.direction for e in experiments}


def _default_direction(experiments: list[ExperimentObject]) -> str:
    return experiments[0].objective.direction if experiments else "maximize"


def _derive_obs_id(prefix: str, cand_id: str) -> str:
    """确定性派生 obs_id：同 cand_id 恒同值（合成观测可复现）。"""
    h = hashlib.sha256(cand_id.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


def _mean_secondary(group: list[ObservationObject]) -> dict[str, float]:
    """副本 secondary 逐键均值（键并集，缺失键只在出现处平均）。"""
    keys: set[str] = set()
    for o in group:
        keys.update(o.result.secondary.keys())
    out: dict[str, float] = {}
    for k in sorted(keys):
        vals = [o.result.secondary[k] for o in group if k in o.result.secondary]
        if vals:
            out[k] = float(statistics.fmean(vals))
    return out


class PassthroughAggregation:
    """naive：逐孔直用，alpha=None（M4 现状，回归基准）。"""

    name = "passthrough"

    def prepare(
        self,
        trusted: list[ObservationObject],
        experiments: list[ExperimentObject],
        quarantine: "list[ObservationObject]" = (),
    ) -> tuple[list[ObservationObject], np.ndarray | None]:
        return list(trusted), None


class MedianAggregation:
    """robust-blind：同 cand_id 的副本合并为一条稳健位置估计观测（无 QC、无信任路由）。

    y 聚合路径（M9_PROTOCOL §1 L34-36 冻结规格，STRESS_TEST_R1 R1-3(c) 修复）：
    n≥3 时 m=median(yᵢ)、s=MAD·1.4826，Huber 位置估计 μ̂ 以 δ=1.345·s IRLS 迭代
    （≤10 次；s=0 时 μ̂=m）。构造开关 ``use_huber`` 默认开（协议默认）；
    ``use_huber=False`` 退回纯中位数（旧行为，供对照/回归）。
    n=2 时 median=mean 无鲁棒性——按 M9 修正版语义（协议 L44-47）**保守选**
    （maximize 取 min，minimize 取 max），Huber 开关不影响该退化分支。
    已记 deviation：协议同款 alpha=max(noise_sd², s²/r) 未在本聚合器实现
    （聚合层不知 noise_sd，robust 臂 alpha 仍为 None）；且 n=2 域配置下
    Huber 同样退化，有效对照需 n=3 副本场景变体（见重跑清单）。

    合成观测仍是合法 TRUSTED / TO_RESPONSE_MODEL 观测：obs_id 确定性派生、
    layout_meta 取首个副本、secondary 取均值。非候选观测（控制孔，cand_id 为空）
    原样透传。
    """

    name = "median"

    def __init__(
        self,
        use_huber: bool = True,
        huber_c: float = 1.345,
        max_iter: int = 10,
    ):
        self._use_huber = bool(use_huber)
        self._huber_c = float(huber_c)
        self._max_iter = int(max_iter)

    def _huber_location(self, values: list[float]) -> float:
        """Huber 位置估计（协议 §1 精确算法）：m=median，s=MAD·1.4826，
        δ=huber_c·s，IRLS（权重 w=min(1, δ/|y−μ|)）≤ max_iter 次；s=0 → m。"""
        m = float(statistics.median(values))
        mad = float(statistics.median([abs(v - m) for v in values]))
        s = 1.4826 * mad
        if s <= 0.0:
            return m
        delta = self._huber_c * s
        mu = m
        for _ in range(self._max_iter):
            weights = [
                1.0 if abs(v - mu) <= delta else delta / abs(v - mu) for v in values
            ]
            new_mu = sum(w * v for w, v in zip(weights, values)) / sum(weights)
            converged = abs(new_mu - mu) <= 1e-12
            mu = new_mu
            if converged:
                break
        return float(mu)

    def prepare(
        self,
        trusted: list[ObservationObject],
        experiments: list[ExperimentObject],
        quarantine: "list[ObservationObject]" = (),
    ) -> tuple[list[ObservationObject], np.ndarray | None]:
        dir_lut = _direction_lut(experiments)
        default_dir = _default_direction(experiments)

        groups: dict[str, list[ObservationObject]] = {}
        controls: list[ObservationObject] = []
        for obs in trusted:
            if obs.cand_id is None:
                controls.append(obs)
            else:
                groups.setdefault(obs.cand_id, []).append(obs)

        out: list[ObservationObject] = []
        for cand_id in sorted(groups):
            group = groups[cand_id]
            valued = [o for o in group if o.result.value is not None]
            if not valued:
                continue
            values = [float(o.result.value) for o in valued]
            direction = dir_lut.get(valued[0].exp_id, default_dir)
            n = len(values)
            if n == 2:
                # 协议 L44-47 n=2 退化处理：保守选（Huber 开关不影响此分支）
                y_agg = min(values) if direction == "maximize" else max(values)
            elif self._use_huber:
                y_agg = self._huber_location(values)
            else:
                y_agg = float(statistics.median(values))
            first = valued[0]
            out.append(
                ObservationObject(
                    obs_id=_derive_obs_id("obsmed", cand_id),
                    exp_id=first.exp_id,
                    round_id=first.round_id,
                    cand_id=cand_id,
                    result=MeasuredResult(
                        metric=first.result.metric,
                        value=y_agg,
                        unit=first.result.unit,
                        secondary=_mean_secondary(valued),
                    ),
                    layout_meta=first.layout_meta,
                    trust=TrustLevel.TRUSTED,
                    routing=Routing.TO_RESPONSE_MODEL,
                    trust_confidence=1.0,
                )
            )
        out.extend(controls)
        return out, None


class ReplicateVarianceAggregation:
    """os：逐孔保留 + 逐点 alpha（DEEP_REVIEW §3.1 的桥，§11.1 逐点 alpha 路线）。

    同 cand_id 的副本共享 alpha = 副本方差 / n（方差大→降权大）；无副本的孔
    （含控制孔）用**组间中位方差**兜底。观测按输入序原样保留，alpha 数组与之一一对应。
    """

    name = "replicate_variance"

    @staticmethod
    def _replicate_stats(
        trusted: list[ObservationObject],
    ) -> tuple[dict[str, list[float]], dict[str, float], float]:
        """副本方差统计：返回 (每 cand_id 值序列, 多副本 cand 的方差, 组间中位方差兜底)。
        供本类与 SoftTrustAggregation 共用（单一真相源）。"""
        groups: dict[str, list[float]] = {}
        for obs in trusted:
            if obs.cand_id is not None and obs.result.value is not None:
                groups.setdefault(obs.cand_id, []).append(float(obs.result.value))

        group_var: dict[str, float] = {}
        multi_vars: list[float] = []
        for cand_id, vals in groups.items():
            if len(vals) >= 2:
                v = float(statistics.variance(vals))
                group_var[cand_id] = v
                multi_vars.append(v)
        fallback_var = float(statistics.median(multi_vars)) if multi_vars else 0.0
        return groups, group_var, fallback_var

    @classmethod
    def _alpha_base(
        cls,
        cid: "str | None",
        groups: dict[str, list[float]],
        group_var: dict[str, float],
        fallback_var: float,
    ) -> float:
        """单点 alpha 基线：多副本孔取 方差/n，其余（无副本孔/控制孔）取组间中位方差。"""
        if cid is not None and cid in group_var:
            return group_var[cid] / len(groups[cid])
        return fallback_var

    def prepare(
        self,
        trusted: list[ObservationObject],
        experiments: list[ExperimentObject],
        quarantine: "list[ObservationObject]" = (),
    ) -> tuple[list[ObservationObject], np.ndarray | None]:
        groups, group_var, fallback_var = self._replicate_stats(trusted)
        alphas = [
            self._alpha_base(obs.cand_id, groups, group_var, fallback_var)
            for obs in trusted
        ]
        return list(trusted), np.asarray(alphas, dtype=float)


class SoftTrustAggregation:
    """os-soft 臂（软信任，docs/SOFT_TRUST_PROPOSAL.md 的忠实落地）。

    包装 ReplicateVarianceAggregation 的副本方差 alpha 基线，额外把
    **routing==QUARANTINE** 的可疑观测（suspicion∈[quarantine_low, suspect_high)）
    以**内存态合成 TRUSTED+TO_RESPONSE_MODEL 副本**并入训练集——**不落盘、不改原观测**
    （deepcopy 后改 trust/routing 字段仅供 fit 守门通过；持久化观测仍是 QUARANTINE，
    审计与路由枚举一律不变）。

    信任权重 w(s)（带内线性斜坡，下边界连续、上边界有意跳变——R1 P3 核实修正）：
        w(s) = clip((suspect_high − s)/(suspect_high − quarantine_low), w_min, 1)
    · 下边界 s→quarantine_low⁺ 时 w→1：与 TRUSTED 侧（满权重 alpha_base）**连续衔接**。
    · 带内 (quarantine_low, suspect_high) 线性单调递减；因 w_min 下限，w 在
      s∈[suspect_high−w_min·denom, suspect_high) 一段被夹平于 w_min（默认约 [0.585,0.6)）。
    · 上边界 s→suspect_high⁻ 时 w→w_min=0.05（**非** 0）；而 s≥suspect_high 的观测被硬隔离
      （routing≠QUARANTINE、完全剔除、有效权重 0）。故上边界处 w 从 w_min **跳变**到剔除，
      **不连续**——w_min 是刻意保留的最小残余权重下限，非与硬隔离的平滑衔接（不吹"连续衔接"）。
    alpha 乘性膨胀 alpha_i = alpha_base_i / w(s_i)
    （异方差 GP/GLS 正解：降权=方差÷w；对齐 Ax SEM²/w、botorch rho 膨胀语义）。

    硬隔离不变量：只触碰 routing==QUARANTINE；suspicion≥suspect_high→TO_FAILURE_MODEL、
    硬检查失败→FAILED 的观测**绝不并入**（不在 quarantine 集内，永不复归）。
    无观测落入 QUARANTINE 带时，与 os 臂逐比特一致（软化只在该带激活）。
    """

    name = "soft_trust"

    def __init__(
        self,
        suspect_high: float = 0.6,
        quarantine_low: float = 0.3,
        w_min: float = 0.05,
    ):
        self._base = ReplicateVarianceAggregation()
        self._suspect_high = float(suspect_high)
        self._quarantine_low = float(quarantine_low)
        self._w_min = float(w_min)

    def _weight(self, s: float) -> float:
        """线性斜坡信任权重 w(s)∈[w_min, 1]。"""
        denom = self._suspect_high - self._quarantine_low
        if denom <= 0:
            return 1.0
        w = (self._suspect_high - s) / denom
        return float(min(1.0, max(self._w_min, w)))

    def prepare(
        self,
        trusted: list[ObservationObject],
        experiments: list[ExperimentObject],
        quarantine: "list[ObservationObject]" = (),
    ) -> tuple[list[ObservationObject], np.ndarray | None]:
        train_obs, alpha_base = self._base.prepare(trusted, experiments)
        # learning_weight_assigned transport surface (letter 042 refinement 1-3):
        # loop reads this optional attribute after prepare() and emits the event;
        # base policies never set it, so the loop stays zero-mode-branch. Reset
        # every call so an empty soft band leaves no stale entries.
        self.last_learning_weights = []

        # 只并入 routing==QUARANTINE 且有测量值者（FAILED/TO_FAILURE_MODEL 绝不复归）
        soft = [
            o for o in quarantine
            if o.routing == Routing.QUARANTINE and o.result.value is not None
        ]
        if not soft:
            return train_obs, alpha_base

        groups, group_var, fallback_var = self._base._replicate_stats(trusted)
        extra_obs: list[ObservationObject] = []
        extra_alpha: list[float] = []
        for obs in soft:
            # VNext batch-1 (Part IV Q2 resolution): suspicion is read from its
            # single authoritative source, obs.qc.suspicion -- NOT trust_confidence.
            # This also fixes the HY-1 RESCUE inversion: a human reclassify to
            # QUARANTINE stamps trust_confidence=1.0 (adjudication certainty),
            # which the old read misinterpreted as maximum suspicion and slammed
            # the weight to the floor, opposite to the human intent.
            s = float(obs.qc.suspicion) if obs.qc is not None else float(obs.trust_confidence)
            w = self._weight(s)
            base_i = self._base._alpha_base(obs.cand_id, groups, group_var, fallback_var)
            extra_alpha.append(base_i / w)  # multiplicative inflation (w<=1 -> downweight)
            # Covert channel DELETED (semantics via facet, transport via explicit
            # parameter): the ORIGINAL QUARANTINE observation is appended, fields
            # untouched -- fit admits it because the explicit alpha vector is the
            # learning-policy admission record. No synthetic TRUSTED copy exists
            # anywhere anymore.
            extra_obs.append(obs)
            self.last_learning_weights.append({
                "obs_id": obs.obs_id, "weight": w, "alpha_inflated": base_i / w,
                # basis is upgradeable to an evidence function (EVIDENCE_TYPING
                # spec) without schema change; cert_class is RESERVED and
                # unconsumed until the Certification Policy layer lands -- do
                # not fake-consume it (C2 lesson).
                "basis": "trust_mapping_v1", "cert_class": None,
            })

        base_arr = (np.zeros(len(train_obs), dtype=float)
                    if alpha_base is None else np.asarray(alpha_base, dtype=float))
        out_alpha = np.concatenate([base_arr, np.asarray(extra_alpha, dtype=float)])
        return list(train_obs) + extra_obs, out_alpha
