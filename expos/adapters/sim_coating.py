"""涂层副域真值面：液滴干燥沉积均匀性（热插拔证明域）。

物理依据（REFERENCE_MAP §9.2/§11.2）：
- 咖啡环强度：表面活性剂抑制（Marangoni，【已核实定性】）；
- 高蒸发速率下"表面捕获"减弱环效应——垂直平流/扩散时间尺度比作一维旋钮
  【已核实：Phys. Rev. Fluids 9, 064304 (2024)；系数工程标定】；
- 过量表面活性剂致泡沫/缺陷、过热致开裂：【工程近似——保证内部最优而构造】。

与 CrystalSim 共用 SimulatorBase 的执行链与全部注入器——换域零内核改动的证明。
变量：concentration c ∈ [0.5, 5] wt%、tilt ∈ [0, 30]°、dry_temp ∈ [20, 80]°C、
surfactant ∈ [1e-4, 1e-2]（log）。
"""

from __future__ import annotations

import math
from typing import Any

from expos.adapters.sim_base import SimulatorBase


class CoatingSim(SimulatorBase):
    name = "sim_coating"
    default_metric = "uniformity_index"
    required_params = frozenset({"concentration", "tilt", "dry_temp", "surfactant"})

    def true_value(self, params: dict[str, Any]) -> float:
        c = self._num(params, "concentration", 1e-6, 20.0)
        tilt = self._num(params, "tilt", 0.0, 90.0)
        temp = self._num(params, "dry_temp", 0.0, 150.0)
        sf = self._num(params, "surfactant", 1e-8, 0.1)

        evap = (temp - 20.0) / 60.0                      # 0..1 蒸发速率代理
        surf = (math.log10(sf) + 4.0) / 2.0              # 0..1 表面活性剂（log 归一）

        # 咖啡环强度：表面活性剂抑制 × 表面捕获（高蒸发减弱环）【已核实定性】
        ring = 0.85 * math.exp(-2.2 * surf) * (1.0 - 0.45 * evap)
        foam = 0.5 * surf**3                             # 过量致泡沫缺陷【工程近似】
        crack = 0.6 * max(0.0, evap - 0.75) ** 1.5       # 过热开裂【工程近似】
        tilt_pen = 0.5 * (tilt / 30.0) ** 2              # 倾斜重力流失【工程近似】
        conc_t = math.exp(-((c - 2.2) ** 2) / (2 * 1.1**2))  # 浓度最优窗【工程近似】

        q = conc_t * (1.0 - ring) * (1.0 - foam) * (1.0 - tilt_pen) - crack
        return float(min(1.0, max(0.0, q)))

    def secondary(self, params: dict[str, Any]) -> dict[str, float]:
        """次级指标只依赖 params——原实现 coverage 耦合 true_value 构成
        可反解的真值 oracle（对抗审查公理级 finding #1），已结构性修复。"""
        sf = float(params["surfactant"])
        temp = float(params["dry_temp"])
        c = float(params["concentration"])
        surf = (math.log10(sf) + 4.0) / 2.0
        evap = (temp - 20.0) / 60.0
        ring = 0.85 * math.exp(-2.2 * surf) * (1.0 - 0.45 * evap)
        coverage = min(0.95, 0.18 * c + 0.1)  # 沉积覆盖率随浓度增长（物质守恒定性）
        return {"ring_index": ring, "coverage": coverage}
