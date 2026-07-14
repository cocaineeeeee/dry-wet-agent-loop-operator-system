"""M9 三臂对比编排与主图（docs/M9_PROTOCOL.md §1 三臂定义 / §6 主图配方）。

`compare` 逐臂逐种子调 `run_cell`（幂等——重跑不重算已完成 campaign），把 naive /
robust-blind / os 三臂的评分（expos.eval.scoring.score_run 的 score.json）聚合成一张
对比台账 + 一张期刊级主图：

    out_root/compare_report/
      compare_summary.json   # 每臂 mean±std：末轮 regret、wrong_optimum_hit 率、
                             #   contaminated_ratio（旧口径）、training_contamination
                             #   （新口径：分母=该臂实际入模原始观测集合，R1-3(b)）、
                             #   n_suspect 率（os 才非零）、QC 税（零伪影场景）
      compare.png            # 上：三臂 best-true-so-far 按种子平均 ±std band（§6/§12.6 CLSLab 配方）
                             #   下：三臂污染样本利用率按轮曲线（§3.3 三臂对比核心）

真值来源：主图上半的 best-true-so-far 序列取自各 run 的 score.json `rounds[*].best_true_so_far`
——评估器是 truth sidecar 的**唯一合法读者**（scoring 模块公理 6 豁免），compare 只读它的产物。

依赖方向（expos.eval 是叶子）：本模块 import run_cell / scoring（同包）+ matplotlib，
无任何跑内内核 import 本模块。
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 无显示环境：必须在 pyplot 之前锁定后端

# 无 CJK 字体时中文字形缺失只是渲染降级（PNG 仍有效），静音以保持输出干净
warnings.filterwarnings("ignore", message="Glyph .* missing from font")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from expos.domain import load_domain  # noqa: E402
from expos.errors import ExposError  # noqa: E402
from expos.eval.run_cell import run_cell  # noqa: E402
from expos.eval.scoring import EvalError  # noqa: E402

# ---------------------------------------------------------------- 臂语义与配色

# 色盲友好三色（Okabe–Ito 子集）——图例注明臂语义
_ARM_COLOR = {
    "naive": "#E69F00",         # 橙：全信基线（稻草人）
    "robust": "#009E73",        # 绿：信任盲 + 副本中位数
    "robust-blind": "#009E73",
    "os": "#0072B2",            # 蓝：三级 QC + 信任路由
}
_ARM_SEMANTIC = {
    "naive": "naive（全信基线）",
    "robust": "robust-blind（信任盲·副本中位数）",
    "robust-blind": "robust-blind（信任盲·副本中位数）",
    "os": "os（三级 QC·信任路由）",
}

_PNG_METADATA = {"Software": None}  # 去掉 mpl 版本串，保证跨环境字节确定性


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 120,
            "axes.spines.top": False,   # 无顶右边框（期刊级）
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.6,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.unicode_minus": False,
            "font.sans-serif": [
                "Noto Sans CJK SC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
                "Source Han Sans SC", "SimHei", "Droid Sans Fallback", "DejaVu Sans",
            ],
        }
    )


# ---------------------------------------------------------------- 聚合工具

def _arm_color(arm: str) -> str:
    return _ARM_COLOR.get(arm, "#555555")


def _arm_semantic(arm: str) -> str:
    return _ARM_SEMANTIC.get(arm, arm)


def _round_matrix(
    by_seed: dict[int, dict], seeds: list[int], key: str
) -> tuple[list[int], np.ndarray]:
    """把某臂各种子的逐轮 `key` 值对齐成 [n_seeds × n_rounds] 矩阵（None→nan）。"""
    all_rounds = sorted(
        {r["round"] for s in by_seed.values() for r in s.get("rounds", [])}
    )
    mat = np.full((len(seeds), len(all_rounds)), np.nan)
    for i, seed in enumerate(seeds):
        rows = {r["round"]: r for r in by_seed[seed].get("rounds", [])}
        for j, rid in enumerate(all_rounds):
            v = rows.get(rid, {}).get(key)
            if v is not None:
                mat[i, j] = float(v)
    return all_rounds, mat


def _mean_std(values: list[float]) -> dict[str, Any]:
    arr = np.array([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return {"mean": None, "std": None, "n": 0, "values": list(values)}
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "n": int(arr.size),
        "values": [None if v is None else float(v) for v in values],
    }


def _suspect_failed_rate(summary: dict) -> float:
    """该 run 全轮 (SUSPECT+FAILED)/总观测 —— n_suspect 率 / QC 税基元。"""
    ns = nf = nt = 0
    for r in summary.get("rounds", []):
        ns += int(r.get("n_suspect", 0) or 0)
        nf += int(r.get("n_failed", 0) or 0)
        nt += int(r.get("n_trusted", 0) or 0)
    total = ns + nf + nt
    return (ns + nf) / total if total else 0.0


def _n_suspect_rate(summary: dict) -> float:
    ns = nt = nf = 0
    for r in summary.get("rounds", []):
        ns += int(r.get("n_suspect", 0) or 0)
        nf += int(r.get("n_failed", 0) or 0)
        nt += int(r.get("n_trusted", 0) or 0)
    total = ns + nf + nt
    return ns / total if total else 0.0


def _aggregate(
    per_arm: dict[str, dict[int, dict]], arms: tuple[str, ...] | list[str],
    seeds: list[int], scenario_id: str, rounds: int, zero_artifact: bool,
) -> dict[str, Any]:
    """§3 指标按臂聚合（mean±std）。零伪影场景附 QC 税块（§3.4 仅 S1 有意义）。

    ``zero_artifact`` 由**场景配置**判定（domain yaml 的 ``artifact_scenario`` 为空），
    不再按经验污染值判定——R1 修复：旧版要求所有臂所有种子所有轮
    ``contaminated_in_training`` 恒 0，而 |bias|>3σ 的纯噪声本底 ≈0.3%/观测，
    协议 N≥20 种子下全零概率趋 0 → qc_tax 块实践不可达（死代码）。
    """

    arms_out: dict[str, Any] = {}
    for arm in arms:
        by_seed = per_arm[arm]
        final_regret = [by_seed[s].get("final_regret") for s in seeds]
        contam = [
            by_seed[s].get("contaminated_in_training",
                           (by_seed[s].get("rounds") or [{}])[-1].get(
                               "contaminated_in_training"))
            for s in seeds
        ]
        # 新口径（R1-3(b)，scoring 双列）：分母=该臂实际入模原始观测集合；
        # 旧 score.json 无此键时回退旧口径值（两口径在 naive/robust/os 本就同集合）
        train_contam = [
            by_seed[s].get("training_contamination",
                           by_seed[s].get("contaminated_in_training"))
            for s in seeds
        ]
        wrong_hits = [1.0 if by_seed[s].get("wrong_optimum_hit_any") else 0.0
                      for s in seeds]
        n_suspect = [_n_suspect_rate(by_seed[s]) for s in seeds]
        arms_out[arm] = {
            "semantic": _arm_semantic(arm),
            "final_regret": _mean_std(final_regret),
            "contaminated_ratio": _mean_std(contam),          # 旧口径（兼容保留）
            "training_contamination": _mean_std(train_contam),  # 新口径（R1-3(b)）
            "wrong_optimum_hit_rate": float(np.mean(wrong_hits)) if seeds else None,
            "n_suspect_rate": _mean_std(n_suspect),  # naive/robust 恒 0，仅 os 非零
        }

    out: dict[str, Any] = {
        "scenario_id": scenario_id,
        "seeds": list(seeds),
        "rounds": rounds,
        "arms_order": list(arms),
        "zero_artifact_scenario": zero_artifact,
        "arms": arms_out,
    }

    # QC 税（§3.4，仅零伪影场景有意义）：os 非 TRUSTED 率 + regret 差 os−naive
    if zero_artifact and "os" in per_arm:
        os_sf = _mean_std([_suspect_failed_rate(per_arm["os"][s]) for s in seeds])
        regret_delta = None
        if "naive" in per_arm:
            os_r = arms_out["os"]["final_regret"]["mean"]
            nv_r = arms_out["naive"]["final_regret"]["mean"]
            if os_r is not None and nv_r is not None:
                regret_delta = float(os_r - nv_r)
        out["qc_tax"] = {
            "os_suspect_failed_rate": os_sf,   # 被 os 判非 TRUSTED 的（好）观测占比
            "regret_delta_os_minus_naive": regret_delta,
        }
    return out


# ---------------------------------------------------------------- 主图（§6 / §12.6）

def _plot_compare(
    per_arm: dict[str, dict[int, dict]], arms: tuple[str, ...] | list[str],
    seeds: list[int], scenario_id: str, out_path: Path, direction: str,
) -> None:
    _apply_style()
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(7.4, 7.0), height_ratios=[3, 2], constrained_layout=True
    )
    arrow = "↑ 越大越好" if direction != "minimize" else "↓ 越小越好"

    # ---- 上：三臂 best-true-so-far 按种子平均 ±1std band（CLSLab 配方）----
    for arm in arms:
        rounds_axis, mat = _round_matrix(per_arm[arm], seeds, "best_true_so_far")
        if not rounds_axis:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            mean = np.nanmean(mat, axis=0)
            std = np.nanstd(mat, axis=0)
        color = _arm_color(arm)
        x = np.array(rounds_axis, dtype=float)
        ok = ~np.isnan(mean)
        ax_top.plot(x[ok], mean[ok], "-o", color=color, lw=2.0, ms=5, zorder=3,
                    label=_arm_semantic(arm))
        band = np.where(np.isnan(std), 0.0, std)
        ax_top.fill_between(x[ok], (mean - band)[ok], (mean + band)[ok],
                            color=color, alpha=0.18, lw=0, zorder=1)
    ax_top.set_title(f"三臂 best-true-so-far 收敛（{scenario_id}，按种子平均 ±1std）")
    ax_top.set_xlabel("轮次 round")
    ax_top.set_ylabel(f"去伪影真值最优\n（{arrow}）")
    _int_xticks(ax_top, per_arm, arms, seeds)
    ax_top.legend(frameon=False, fontsize=9, loc="best", title="臂（裁决×聚合策略）")

    # ---- 下：三臂污染样本利用率按轮曲线（§3.3）----
    for arm in arms:
        rounds_axis, mat = _round_matrix(per_arm[arm], seeds,
                                         "contaminated_in_training")
        if not rounds_axis:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            mean = np.nanmean(mat, axis=0)
            std = np.nanstd(mat, axis=0)
        color = _arm_color(arm)
        x = np.array(rounds_axis, dtype=float)
        ok = ~np.isnan(mean)
        ax_bot.plot(x[ok], mean[ok], "-s", color=color, lw=2.0, ms=4, zorder=3,
                    label=_arm_semantic(arm))
        band = np.where(np.isnan(std), 0.0, std)
        ax_bot.fill_between(x[ok], (mean - band)[ok], (mean + band)[ok],
                            color=color, alpha=0.15, lw=0, zorder=1)
    ax_bot.set_title("三臂污染样本利用率（训练集内真污染观测占比，越低越好）")
    ax_bot.set_xlabel("轮次 round")
    ax_bot.set_ylabel("污染利用率")
    _int_xticks(ax_bot, per_arm, arms, seeds)
    ax_bot.legend(frameon=False, fontsize=8, loc="best")

    fig.savefig(out_path, metadata=_PNG_METADATA)
    plt.close(fig)


def _int_xticks(ax, per_arm, arms, seeds) -> None:
    all_rounds = sorted({
        r["round"] for arm in arms for s in seeds
        for r in per_arm[arm][s].get("rounds", [])
    })
    if all_rounds:
        ax.set_xticks(all_rounds)


# ---------------------------------------------------------------- 编排入口

def compare(
    domain_yaml, scenario_id: str, seeds: list[int], rounds: int,
    out_root, arms=("naive", "robust", "os"),
) -> dict:
    """逐臂逐种子调 run_cell（幂等），聚合产出：
    out_root/compare_report/{compare_summary.json, compare.png}

    - summary：每臂 mean±std 的末轮 regret、wrong_optimum_hit 率、
      contaminated_ratio、n_suspect 率（os 才有）、QC 税（若 scenario 零伪影）；
    - compare.png：上图=三臂 best-true-so-far 按种子平均 ±std band（真值来自
      score.json 的 best_true_so_far 序列——evaluation 是 truth 合法读者）；
      下图=三臂污染样本利用率按轮曲线。期刊级：无顶右边框、中文标签、
      constrained_layout、色盲友好三色、图例注明臂语义。

    单臂/单种子失败 → 响亮 EvalError，消息带臂标识（Slurm/批量里定位）。
    """
    arms = tuple(arms)
    seeds = [int(s) for s in seeds]
    out_root = Path(out_root)

    per_arm: dict[str, dict[int, dict]] = {}
    direction = "maximize"
    for arm in arms:
        per_arm[arm] = {}
        for seed in seeds:
            try:
                summary = run_cell(
                    domain_yaml, arm=arm, scenario_id=scenario_id,
                    seed=seed, rounds=rounds, out_root=out_root,
                )
            except EvalError as e:
                raise EvalError(f"[compare arm={arm} seed={seed}] {e}") from e
            except ExposError as e:
                raise EvalError(
                    f"[compare arm={arm} seed={seed}] {type(e).__name__}: {e}"
                ) from e
            per_arm[arm][seed] = summary
            direction = summary.get("direction", direction)

    report_dir = out_root / "compare_report"
    report_dir.mkdir(parents=True, exist_ok=True)

    # QC 税门（§3.4）由**场景配置**判定，不再按经验污染值（R1 死代码修复）：
    # domain yaml 的 simulator.artifact_scenario 为空 ⇒ 零伪影场景 S1，QC 税才有意义。
    # 此处 domain_yaml 已被上方 arm 循环成功加载过（坏 yaml 早已响亮 EvalError），
    # 故此刻 load_domain 必成功；放在循环后避免抢先于「带臂标识」错误路径。
    zero_artifact = not load_domain(domain_yaml).simulator.get("artifact_scenario")

    agg = _aggregate(per_arm, arms, seeds, scenario_id, rounds, zero_artifact)
    (report_dir / "compare_summary.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _plot_compare(per_arm, arms, seeds, scenario_id,
                  report_dir / "compare.png", direction)

    agg["compare_report"] = str(report_dir)
    return agg


# ---------------------------------------------------------------- CLI

def _parse_seeds(text: str) -> list[int]:
    return [int(x) for x in text.replace(",", " ").split()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="M9 三臂对比编排与主图（naive/robust/os）",
        prog="python3 -m expos.eval.compare",
    )
    ap.add_argument("--domain", required=True, help="域 yaml 路径（或 scenarios/<id>.yaml）")
    ap.add_argument("--scenario", required=True, help="scenario_id（§4.1 场景族标识，如 S0.demo）")
    ap.add_argument("--seeds", required=True, help="逗号分隔种子，如 1,2,3")
    ap.add_argument("--rounds", type=int, default=8, help="轮数（协议 §2 统一 8 轮）")
    ap.add_argument("--out-root", required=True,
                    help="run 根目录；各格子=out-root/<scenario>__<arm>__s<seed>，"
                         "对比产物=out-root/compare_report/")
    ap.add_argument("--arms", default="naive,robust,os",
                    help="逗号分隔臂列表（默认 naive,robust,os）")
    args = ap.parse_args(argv)

    arms = tuple(x for x in args.arms.replace(",", " ").split())
    try:
        agg = compare(
            args.domain, scenario_id=args.scenario, seeds=_parse_seeds(args.seeds),
            rounds=args.rounds, out_root=args.out_root, arms=arms,
        )
    except ExposError as e:
        if not e.user_facing:
            raise  # 内部不变量破坏=bug，保留响亮 traceback
        print(f"[compare error] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
