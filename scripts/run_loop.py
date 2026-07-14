#!/usr/bin/env python3
"""闭环 CLI（docs/ARCHITECTURE.md §11）。

M4：naive 基线。示例：
    python scripts/run_loop.py --domain crystal --mode naive --rounds 4 --seed 7 \\
        --out runs/m4_naive
断点续跑加 --resume（同目录同 domain/mode/seed）。

M9：--mode compare 转发到 expos.eval.compare（三臂 naive/robust/os 编排 + 主图）。
    --seed 为单值时扩展为 [seed, seed+1, seed+2]（对比图需多种子求 ±std band）；
    --out 作为 out_root（各格子 = out/<scenario>__<arm>__s<seed>，对比产物 = out/compare_report/）；
    scenario_id 取 --scenario（默认 S0.demo，仅 compare 模式使用）。示例：
    python scripts/run_loop.py --domain crystal --mode compare --rounds 6 --seed 1 \\
        --scenario S0.demo --out runs/m9_compare
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from expos.errors import ExposError  # noqa: E402
from expos.loop import run_loop  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="expos 闭环运行器")
    ap.add_argument("--domain", required=True, help="域名（domains/<name>.yaml）或 YAML 路径")
    ap.add_argument("--mode", default="naive",
                    choices=["naive", "robust", "rcgp", "os", "os-soft",
                             "os-lite", "os-minus-riskmap", "os-minus-arbiter",
                             "os-minus-attribution", "compare"],
                    help="naive=全信 / robust=信任盲+副本中位数 / rcgp=信任盲+模型层稳健 / "
                         "os=三级 QC+信任路由 / os-soft=os+QUARANTINE 软信任降权复归 / "
                         "os-lite=os 全栈×rcgp 同容量档模型（容量对齐消融）/ "
                         "os-minus-riskmap=os 全栈但无风险图 / "
                         "os-minus-arbiter=os 全栈但动作仲裁空转 / "
                         "os-minus-attribution=os 全栈但不做归因 / "
                         "compare=M9 三臂编排+主图")
    ap.add_argument("--rounds", type=int, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True, help="运行目录（runs/<name>）；compare 模式下作为 out_root")
    ap.add_argument("--resume", action="store_true", help="从 checkpoint.json 续跑")
    ap.add_argument("--scenario", default="S0.demo",
                    help="scenario_id（仅 --mode compare 使用，默认 S0.demo）")
    args = ap.parse_args()

    domain_path = Path(args.domain)
    if not domain_path.exists():
        domain_path = ROOT / "domains" / f"{args.domain}.yaml"

    if args.mode == "compare":
        # --seed 单值扩展为 [seed, seed+1, seed+2]，供 ±std band 求平均
        from expos.eval.compare import compare  # noqa: PLC0415
        seeds = [args.seed, args.seed + 1, args.seed + 2]
        try:
            summary = compare(
                domain_path, scenario_id=args.scenario, seeds=seeds,
                rounds=args.rounds, out_root=args.out,
            )
        except ExposError as e:
            if not e.user_facing:
                raise
            print(f"[compare error] {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    try:
        summary = run_loop(
            domain_path, mode=args.mode, rounds=args.rounds,
            seed=args.seed, out_dir=args.out, resume=args.resume,
        )
    except ExposError as e:
        if not e.user_facing:
            raise  # 内部不变量破坏=bug，不许静默（保留响亮 traceback）
        print(f"[loop error] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
