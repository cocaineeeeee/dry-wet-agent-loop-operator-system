"""CSV → ObservationObject（模板校验，stdlib csv，不依赖 pandas）。

真实台面（bench_manual）实验后由此回灌：一份按 prepare() 板图人工填测的 CSV
被解析为 RawResult，再统一走 ingest.raw_to_observations（唯一转换通道）。

CSV 模板：
- 必需列：well_id, value
- 可选列（识别为已知元数据）：exposure, illumination, capture_index,
  solution_batch, additive_lot
- 其余数值列 → RawResult.secondary（次级测量）

校验一律响亮失败（AdapterError）：文件不存在 / 空文件 / 缺必需列 /
value 非数值 / well_id 不在布局 / well_id 重复（压力测试 finding：
重复行静默透传会造成同孔双观测污染下游）。红线：禁读真值 sidecar。
"""

from __future__ import annotations

import csv
from pathlib import Path

from expos.adapters.base import AdapterError, RawResult
from expos.adapters.ingest import raw_to_observations
from expos.kernel.objects import ExperimentObject, ObservationObject

#: 必需列
_REQUIRED = ("well_id", "value")
#: 已知可选元数据列 → 各自映射到 RawResult 字段
_META_FLOAT = ("exposure", "illumination")
_META_INT = ("capture_index",)
_META_STR = ("solution_batch", "additive_lot")
_KNOWN = set(_REQUIRED) | set(_META_FLOAT) | set(_META_INT) | set(_META_STR)


def _to_float(name: str, well: str, text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        raise AdapterError(f"well {well}: 列 {name!r} 值 {text!r} 非数值")


def load_results_csv(path: str | Path, exp: ExperimentObject) -> list[ObservationObject]:
    """读一份回灌 CSV，校验模板并转成 ObservationObject（trust=PENDING）。"""
    path = Path(path)
    if not path.exists():
        raise AdapterError(f"CSV 文件不存在: {path}")

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise AdapterError(f"空文件（无表头）: {path}")
        header = [h.strip() for h in reader.fieldnames]
        missing = [c for c in _REQUIRED if c not in header]
        if missing:
            raise AdapterError(f"CSV 缺必需列 {missing}（表头={header}）: {path}")
        rows = list(reader)

    if not rows:
        raise AdapterError(f"空文件（有表头无数据行）: {path}")

    layout_wells = {w.well_id: w for w in (exp.layout.wells if exp.layout else [])}
    raws: list[RawResult] = []
    seen_wells: set[str] = set()
    for i, row in enumerate(rows):
        clean = {(k.strip() if k else k): (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items()}
        well = clean.get("well_id") or ""
        if not well:
            raise AdapterError(f"第 {i + 1} 数据行 well_id 为空: {path}")
        wa = layout_wells.get(well)
        if wa is None:
            raise AdapterError(f"well_id {well!r} 不在 exp {exp.exp_id} 的布局中: {path}")
        if well in seen_wells:
            raise AdapterError(f"well_id {well!r} 重复出现（每孔只允许一行）: {path}")
        seen_wells.add(well)

        value = _to_float("value", well, clean.get("value", ""))

        secondary: dict[str, float] = {}
        for col in header:
            if col in _KNOWN:
                continue
            raw_val = clean.get(col)
            if raw_val is None or raw_val == "":
                continue
            secondary[col] = _to_float(col, well, raw_val)

        kwargs = {}
        for col in _META_FLOAT:
            if clean.get(col):
                kwargs[col] = _to_float(col, well, clean[col])
        for col in _META_INT:
            if clean.get(col):
                kwargs[col] = int(_to_float(col, well, clean[col]))
        for col in _META_STR:
            if clean.get(col):
                kwargs[col] = clean[col]

        raws.append(
            RawResult(
                well_id=well,
                cand_id=wa.cand_id,
                control_id=wa.control_id,
                metric=exp.objective.metric,
                value=value,
                secondary=secondary,
                **kwargs,
            )
        )

    return raw_to_observations(exp, raws, raw_kind="csv")
