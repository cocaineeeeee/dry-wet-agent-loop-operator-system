"""布局分配（docs/ARCHITECTURE.md §5）：哨兵固定位 + 副本跨区组 + 边缘/中心分层随机化
+ placement_hint + 风险避让。

设计原则：
- 位置是一等公民变量——布局由系统控制，否则失败归因不可辨识（公理 4）；
- **无静默降级**：容量不足、hint 无法满足、跨区组不可行一律 raise LayoutError，
  不返回部分分配的布局；
- 确定性：同输入同 seed → 输出逐字段相同（随机化种子记入 LayoutAssignment.seed）；
- 优先级次序：哨兵固定位（不受 risk_map 影响）> 跨区组（副本数 ≤ 区组数时强制）
  > 低风险孔优先 > 边缘/中心分层（尽量交替）。

本层只依赖 kernel.objects 与 numpy——不得 import 模拟器/QC/规划器/agent。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from expos.kernel.objects import (
    Candidate,
    Control,
    LayoutAssignment,
    WellAssignment,
)


from expos.errors import ExposError


class LayoutError(ExposError):
    pass


@dataclass
class _Well:
    well_id: str
    row: int
    col: int
    is_edge: bool
    block_id: str
    risk: float = 0.0
    order: int = 0  # 洗牌序，风险并列时的确定性 tiebreak


def well_id_of(row: int, col: int) -> str:
    if row > 25:
        raise LayoutError("行数超过 26，well_id 命名不支持")
    return f"{chr(ord('A') + row)}{col + 1}"


def _parse_well_id(well_id: str, rows: int, cols: int) -> tuple[int, int]:
    row = ord(well_id[0]) - ord("A")
    try:
        col = int(well_id[1:]) - 1
    except ValueError:
        raise LayoutError(f"非法 well_id: {well_id!r}")
    if not (0 <= row < rows and 0 <= col < cols):
        raise LayoutError(f"well_id {well_id!r} 越界（板 {rows}×{cols}）")
    return row, col


class LayoutPlanner:
    def __init__(
        self,
        rows: int,
        cols: int,
        seed: int,
        sentinel_wells: list[str] | None = None,
    ):
        if rows < 1 or cols < 1:
            raise LayoutError("板尺寸必须为正")
        self.rows, self.cols, self.seed = rows, cols, seed
        if sentinel_wells is None:
            corners = [(0, 0), (0, cols - 1), (rows - 1, 0), (rows - 1, cols - 1)]
            center = (rows // 2, cols // 2)
            ids = [well_id_of(r, c) for r, c in corners + [center]]
            self.sentinel_wells = list(dict.fromkeys(ids))  # 小板去重
        else:
            if len(sentinel_wells) != len(set(sentinel_wells)):
                raise LayoutError(f"sentinel_wells 含重复: {sentinel_wells}")
            for w in sentinel_wells:
                _parse_well_id(w, rows, cols)
            self.sentinel_wells = list(sentinel_wells)

    # ------------------------------------------------------------ 内部

    def _all_wells(self, rng: np.random.Generator, risk_map: dict[str, float] | None) -> list[_Well]:
        wells = []
        for r in range(self.rows):
            for c in range(self.cols):
                wid = well_id_of(r, c)
                is_edge = r in (0, self.rows - 1) or c in (0, self.cols - 1)
                block = f"Q{(2 if r >= self.rows / 2 else 0) + (1 if c >= self.cols / 2 else 0)}"
                wells.append(_Well(wid, r, c, is_edge, block))
        if risk_map:
            unknown = sorted(set(risk_map) - {w.well_id for w in wells})
            if unknown:
                raise LayoutError(f"risk_map 含未知 well_id: {unknown}（拒绝静默忽略）")
            for w in wells:
                w.risk = risk_map.get(w.well_id, 0.0)
        for i, order in enumerate(rng.permutation(len(wells))):
            wells[i].order = int(order)
        return wells

    @staticmethod
    def _take(
        pool: list[_Well],
        stratum: bool | None = None,
        exclude_blocks: set[str] | None = None,
        balance_first: bool = False,
    ) -> _Well | None:
        """取满足条件且优先级最高的孔并从池中移除。

        默认优先级 = (低风险, 区组剩余容量大者优先, 洗牌序)：中间一项做跨候选的
        区组负载均衡——高利用率布局（如 43/42 孔）下贪心不均衡会把某区组
        抽干、令后续候选的跨区组强制无解（M2 审查预言、M4 闭环实测命中）。

        ``balance_first``（跨区组强制路径专用）= (区组剩余容量大者优先, 低风险, 洗牌序)：
        把区组均衡抬到风险之上，兑现"跨区组 > 低风险"的既定优先次序（layout §设计原则）。
        教训（压测 R1-2b 收尾）：R1-2b 令 risk_map 真正非常数后，**区组相关**的风险
        （失败模型以 block_id 为一等桶维，整个象限可能同层高风险）会让"低风险优先"的
        主键把三个低风险区组整体抽干、独留高风险区组，第 k 个副本的跨区组强制当场炸
        LayoutError（tests/test_loop_soft.py 眩光带变体 47/48 满载实测命中）。0.25 粗分层
        只能救"同区组内跨层"的饿死，救不了"整块成层"——故均衡路径改由容量主导、风险退为
        次键。风险均匀时两序等价（风险恒定 → 同落 -remaining/order），既有无风险图断言不变。
        逐区组耗尽的响亮失败仍保留为兜底。"""
        remaining: dict[str, int] = {}
        for w in pool:
            remaining[w.block_id] = remaining.get(w.block_id, 0) + 1
        best_i = -1
        best_key: tuple[float, int, int] | None = None
        for i, w in enumerate(pool):
            if stratum is not None and w.is_edge != stratum:
                continue
            if exclude_blocks and w.block_id in exclude_blocks:
                continue
            if balance_first:
                key = (-remaining[w.block_id], w.risk, w.order)
            else:
                key = (w.risk, -remaining[w.block_id], w.order)
            if best_key is None or key < best_key:
                best_i, best_key = i, key
        return pool.pop(best_i) if best_i >= 0 else None

    def _pick_replicates(
        self,
        pool: list[_Well],
        k: int,
        n_blocks: int,
        hint: str | None,
        label: str,
    ) -> list[_Well]:
        picks: list[_Well] = []
        used_blocks: set[str] = set()
        enforce_blocks = k <= n_blocks  # 副本数 ≤ 区组数：跨区组强制
        for j in range(k):
            if hint == "center_only":
                strata = [False]  # 只许中心
            else:
                prefer_edge = j % 2 == 1  # 分层尽量交替：中心、边缘、中心…
                strata = [prefer_edge, not prefer_edge]
            w = None
            if enforce_blocks:
                for s in strata:
                    # balance_first：跨区组强制下让区组均衡压过低风险偏好，保住可行性
                    # （否则区组相关的风险图会抽干低风险区组、令跨区组无解，见 _take docstring）
                    w = self._take(pool, stratum=s, exclude_blocks=used_blocks,
                                   balance_first=True)
                    if w is not None:
                        break
                if w is None:
                    raise LayoutError(
                        f"{label}: 第 {j + 1}/{k} 个副本无法满足"
                        f"{'中心限定+' if hint == 'center_only' else ''}跨区组要求——"
                        f"可能是未用区组的可用孔已被先前候选抽干（板级容量精检只保证总数，"
                        f"不保证逐区组可行性）；请减少候选/副本或放宽 hint（无静默降级）"
                    )
            else:
                for s in strata:
                    w = self._take(pool, stratum=s)
                    if w is not None:
                        break
                if w is None:
                    raise LayoutError(f"{label}: 板容量不足，第 {j + 1}/{k} 个副本无孔可用")
            used_blocks.add(w.block_id)
            picks.append(w)
        return picks

    def _pick_edge_center_pair(self, pool: list[_Well], label: str) -> list[_Well]:
        edge = self._take(pool, stratum=True)
        if edge is None:
            raise LayoutError(f"{label}: edge_center_pair 无边缘孔可用")
        center = self._take(pool, stratum=False, exclude_blocks={edge.block_id})
        if center is None:
            center = self._take(pool, stratum=False)
        if center is None:
            raise LayoutError(f"{label}: edge_center_pair 无中心孔可用")
        return [edge, center]

    # ------------------------------------------------------------ 主入口

    def assign(
        self,
        candidates: list[Candidate],
        controls: list[Control],
        n_replicates: int = 2,
        risk_map: dict[str, float] | None = None,
    ) -> LayoutAssignment:
        if n_replicates < 1:
            raise LayoutError("n_replicates 必须 ≥ 1")
        for c in candidates:
            if c.placement_hint not in (None, "center_only", "edge_center_pair"):
                raise LayoutError(f"未知 placement_hint: {c.placement_hint!r}")

        rng = np.random.default_rng(self.seed)
        pool = self._all_wells(rng, risk_map)
        by_id = {w.well_id: w for w in pool}
        n_blocks = len({w.block_id for w in pool})

        # 容量预检（响亮失败，先于任何分配）
        sentinel_ctrls = [c for c in controls if c.kind == "sentinel"]
        other_ctrls = [c for c in controls if c.kind != "sentinel"]
        fixed_n = min(len(sentinel_ctrls), len(self.sentinel_wells))
        extra_sentinels = sentinel_ctrls[fixed_n:]
        needed = (
            fixed_n
            + len(extra_sentinels)
            + len(other_ctrls)
            + sum(2 if c.placement_hint == "edge_center_pair" else n_replicates for c in candidates)
        )
        capacity = self.rows * self.cols
        if needed > capacity:
            raise LayoutError(f"孔位需求 {needed} 超过板容量 {capacity}（{self.rows}×{self.cols}）")

        assigned: list[WellAssignment] = []

        def emit(w: _Well, cand_id: str | None = None, control_id: str | None = None) -> None:
            assigned.append(
                WellAssignment(
                    well_id=w.well_id, row=w.row, col=w.col,
                    cand_id=cand_id, control_id=control_id,
                    is_edge=w.is_edge, block_id=w.block_id,
                )
            )

        # 1) 哨兵固定位（四角+中心，不受 risk_map 影响——它们是板级伪影的传感器）
        for ctrl, wid in zip(sentinel_ctrls[:fixed_n], self.sentinel_wells):
            w = by_id[wid]
            if w not in pool:
                raise LayoutError(f"哨兵固定位 {wid} 已被占用")
            pool.remove(w)
            emit(w, control_id=ctrl.control_id)

        # 2) 候选：最受约束者先分（pair → center_only → 无 hint）
        def hint_rank(c: Candidate) -> int:
            return {"edge_center_pair": 0, "center_only": 1}.get(c.placement_hint, 2)

        for cand in sorted(candidates, key=hint_rank):
            label = f"候选 {cand.cand_id}"
            if cand.placement_hint == "edge_center_pair":
                picks = self._pick_edge_center_pair(pool, label)
            else:
                picks = self._pick_replicates(
                    pool, n_replicates, n_blocks, cand.placement_hint, label
                )
            for w in picks:
                emit(w, cand_id=cand.cand_id)

        # 3) 非哨兵对照与溢出哨兵：各 1 孔，取最高优先级
        for ctrl in other_ctrls + extra_sentinels:
            w = self._take(pool)
            if w is None:
                raise LayoutError(f"对照 {ctrl.control_id} 无孔可用")
            emit(w, control_id=ctrl.control_id)

        return LayoutAssignment(rows=self.rows, cols=self.cols, seed=self.seed, wells=assigned)
