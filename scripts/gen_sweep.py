#!/usr/bin/env python3
"""M9 扫描脚手架生成器（docs/M9_PROTOCOL.md §2/§4.3/§5；幅度网格照 REFERENCE_MAP §14 修正）。

从一个基准域 yaml 生成三样脚手架（仓库只留本脚本，产物落 scratchpad/演示）：

  <out>/scenarios/<scenario_id>.yaml   场景变体域配置（确定性命名，全部经 load_domain 校验）
  <out>/cells.tsv                      (scenario_id, arm, seed, seed_set, domain_yaml) 展开表
  <out>/sweep.sbatch                   Slurm 数组作业模板（--array 按 cells.tsv 行数）

场景矩阵（§2 的本地脚手架子集，只含 loop 侧已接线的注入器网格）：
  S0.demo    1  基准 crystal.yaml 现状（demo 第一幕）
  S1.zero    1  关闭全部注入器（QC 税专用；noise_sd 保留）
  S2 单伪影×幅度网格（§14 修正后的默认档）：
    edge_evaporation strength ∈ {0.05,0.10,0.15,0.20,0.35}  （§14：加 0.15 细化陡段、删饱和端）
    batch_shift      shift    ∈ {−0.05,−0.07,−0.10,−0.18}   （§14：加 0.07）
    glare            prob     ∈ {0.02,0.05,0.08,0.15}        （§14：改扫 prob 而非 boost）
    thermal_gradient magnitude∈ {0.10,0.20,0.30,0.50}        （§14：上探 0.3–0.5 避免贴地板）

种子 A/B 集分离（§2/§4.3 标定/评估分离）：每注入器幅度档按**索引奇偶**分家——
  偶数档 → 标定集 A（seed∈[0, calib_seeds)）；奇数档 → 评估集 B（seed∈[1000, 1000+eval_seeds)）。
  S0/S1 归评估集 B（QC 税与 demo 是报数来源）。A 锁阈值、B 冻结报数，绝不回标。

臂：默认 naive,os（loop 已接线）。robust(-blind) 臂 loop 侧未接线（run_cell 抛 NotImplementedError），
不进 cells.tsv；协议 §1 三臂设计在此以注释留位。
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from expos.domain import load_domain  # noqa: E402

# §14 修正后的默认幅度网格（每档 (scenario_id 后缀, 注入器条目)）
_GRID = {
    "edge_evaporation": ("strength", [0.05, 0.10, 0.15, 0.20, 0.35],
                         {"decay_wells": 1.0}),
    "batch_shift": ("shift", [-0.05, -0.07, -0.10, -0.18], {"batch_suffix": "B1"}),
    "glare": ("prob", [0.02, 0.05, 0.08, 0.15], {"boost": 0.35}),
    "thermal_gradient": ("magnitude", [0.10, 0.20, 0.30, 0.50], {"axis": "row"}),
}

_CALIB_BASE = 0      # 标定集 A 种子起点（§2：[0,9]）
_EVAL_BASE = 1000    # 评估集 B 种子起点（§2：[1000,1000+N)）


def _fmt(v: float) -> str:
    """幅度档 → 确定性文件名片段（0.10→'0.1'，−0.05→'-0.05'）。"""
    return f"{v:g}"


def build_scenarios(base_raw: dict) -> list[tuple[str, str, dict]]:
    """返回 [(scenario_id, seed_set, domain_dict)]。seed_set ∈ {'A','B'}。"""
    out: list[tuple[str, str, dict]] = []

    # S0 主 demo：基准现状（保留全部常驻注入器 + 第 3 轮强边缘）
    s0 = copy.deepcopy(base_raw)
    s0["name"] = "crystal_S0_demo"
    out.append(("S0.demo", "B", s0))

    # S1 零伪影对照（关注 QC 税）：清空 artifact_scenario，noise_sd 保留
    s1 = copy.deepcopy(base_raw)
    s1["name"] = "crystal_S1_zero"
    s1.setdefault("simulator", {})["artifact_scenario"] = []
    out.append(("S1.zero", "B", s1))

    # S2 单伪影 × 幅度网格；偶数档→A，奇数档→B（§4.3 A/B 幅度分家）
    for injector, (param, values, fixed) in _GRID.items():
        for idx, v in enumerate(values):
            seed_set = "A" if idx % 2 == 0 else "B"
            sid = f"S2.{injector}.{_fmt(v)}"
            dom = copy.deepcopy(base_raw)
            dom["name"] = f"crystal_{injector}_{_fmt(v)}".replace("-", "m").replace(".", "_")
            params = dict(fixed)
            params[param] = v
            dom.setdefault("simulator", {})["artifact_scenario"] = [
                {"injector": injector, "params": params}
            ]
            out.append((sid, seed_set, dom))
    return out


def _seeds_for(seed_set: str, calib_seeds: int, eval_seeds: int) -> list[int]:
    if seed_set == "A":
        return list(range(_CALIB_BASE, _CALIB_BASE + calib_seeds))
    return list(range(_EVAL_BASE, _EVAL_BASE + eval_seeds))


def write_sweep(
    base_yaml: str | Path,
    out_dir: str | Path,
    arms: list[str],
    calib_seeds: int,
    eval_seeds: int,
) -> dict[str, object]:
    base_yaml = Path(base_yaml)
    base_raw = yaml.safe_load(base_yaml.read_text(encoding="utf-8"))
    out_dir = Path(out_dir)
    scen_dir = out_dir / "scenarios"
    scen_dir.mkdir(parents=True, exist_ok=True)

    scenarios = build_scenarios(base_raw)

    # 落场景 yaml（确定性命名）并逐一 load_domain 校验（拼错的注入器名加载期就炸）
    scen_paths: dict[str, Path] = {}
    for sid, _set, dom in scenarios:
        p = scen_dir / f"{sid}.yaml"
        p.write_text(
            yaml.safe_dump(dom, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        load_domain(p)  # 校验；失败 → DomainError 响亮上抛
        scen_paths[sid] = p

    # cells.tsv：(scenario, arm, seed) 笛卡尔积 + seed_set 分离
    rows: list[str] = ["\t".join(["scenario_id", "arm", "seed", "seed_set", "domain_yaml"])]
    n_cells = 0
    for sid, seed_set, _dom in scenarios:
        seeds = _seeds_for(seed_set, calib_seeds, eval_seeds)
        rel = scen_paths[sid].relative_to(out_dir).as_posix()
        for arm in arms:
            for sd in seeds:
                rows.append("\t".join([sid, arm, str(sd), seed_set, rel]))
                n_cells += 1
    cells_tsv = out_dir / "cells.tsv"
    cells_tsv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    # sweep.sbatch：数组作业模板；一 task 读 cells.tsv 一行调 run_cell CLI
    sbatch = out_dir / "sweep.sbatch"
    sbatch.write_text(_SBATCH_TEMPLATE.format(n_cells=n_cells, last=n_cells), encoding="utf-8")

    return {
        "n_scenarios": len(scenarios),
        "n_cells": n_cells,
        "arms": arms,
        "scenarios_dir": str(scen_dir),
        "cells_tsv": str(cells_tsv),
        "sbatch": str(sbatch),
    }


# 分区/时限留占位注释；本机 Slurm 在 /opt/slurm/bin（不在默认 PATH）。
_SBATCH_TEMPLATE = """\
#!/bin/bash
# M9 扫描数组作业（docs/M9_PROTOCOL.md §5）。生成物；分区/时限按集群填占位注释。
#SBATCH --job-name=m9grid
#SBATCH --array=1-{last}%200          # cells.tsv 共 {n_cells} 行（含表头，下方 +1 跳过）；%N 限并发
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
##SBATCH --partition=<FILL_ME>        # TODO: 按 `sinfo` 填分区
##SBATCH --time=00:15:00              # TODO: 单 run 8 轮 CPU 约 1–3 min，留冗余
#SBATCH --output=_slurm/%A_%a.out
set -euo pipefail

export PATH=/opt/slurm/bin:$PATH       # 本机 Slurm 不在默认 PATH
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
mkdir -p "$HERE/_slurm"

# cells.tsv 第 1 行是表头；数组索引 i → 数据行 i+1（确定性防重跑：run_cell 命名幂等）
LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$HERE/cells.tsv")
IFS=$'\\t' read -r SCEN ARM SEED SEED_SET DOMAIN_REL <<< "$LINE"

cd "$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || echo "$HERE")"
python3 -m expos.eval.run_cell \\
    --domain   "$HERE/$DOMAIN_REL" \\
    --arm      "$ARM" \\
    --scenario "$SCEN" \\
    --seed     "$SEED" \\
    --rounds   8 \\
    --out-root "$HERE/runs"
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M9 扫描脚手架生成器")
    ap.add_argument("--base", default=str(ROOT / "domains" / "crystal.yaml"),
                    help="基准域 yaml（默认 domains/crystal.yaml）")
    ap.add_argument("--out", required=True, help="脚手架输出目录（scenarios/cells.tsv/sweep.sbatch）")
    ap.add_argument("--arms", default="naive,os",
                    help="逗号分隔臂（默认 naive,os；robust 未接线不入表）")
    ap.add_argument("--calib-seeds", type=int, default=10, help="标定集 A 种子数（§2 [0,9]）")
    ap.add_argument("--eval-seeds", type=int, default=20, help="评估集 B 种子数（§2 N=20）")
    args = ap.parse_args(argv)

    info = write_sweep(
        args.base, args.out, [a.strip() for a in args.arms.split(",") if a.strip()],
        args.calib_seeds, args.eval_seeds,
    )
    print(
        f"[gen_sweep] {info['n_scenarios']} 场景 × 臂{info['arms']} → {info['n_cells']} 格\n"
        f"  scenarios: {info['scenarios_dir']}\n"
        f"  cells.tsv: {info['cells_tsv']}\n"
        f"  sbatch:    {info['sbatch']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
