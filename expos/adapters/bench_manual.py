"""真实台面 adapter（§6）：不自动执行，只出人类可读实验单。

prepare() 把布局折算成 worklist.md（每孔配液指令）+ platemap.csv（板图），
供实验员照做；实验后测得数据经 ingest.csv_loader 回灌成 ObservationObject。

设计约束（§6 / base.py）：
- **不得修改 exp**（消费只读）；exp.layout 缺失一律 AdapterError；
- execute() 一律 raise AdapterError——人工台面没有自动执行语义。
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from expos.adapters.base import AdapterError, ExecutionResult
from expos.kernel.objects import (
    Candidate,
    Control,
    ExperimentObject,
    WellAssignment,
)

#: control.kind → 人类可读类型名
_CONTROL_LABEL = {
    "sentinel": "哨兵",
    "negative": "阴性对照",
    "positive": "阳性对照",
}


class BenchManualAdapter:
    name = "bench_manual"

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})

    # ------------------------------------------------------------ 出单

    def prepare(self, exp: ExperimentObject, out_dir: str | Path) -> dict[str, Path]:
        """生成人类可读 worklist 与板图；返回 {"worklist": path, "platemap": path}。

        不修改 exp；exp.layout 为 None → AdapterError。
        """
        if exp.layout is None:
            raise AdapterError(
                f"exp {exp.exp_id} 无布局，无法出台面单（需先 design.layout 分配）"
            )

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        worklist_path = out / "worklist.md"
        platemap_path = out / "platemap.csv"

        worklist_path.write_text(self._render_worklist(exp), encoding="utf-8")
        self._write_platemap(exp, platemap_path)

        return {"worklist": worklist_path, "platemap": platemap_path}

    def execute(self, exp: ExperimentObject, rng: np.random.Generator) -> ExecutionResult:
        """人工台面不自动执行：一律响亮失败。

        流程是 prepare() 出单 → 实验员操作 → 测量数据经 ingest.csv_loader 回灌，
        这里没有可复现的自动执行语义。
        """
        raise AdapterError(
            f"bench_manual 不支持自动执行 (exp={exp.exp_id})：请用 prepare() 出单，"
            f"实验后经 ingest.csv_loader 回灌"
        )

    # ------------------------------------------------------------ 内部渲染

    @staticmethod
    def _sorted_wells(exp: ExperimentObject) -> list[WellAssignment]:
        return sorted(exp.layout.wells, key=lambda w: (w.row, w.col))

    def _render_worklist(self, exp: ExperimentObject) -> str:
        cand_by_id = {c.cand_id: c for c in exp.candidates}
        ctrl_by_id = {c.control_id: c for c in exp.controls}
        variables = list(exp.design_space.variables)

        lines: list[str] = []
        lines.append(f"# 台面实验单 worklist — {exp.exp_id}")
        lines.append("")
        lines.append(f"- round_id: {exp.round_id}")
        lines.append(f"- domain: {exp.domain}")
        lines.append(
            f"- objective: {exp.objective.name} "
            f"（metric={exp.objective.metric}, {exp.objective.direction}）"
        )
        lines.append("")
        lines.append("| 孔位 | 类型 | 条目 id | 配方 |")
        lines.append("|---|---|---|---|")

        for w in self._sorted_wells(exp):
            if w.cand_id is not None:
                item = cand_by_id.get(w.cand_id)
                kind = "候选"
                item_id = w.cand_id
                params = item.params if item is not None else {}
            else:
                item = ctrl_by_id.get(w.control_id)
                kind = _CONTROL_LABEL.get(item.kind, "对照") if item is not None else "对照"
                item_id = w.control_id
                params = item.params if item is not None else {}
            recipe = self._format_recipe(variables, params)
            lines.append(f"| {w.well_id} | {kind} | {item_id} | {recipe} |")

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_recipe(variables, params: dict) -> str:
        """按 design_space 变量顺序渲染 `名=值(单位)`；缺失变量跳过。"""
        parts: list[str] = []
        for v in variables:
            if v.name not in params:
                continue
            val = params[v.name]
            unit = f"({v.unit})" if v.unit else ""
            parts.append(f"{v.name}={val}{unit}")
        # 变量表未覆盖的额外参数也列出（无单位）
        for k, val in params.items():
            if k not in {v.name for v in variables}:
                parts.append(f"{k}={val}")
        return "; ".join(parts) if parts else "—"

    def _write_platemap(self, exp: ExperimentObject, path: Path) -> None:
        layout = exp.layout
        by_pos: dict[tuple[int, int], str] = {}
        for w in layout.wells:
            by_pos[(w.row, w.col)] = w.cand_id if w.cand_id is not None else w.control_id

        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([""] + [str(c + 1) for c in range(layout.cols)])
            for r in range(layout.rows):
                row_label = chr(ord("A") + r)
                cells = [by_pos.get((r, c), "") for c in range(layout.cols)]
                writer.writerow([row_label] + cells)
