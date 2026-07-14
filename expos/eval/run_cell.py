"""M9 评测格子运行器（docs/M9_PROTOCOL.md §5）—— 单 (arm, scenario, seed) 三元组。

一个"格子"= 对某场景变体 yaml、某臂、某种子跑一次完整 campaign 并落 §4 产物：
    run_loop（跑内闭环，禁触 truth）→ score_run（事后评分，truth 唯一合法读者）
    → write_trajectory（§4.1 逐轮 JSONL）。

设计要点（对抗审查 / §18.1 族2 Slurm 确定性命名防重跑）：
- **确定性 run 目录名** `f"{scenario_id}__{arm}__s{seed}"`：同三元组恒同目录，
  多节点各写独立子目录（§13.1 坑：多 task 禁写同一 run 根）。
- **幂等**：run 目录已存在且 checkpoint 完成（completed_rounds ≥ rounds）→ **跳过 campaign**、
  直接补评分（score/trajectory 幂等覆盖写）；checkpoint 存在但未完成 → resume 续跑。
- **arm→mode 映射**：naive/robust/os 三臂均已在 loop._policies_for_mode 接线
  （robust = NaivePolicy×MedianAggregation×BaselinePlanner，信任盲对照）。
- **错误边界**：任何跑内/评分 ExposError 转 EvalError 并**带格子标识**（Slurm 万级作业里定位）。

依赖方向不变（expos.eval 是叶子）：本模块 import loop（跑内）+ 同包 scoring/trajectory，
无任何跑内内核 import 本模块。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from expos.errors import ExposError
from expos.eval.scoring import EvalError, score_run
from expos.eval.trajectory import write_trajectory
from expos.loop import derive_seed, run_loop

# 已在 loop._policies_for_mode 接线的臂 → run_loop 的 mode（零 if arm== 分支的对齐点）。
# M13 消融矩阵：os-lite（容量对齐）+ 三个 os-minus-* 机制消融臂——臂名即 mode（恒等映射）。
_ARM_TO_MODE = {
    "naive": "naive", "os": "os", "os-soft": "os-soft", "rcgp": "rcgp",
    "os-lite": "os-lite",
    "os-minus-riskmap": "os-minus-riskmap",
    "os-minus-arbiter": "os-minus-arbiter",
    "os-minus-attribution": "os-minus-attribution",
}
_ROBUST_ARMS = {"robust", "robust-blind"}

def cell_id(scenario_id: str, arm: str, seed: int) -> str:
    """确定性格子标识 = run 目录名（防重跑主键）。"""
    return f"{scenario_id}__{arm}__s{seed}"


def _mode_for_arm(arm: str) -> str:
    if arm in _ROBUST_ARMS:
        return "robust"  # 已接线：NaivePolicy×MedianAggregation×BaselinePlanner（loop._policies_for_mode）
    if arm not in _ARM_TO_MODE:
        raise EvalError(
            f"未知臂: {arm!r}（可用: {sorted(_ARM_TO_MODE) + sorted(_ROBUST_ARMS)}）"
        )
    return _ARM_TO_MODE[arm]


def _seed_triplet(seed: int, scenario_id: str) -> dict[str, int]:
    """§4.3 种子元数据（措辞修正——R2 ①③实锤）：

    - 执行真源是 ``derive_seed(seed, "exec", round_id)``（loop 传给 adapter 的 rng，
      噪声与伪影共用一条流）；跨臂同 base seed ⇒ **共同随机数近似**配对——各臂布局
      不同（os 有风险避让），伪影落点随孔位变化，"同伪影实现"的强声称不成立，
      配对置换检验的有效性只依赖"按 seed 配对"本身。
    - ``artifact`` 键是**孤儿派生值**（无任何执行路径消费；R2 审查实锤），保留仅为
      历史 trajectory 兼容，标注 orphan 防止再被引用为"独立伪影种子流"的证据。"""
    return {
        "np": int(seed),
        "exec_round0": derive_seed(seed, "exec", 0),  # 执行流真源（首轮代表值）
        "artifact_orphan": derive_seed(seed, "artifact", scenario_id),
        "layout": derive_seed(seed, "layout", 0),
    }


def _is_complete(run_dir: Path, rounds: int) -> bool:
    """run 目录已存在且 checkpoint 记载 completed_rounds ≥ rounds ⇒ 已跑完（可跳过）。"""
    ckpt_path = run_dir / "checkpoint.json"
    if not ckpt_path.exists():
        return False
    try:
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return int(ckpt.get("completed_rounds", 0)) >= rounds


def run_cell(
    domain_yaml: str | Path,
    arm: str,
    scenario_id: str,
    seed: int,
    rounds: int,
    out_root: str | Path,
) -> dict[str, Any]:
    """跑一个评测格子，返回 score summary（附格子元数据）。

    幂等：run 目录已存在且 checkpoint 完成 → 跳过 campaign，仅补评分；未完成 → resume 续跑。
    返回 dict 追加 `cell_id / arm / scenario_id / seed / run_dir / skipped`（skipped=True 表示
    campaign 未重算——重跑不重算的判据）。
    """
    cid = cell_id(scenario_id, arm, seed)
    run_dir = Path(out_root) / cid
    mode = _mode_for_arm(arm)
    try:
        skipped = _is_complete(run_dir, rounds)
        if not skipped:
            resume = (run_dir / "checkpoint.json").exists()
            run_loop(
                domain_yaml, mode=mode, rounds=rounds, seed=seed,
                out_dir=run_dir, resume=resume,
            )
        # 补评分（幂等覆盖写 score.json / trajectory.jsonl）——跳过时也补，缺产物则现算
        summary = score_run(run_dir, domain_yaml)
        write_trajectory(
            run_dir, domain_yaml, arm=arm, scenario_id=scenario_id,
            seeds=_seed_triplet(seed, scenario_id),
        )
    except ExposError as e:
        raise EvalError(f"[cell {cid}] {type(e).__name__}: {e}") from e

    summary = dict(summary)
    summary.update(
        cell_id=cid, arm=arm, scenario_id=scenario_id, seed=int(seed),
        run_dir=str(run_dir), skipped=skipped,
    )
    return summary


# ---------------------------------------------------------------- CLI（Slurm 数组一 task 一行）

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="M9 评测格子运行器（单 arm×scenario×seed）",
        prog="python3 -m expos.eval.run_cell",
    )
    ap.add_argument("--domain", required=True, help="场景变体域 yaml 路径（scenarios/<id>.yaml）")
    ap.add_argument("--arm", required=True, help="naive | robust | os")
    ap.add_argument("--scenario", required=True, help="scenario_id（§4.1 场景族.注入器.幅度档）")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=8, help="轮数（协议 §2 统一 8 轮）")
    ap.add_argument("--out-root", required=True,
                    help="run 根目录；实际 run 目录 = out-root/<scenario>__<arm>__s<seed>")
    args = ap.parse_args(argv)

    try:
        summary = run_cell(
            args.domain, arm=args.arm, scenario_id=args.scenario,
            seed=args.seed, rounds=args.rounds, out_root=args.out_root,
        )
    except ExposError as e:
        if not e.user_facing:
            raise  # 内部不变量破坏=bug，保留响亮 traceback
        print(f"[run_cell error] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
