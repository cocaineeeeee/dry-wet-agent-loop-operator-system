"""canonical 设计空间 ↔ 单位立方（docs/ARCHITECTURE.md §5）。

本层只依赖 kernel.objects 与 numpy——不得 import 模拟器/QC/规划器/agent。
连续变量支持 linear/log 变换；categorical 以等距索引映射进 [0,1] 并在解码时 snap。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from expos.kernel.objects import Constraint, DesignSpace, VariableDef


from expos.errors import ExposError


class DesignError(ExposError):
    pass


def dim(space: DesignSpace) -> int:
    return len(space.variables)


def _to_unit_one(var: VariableDef, value: Any) -> float:
    if var.kind == "categorical":
        try:
            idx = var.choices.index(value)
        except ValueError:
            raise DesignError(f"变量 {var.name}: {value!r} 不在 choices {var.choices} 中")
        k = len(var.choices)
        return 0.0 if k == 1 else idx / (k - 1)
    x = float(value)
    if not var.low <= x <= var.high:
        raise DesignError(f"变量 {var.name}: {x} 超出 [{var.low}, {var.high}]")
    if var.transform == "log":
        return (math.log(x) - math.log(var.low)) / (math.log(var.high) - math.log(var.low))
    return (x - var.low) / (var.high - var.low)


def _from_unit_one(var: VariableDef, u: float) -> Any:
    u = float(min(1.0, max(0.0, u)))
    if var.kind == "categorical":
        k = len(var.choices)
        idx = 0 if k == 1 else int(round(u * (k - 1)))
        return var.choices[idx]
    if var.transform == "log":
        x = math.exp(math.log(var.low) + u * (math.log(var.high) - math.log(var.low)))
    else:
        x = var.low + u * (var.high - var.low)
    # 裁剪到边界：exp/乘加的浮点微溢出会让 u=0/1 反解出略越界的值，
    # 使合法点无法往返（hypothesis 属性测试发现的真实反例）
    return min(var.high, max(var.low, x))


def to_unit(space: DesignSpace, params: dict[str, Any]) -> np.ndarray:
    """params → 单位立方向量。缺失/未知变量、越界一律 DesignError（拒绝而非静默修剪）。"""
    unknown = set(params) - {v.name for v in space.variables}
    if unknown:
        raise DesignError(f"未知变量: {sorted(unknown)}")
    out = []
    for var in space.variables:
        if var.name not in params:
            raise DesignError(f"缺失变量: {var.name}")
        out.append(_to_unit_one(var, params[var.name]))
    return np.asarray(out, dtype=float)


def from_unit(space: DesignSpace, u: np.ndarray) -> dict[str, Any]:
    u = np.asarray(u, dtype=float).ravel()
    if u.shape[0] != dim(space):
        raise DesignError(f"维度不符: 期望 {dim(space)}, 得到 {u.shape[0]}")
    return {var.name: _from_unit_one(var, u[i]) for i, var in enumerate(space.variables)}


# ---------------------------------------------------------------- 约束

def _require(params: dict[str, Any], name: str, constraint_name: str) -> Any:
    """约束引用了不存在的变量（多为拼错名）必须响亮失败——
    静默放行会让安全上限永不生效（对抗审查红线 finding）。"""
    if name not in params:
        raise DesignError(f"约束 {constraint_name!r} 引用了不存在的变量 {name!r}")
    return params[name]


def _check_one(params: dict[str, Any], c: Constraint) -> bool:
    if c.kind == "range":
        v = float(_require(params, c.params["var"], c.name))
        lo = c.params.get("min", -math.inf)
        hi = c.params.get("max", math.inf)
        return lo <= v <= hi
    if c.kind == "sum_leq":
        total = sum(float(_require(params, name, c.name)) for name in c.params["vars"])
        return total <= float(c.params["max"])
    if c.kind == "forbidden_combo":
        conditions: dict[str, Any] = c.params["conditions"]
        return not all(_require(params, k, c.name) == v for k, v in conditions.items())
    raise DesignError(f"未知约束类型: {c.kind}")


def check_constraints(params: dict[str, Any], restrictions: list[Constraint] | None) -> bool:
    """全部约束满足才为 True。约束类型: range / sum_leq / forbidden_combo。"""
    return all(_check_one(params, c) for c in restrictions or [])


def validate_params(space: DesignSpace, params: dict[str, Any],
                    restrictions: list[Constraint] | None = None) -> None:
    """严格校验：范围/取值经 to_unit 检查，再过约束；失败抛 DesignError。"""
    to_unit(space, params)
    if not check_constraints(params, restrictions):
        raise DesignError(f"params 违反约束: {params}")
