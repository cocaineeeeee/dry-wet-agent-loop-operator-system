"""M6 失败模型（`failure_model.py`，权威规格：本里程碑任务书 + docs/ARCHITECTURE.md §7.3）。

Beta-Bernoulli 计数模型：特征桶 = {is_edge, block_id, solution_batch, round_band}。

- **正例 k** = 落桶且**当前**裁决 ∈ {SUSPECT, FAILED} 的观测数（由 trust 驱动、与 routing
  处置正交——被 QUARANTINE 的 SUSPECT 同样计入正例，架构 §3/§7.3）；
- **曝险 n** = 落桶的全部**已裁决**观测数（TRUSTED 仅作分母，其响应值绝不进模型）；
- **PENDING 跳过**（未裁决不计入分子分母）。

event-sourced 语义的物化版：`rebuild` 以观测**当前** trust 全量重建，reclassify 一旦物化
到 `ObservationObject.trust`，改判后重建自动生效、无需增量回滚（对照 FireWorks `_rerun`
不回滚动态节点的坑，REFERENCE_MAP §13.2——靠全量重建天然规避）。

This is a *transparent conduction / amplification* layer of the upstream trust adjudication,
not an independent verification layer: it faithfully learns whatever the current adjudication
asserts and has no self-purification. Under mis-adjudication it will learn the wrong artifact
rate (e.g. a clean batch tagged high); once the upstream verdict is corrected, a rebuild flips
the estimate accordingly. Its value is making the adjudication's consequences legible and
actionable downstream — not second-guessing the adjudication.

收缩先验（REFERENCE_MAP §11.5，James-Stein 型经验贝叶斯）`Beta(m·p̄, m·(1−p̄))`，`m=5`，
`p̄` = 这批观测的全局伪影率（无已裁决观测时 p̄=0.05 兜底）。后验均值：
    p_artifact(bucket) = (m·p̄ + k) / (m + n)
空桶 → 收缩回全局 p̄（k=0,n=0 时即 m·p̄/m）。

`p_artifact_optimistic` 给 Beta 后验的**乐观（下）置信界** mean − z·std（clip≥0），供 M7
规划器折扣：桶稀疏 → std 宽 → 下界被压低 → 天然弱化折扣，缓解 RAHBO 覆盖偏差
（REFERENCE_MAP §11.5，M6 只提供、不消费）。

红线（不作弊）：本层只读 **OS 可见** provenance 特征（layout_meta / material_meta 的桶键
与 trust 裁决计数）；**绝不引用注入器内部参数或任何真值**。依赖隔离：只依赖 kernel.objects
与标准库/numpy——不 import adapters / planner / agent / models。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from expos.errors import ExposError
from expos.kernel.objects import (
    LayoutAssignment,
    ObservationObject,
    TrustLevel,
)


class FailureModelError(ExposError):
    """失败模型的响亮失败：非法先验、未知键等一律抛此异常而非静默返回。"""


#: 全局伪影率兜底值（无已裁决观测时用，任务书规定 p̄=0.05）。
_DEFAULT_P_GLOBAL = 0.05

#: 正例裁决集（分子）——由 trust 驱动，与 routing 正交。
_POSITIVE = (TrustLevel.SUSPECT, TrustLevel.FAILED)


def round_band_of(round_id: int) -> str:
    """轮次段键：两轮一段。round_band = f"r{round_id//2*2}-{round_id//2*2+1}"。"""
    lo = (round_id // 2) * 2
    return f"r{lo}-{lo + 1}"


#: Execution-face solution-batch labels are minted per round as ``R{round}-B{k}``
#: (sim_base / attribution checkerboard formula). The round prefix double-encodes the
#: round already carried by ``round_band`` — see ``_batch_key``.
_ROUND_BATCH_RE = re.compile(r"^R\d+-(?P<batch>.+)$")


def _batch_key(solution_batch: str) -> str:
    """Round-invariant batch identity for the bucket key (FM3 fix).

    The execution face mints ``solution_batch = "R{round}-B{k}"`` fresh every round, so a
    round-prefixed key (a) fragments the same physical batch into one bucket per round —
    each pinned at ~one round of exposure — and (b) double-encodes the round already held
    by ``round_band``. Stripping the ``R{round}-`` prefix (``"R7-B1" -> "B1"``) lets the
    same batch accumulate across rounds and lets a planning-time query for the batch about
    to be cast match the batch's learned history. Labels without the prefix (already
    round-invariant, e.g. test fixtures / foreign data) pass through unchanged.
    """
    m = _ROUND_BATCH_RE.match(solution_batch)
    return m.group("batch") if m else solution_batch


@dataclass(frozen=True)
class Bucket:
    """provenance 特征桶（可哈希、确定性排序）。"""

    is_edge: bool
    block_id: str
    solution_batch: str
    round_band: str  # round_band = f"r{round_id//2*2}-{round_id//2*2+1}"（两轮一段）


class FailureModel:
    """Beta-Bernoulli 伪影率计数模型（收缩先验 + 层级回退）。"""

    def __init__(self, m_prior: float = 5.0):
        if m_prior <= 0.0:
            raise FailureModelError(f"m_prior={m_prior} 非法，收缩先验强度须 >0")
        self.m_prior = float(m_prior)
        # 桶 -> (k 正例数, n 曝险数)；只保留已裁决观测。
        self._counts: dict[Bucket, list[int]] = {}
        self._p_global: float = _DEFAULT_P_GLOBAL
        self._n_total: int = 0  # 已裁决观测总数（分母）
        self._k_total: int = 0  # 正例总数（分子）

    # ------------------------------------------------------------ 重建

    def rebuild(self, observations: list[ObservationObject]) -> "FailureModel":
        """全量重建：以观测**当前** trust 计数（改判已物化，重建自动生效）。

        正例=SUSPECT/FAILED；TRUSTED 只作分母；PENDING 跳过。全局率 p̄ 也从这批观测算
        （无已裁决观测时 p̄=0.05 兜底）。就地更新并返回 self（fluent）。
        """
        counts: dict[Bucket, list[int]] = {}
        n_total = 0
        k_total = 0
        for obs in observations:
            if obs.trust == TrustLevel.PENDING:
                continue  # 未裁决不进分子分母
            bucket = self._bucket_of_obs(obs)
            slot = counts.setdefault(bucket, [0, 0])
            slot[1] += 1  # 曝险 n
            n_total += 1
            if obs.trust in _POSITIVE:
                slot[0] += 1  # 正例 k
                k_total += 1
        self._counts = counts
        self._n_total = n_total
        self._k_total = k_total
        self._p_global = (k_total / n_total) if n_total > 0 else _DEFAULT_P_GLOBAL
        return self

    @staticmethod
    def _bucket_of_obs(obs: ObservationObject) -> Bucket:
        # Round-invariant batch key (``_batch_key``): the same physical batch accumulates
        # across rounds instead of fragmenting into one death-pinned bucket per round.
        return Bucket(
            is_edge=bool(obs.layout_meta.is_edge),
            block_id=obs.layout_meta.block_id,
            solution_batch=_batch_key(obs.material_meta.solution_batch),
            round_band=round_band_of(obs.round_id),
        )

    # ------------------------------------------------------------ 计数聚合

    def _agg_full(self, bucket: Bucket) -> tuple[int, int]:
        """精确桶计数 (k, n)；空桶 → (0, 0)。"""
        slot = self._counts.get(bucket)
        return (slot[0], slot[1]) if slot is not None else (0, 0)

    def _agg_round_marginal(
        self, is_edge: bool, block_id: str, solution_batch: str
    ) -> tuple[int, int]:
        """Pool over ``round_band`` while KEEPING the batch: all round bands of the same
        (is_edge, block_id, solution_batch).

        This is the FM3 fix. The batch effect is stable across rounds, so when the exact
        (round-band-scoped) bucket is empty — as it always is at the start of a round band,
        when a planning query asks about a batch whose current-band exposure has not yet
        landed — the batch's cross-round history must be used rather than discarded. Pooling
        here preserves the batch signal that ``_agg_batch_marginal`` would otherwise average
        away, so the learned batch differences actually reach the risk map.
        """
        k = n = 0
        for b, slot in self._counts.items():
            if (
                b.is_edge == is_edge
                and b.block_id == block_id
                and b.solution_batch == solution_batch
            ):
                k += slot[0]
                n += slot[1]
        return k, n

    def _agg_batch_marginal(
        self, is_edge: bool, block_id: str, round_band: str
    ) -> tuple[int, int]:
        """对 solution_batch 维取边际：汇集同 (is_edge, block_id, round_band) 的所有批次桶。

        布局期批次未定时用（层级回退：full → 去 solution_batch）。经验贝叶斯做法是把跨批
        计数并池（pool），而非对各批率简单平均——空桶集合自然回退到全局 p̄。
        """
        k = n = 0
        for b, slot in self._counts.items():
            if b.is_edge == is_edge and b.block_id == block_id and b.round_band == round_band:
                k += slot[0]
                n += slot[1]
        return k, n

    def _agg_hier(self, bucket: Bucket) -> tuple[int, int]:
        """Hierarchical fallback counts, most-specific first, each level tried only if the
        previous is empty (n=0):

        1. exact bucket (is_edge, block, batch, round_band);
        2. round-marginal — pool the batch across all round bands (KEEP batch), so the
           learned batch signal survives when the current band has no exposure yet;
        3. batch-marginal — pool all batches in this (is_edge, block, round_band) band
           (DROP batch), the spatial prior for a batch with no history at all;
        4. all empty -> (0, 0), which the posterior shrinks back to the global rate p̄.

        Rationale (FM3): batch labels are minted per round (``R{round}-B{k}``), so at the
        start of a round band the exact bucket for the batch about to be cast is empty.
        Level 2 keeps that batch's cross-round history in the estimate instead of averaging
        it into a batch-agnostic band rate — the fix for "the batch dimension is learned but
        never reaches the risk map". Levels 3-4 preserve the R1-2b guarantee that
        is_edge/block/band history is never dropped straight to a flat constant map.
        """
        k, n = self._agg_full(bucket)
        if n == 0:
            k, n = self._agg_round_marginal(
                bucket.is_edge, bucket.block_id, bucket.solution_batch
            )
        if n == 0:
            k, n = self._agg_batch_marginal(
                bucket.is_edge, bucket.block_id, bucket.round_band
            )
        return k, n

    # ------------------------------------------------------------ 后验

    def _posterior_mean(self, k: int, n: int) -> float:
        """收缩后验均值 (m·p̄ + k)/(m + n)。空桶 (k=0,n=0) → 全局 p̄。"""
        m, p = self.m_prior, self._p_global
        return (m * p + k) / (m + n)

    def _posterior_std(self, k: int, n: int) -> float:
        """Beta(α, β) 后验标准差，α=m·p̄+k, β=m·(1−p̄)+(n−k)。"""
        m, p = self.m_prior, self._p_global
        alpha = m * p + k
        beta = m * (1.0 - p) + (n - k)
        s = alpha + beta
        var = (alpha * beta) / (s * s * (s + 1.0))
        return var**0.5

    def p_artifact(
        self, is_edge: bool, block_id: str, solution_batch: str, round_id: int
    ) -> float:
        """桶后验均值 (m·p̄ + k)/(m + n)；层级回退（见 ``_agg_hier``）：精确桶 → 跨轮
        同批边际 → 跨批边际 → 全局 p̄。查询侧对 ``solution_batch`` 同样做去轮次化
        (``_batch_key``)，使查询键与存储键对齐（"R7-B1" 与 "B1" 命中同一桶）。"""
        bucket = Bucket(
            is_edge=bool(is_edge),
            block_id=block_id,
            solution_batch=_batch_key(solution_batch),
            round_band=round_band_of(round_id),
        )
        k, n = self._agg_hier(bucket)
        return self._posterior_mean(k, n)

    def p_artifact_optimistic(
        self,
        is_edge: bool,
        block_id: str,
        solution_batch: str,
        round_id: int,
        z: float = 1.0,
    ) -> float:
        """乐观（下）置信界——Beta 后验的 mean − z·std，clip≥0；供规划器折扣防覆盖偏差
        （REFERENCE_MAP §11.5 RAHBO 结论）。计数走层级回退（同 `p_artifact`），
        ``solution_batch`` 同样去轮次化 (``_batch_key``)。"""
        bucket = Bucket(
            is_edge=bool(is_edge),
            block_id=block_id,
            solution_batch=_batch_key(solution_batch),
            round_band=round_band_of(round_id),
        )
        k, n = self._agg_hier(bucket)
        mean = self._posterior_mean(k, n)
        std = self._posterior_std(k, n)
        return max(0.0, mean - z * std)

    # ------------------------------------------------------------ risk_map

    def risk_map(
        self,
        layout: LayoutAssignment,
        round_id: int,
        solution_batch_hint: str | None = None,
    ) -> dict[str, float]:
        """整板伪影率热图（后验均值点估计）。

        键**严格** = layout 的 well_id 集合（LayoutPlanner 会拒未知键，见 M2 检查点）。
        布局期未知批次（`solution_batch_hint is None`）→ 对批次维取边际（去 batch 维、
        跨批并池）。
        """
        band = round_band_of(round_id)
        out: dict[str, float] = {}
        for w in layout.wells:
            if solution_batch_hint is not None:
                k, n = self._agg_full(
                    Bucket(
                        is_edge=bool(w.is_edge),
                        block_id=w.block_id,
                        solution_batch=_batch_key(solution_batch_hint),
                        round_band=band,
                    )
                )
            else:
                k, n = self._agg_batch_marginal(bool(w.is_edge), w.block_id, band)
            out[w.well_id] = self._posterior_mean(k, n)
        return out

    # ------------------------------------------------------------ 摘要

    def summary(self) -> dict:
        """桶计数表，供 UI/事件。确定性排序（按桶键）。

        契约键：``"p_global"`` 是规划器风险折扣的消费键（planner.policy 读取处
        缺键即响亮失败）——任何一侧改名都会当场炸，不许静默回退（压测 R1-2a 教训：
        读取处曾用 ``.get("global_rate", 0.0)`` 默认值兜底，键名失配被吞、折扣恒 1）。
        """
        buckets = []
        for b in sorted(
            self._counts,
            key=lambda x: (x.is_edge, x.block_id, x.solution_batch, x.round_band),
        ):
            k, n = self._counts[b]
            buckets.append(
                {
                    "is_edge": b.is_edge,
                    "block_id": b.block_id,
                    "solution_batch": b.solution_batch,
                    "round_band": b.round_band,
                    "k": k,
                    "n": n,
                    "p_artifact": self._posterior_mean(k, n),
                }
            )
        return {
            "m_prior": self.m_prior,
            "p_global": self._p_global,
            "n_total": self._n_total,
            "k_total": self._k_total,
            "n_buckets": len(self._counts),
            "buckets": buckets,
        }
