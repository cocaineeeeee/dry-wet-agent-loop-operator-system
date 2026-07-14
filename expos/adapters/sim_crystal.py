"""结晶主域真值面（物理骨架：REFERENCE_MAP §11.2；框架：sim_base.py）。

每一项都标注【已核实】（有文献支撑的定性/形式）或【工程近似】（为演示构造，
文献未给出定量形式）——这是验收要求，勿删注释。

变量：supersaturation S ∈ [1.05, 1.6]、additive_frac f ∈ [1e-4, 1e-2]（log）、
cool_rate r ∈ [0.1, 2.0] K/h、seeded ∈ {0, 1}。
"""

from __future__ import annotations

import math
from typing import Any

from expos.adapters.sim_base import SimulatorBase


class CrystalSim(SimulatorBase):
    name = "sim_crystal"
    default_metric = "quality_index"
    required_params = frozenset({"supersaturation", "additive_frac", "cool_rate", "seeded"})

    # 【工程近似】以下常数为演示标定值，量级依据见各项注释
    B_CNT = 0.06          # CNT 势垒参数（无机盐-水量级 1–100 经缩放适配 S∈[1.05,1.6]）
    SEED_FRACTION = 0.25  # 投籽晶后初级成核被抑制的残余比例
    SEED_FLOOR = 0.08     # 籽晶提供的确定性二次成核基线
    N_OPT = 0.35          # 尺寸项的核密度尺度
    F_OPT_LOG10 = -3.3    # 添加剂促进峰位置（~5e-4 w/w）
    K_LANGMUIR = 3e-3     # Kubota-Mullin Langmuir 半饱和浓度

    def true_value(self, params: dict[str, Any]) -> float:
        S = self._num(params, "supersaturation", 1.0 + 1e-6, 3.0)  # S=1 时 lnS=0 奇异
        f = self._num(params, "additive_frac", 1e-8, 0.1)
        r = self._num(params, "cool_rate", 1e-6, 10.0)
        seeded = int(self._num(params, "seeded", 0, 1)) == 1

        # ---- 成核密度：CNT 阈值型 J ∝ exp(−B/ln²S)【已核实：经典成核理论的
        #      "先缓后爆"行为；B 具体数值为工程标定】
        lnS = math.log(S)
        n_primary = math.exp(-self.B_CNT / (lnS * lnS))
        # ---- 籽晶：二次成核主导、初级成核受抑，"少而大、重现性高"
        #      【已核实（定性，ACS CGD/PMC10326855）；系数为工程标定】
        n_eff = self.SEED_FRACTION * n_primary + self.SEED_FLOOR if seeded else n_primary

        # ---- 产率项：无核则无晶【工程近似】
        yield_t = 1.0 - math.exp(-6.0 * n_eff)
        # ---- 尺寸项：核密度高 → 平均晶粒小【已核实（质量守恒定性）；形式工程近似】
        size_t = 1.0 / (1.0 + (n_eff / self.N_OPT) ** 2)

        # ---- 均匀性：快降温 → MZW 收窄 → 同时成核多 → CV 大
        #      【定性依据已核实：Nývlt 关系 ΔT_max ∝ rate^(1/m)，m≈2–5；
        #       CV↔cool_rate 的映射形式与指数 1.5 为工程标定，非文献结论】
        cv = 0.25 + 0.5 * (r / 2.0) ** 1.5
        if seeded:
            cv *= 0.7  # 籽晶收窄分布【已核实（定性）】
        # ---- 降温速率的生长窗口：过慢则蒸发主导/杂晶竞争【工程近似——为保证
        #      r 维内部最优而构造；物理上"越慢越好"在演示预算内不成立】
        growth_window = math.exp(-((r - 0.6) ** 2) / (2 * 0.5**2))
        unif_t = max(0.0, 1.0 - cv)

        # ---- 添加剂倒 U：低剂量促进【工程外推——文献仅摘要级支持】
        #      + 高剂量 Kubota-Mullin 抑制 G/G0 = 1 − α·θ【已核实（模型形式）】
        lf = math.log10(f)
        theta = f / (f + self.K_LANGMUIR)  # Langmuir 覆盖度
        promo = 0.35 * math.exp(-((lf - self.F_OPT_LOG10) ** 2) / (2 * 0.35**2))
        add_t = max(0.0, 0.75 + promo - 0.9 * theta)

        # ---- 综合质量指数：加权几何乘积【工程构造——CSD 三要素（低成核密度、
        #      大平均晶粒、窄 CV）无公认单一标量，见 REFERENCE_MAP §11.2/§9.5】
        q = (yield_t**0.6) * (size_t**0.9) * (unif_t**0.7) * growth_window * add_t
        return float(min(1.0, max(0.0, q)))

    def secondary(self, params: dict[str, Any]) -> dict[str, float]:
        """图像法可观测的次级指标——只依赖 params（成核密度代理），
        噪声由 SimulatorBase 统一叠加。"""
        S = float(params["supersaturation"])
        seeded = int(params["seeded"]) == 1
        lnS = math.log(S)
        n_primary = math.exp(-self.B_CNT / (lnS * lnS))
        n_eff = self.SEED_FRACTION * n_primary + self.SEED_FLOOR if seeded else n_primary
        grain_count = 120.0 * n_eff
        coverage = min(0.9, 0.6 * (1.0 - math.exp(-6.0 * n_eff)))
        return {"grain_count": grain_count, "coverage": coverage}
