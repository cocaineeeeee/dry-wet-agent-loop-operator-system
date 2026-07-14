#!/usr/bin/env python3
"""运行可视化报告脚本（docs/REFERENCE_MAP.md §13.7 / §12.6）。

读一个运行目录（RunStore 布局，见 expos/kernel/store.py），产出期刊级报告：

  python3 scripts/plot_run.py <run_dir> [--out report/] [--round K]

产物（默认写进 <run_dir>/report/，或 --out 指定目录）：
  1. loop.png        —— 上：逐轮 best-so-far（TRUSTED 非对照，按 objective.direction
                        取 max/min accumulate）折线 + 每轮观测散点（浅色）；
                        下：每轮 TRUSTED/SUSPECT/FAILED 计数堆叠条。
  2. plate_round<k>.png —— 该轮 6×8 值热图；SUSPECT/FAILED 孔红/黄描边、哨兵孔星标。
                        默认画所有轮；--round K 只画第 K 轮。
  3. summary.txt     —— 轮数 / 观测数 / 最优值与孔位 / 各 trust 计数。

设计纪律：matplotlib Agg 后端（无显示环境）、无多余边框、中文轴标签、
constrained_layout、确定性输出（观测按 (round, obs_id) 稳定排序，PNG 去除版本元数据）。
错误处理：目录不存在 / 空 run → 干净报错 exit 2（ExposError 语义）。
"""

from __future__ import annotations

import argparse
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无显示环境：必须在 pyplot 之前锁定后端

# 无 CJK 字体时中文字形缺失只是渲染降级（PNG 仍有效），静音以保持输出干净
warnings.filterwarnings("ignore", message="Glyph .* missing from font")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from expos.errors import ExposError  # noqa: E402
from expos.kernel.objects import TrustLevel  # noqa: E402
from expos.kernel.store import RunStore  # noqa: E402

# ---------------------------------------------------------------- 审美常量

_TRUST_ORDER = [TrustLevel.TRUSTED, TrustLevel.SUSPECT, TrustLevel.FAILED]
_TRUST_LABEL = {
    TrustLevel.TRUSTED: "可信 TRUSTED",
    TrustLevel.SUSPECT: "存疑 SUSPECT",
    TrustLevel.FAILED: "失败 FAILED",
}
_TRUST_COLOR = {
    TrustLevel.TRUSTED: "#4C78A8",
    TrustLevel.SUSPECT: "#E45756",
    TrustLevel.FAILED: "#F2B701",
}
# 孔描边配色：SUSPECT=红 / FAILED=黄（对应 §规格 "SUSPECT/FAILED 红/黄"）
_EDGE_COLOR = {TrustLevel.SUSPECT: "#D62728", TrustLevel.FAILED: "#E8A00D"}

_PNG_METADATA = {"Software": None}  # 去掉 mpl 版本串，保证跨环境字节确定性


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 120,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.6,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.unicode_minus": False,
            # 尽量挑一个含 CJK 的字体；缺失时退回默认（渲染可能缺字形但不报错）
            "font.sans-serif": [
                "Noto Sans CJK SC",
                "WenQuanYi Zen Hei",
                "WenQuanYi Micro Hei",
                "Source Han Sans SC",
                "SimHei",
                "Droid Sans Fallback",
                "DejaVu Sans",
            ],
        }
    )


# ---------------------------------------------------------------- 数据装载

class RunData:
    """从运行目录抽取绘图所需的最小结构（只读，不写盘）。"""

    def __init__(self, run_dir: Path):
        if not run_dir.is_dir():
            raise ExposError(f"运行目录不存在: {run_dir}")
        try:
            store = RunStore(run_dir, create=False)
        except FileNotFoundError as e:
            raise ExposError(str(e)) from e

        # 稳定排序：先按轮、再按 obs_id，保证确定性输出
        self.observations = sorted(
            store.list_observations(), key=lambda o: (o.round_id, o.obs_id)
        )
        if not self.observations:
            raise ExposError(f"空 run（无任何观测）: {run_dir}")

        self.experiments = store.list_experiments()
        self.rounds = sorted({o.round_id for o in self.observations})

        # objective.direction：优先取实验，退回 config.json
        self.direction = "maximize"
        self.metric = ""
        if self.experiments:
            obj = self.experiments[0].objective
            self.direction, self.metric = obj.direction, obj.metric
        else:
            cfg = store.read_config() or {}
            obj = (cfg.get("domain_config") or {}).get("objective") or {}
            self.direction = obj.get("direction", "maximize")
            self.metric = obj.get("metric", "")

        # 板型：优先实验 layout，退回 config.plate，兜底 6×8
        self.rows, self.cols = 6, 8
        for exp in self.experiments:
            if exp.layout is not None:
                self.rows, self.cols = exp.layout.rows, exp.layout.cols
                break
        else:
            cfg = store.read_config() or {}
            plate = (cfg.get("domain_config") or {}).get("plate") or {}
            self.rows = plate.get("rows", self.rows)
            self.cols = plate.get("cols", self.cols)

        # control_id -> kind（用于哨兵星标）
        self.control_kind: dict[str, str] = {}
        for exp in self.experiments:
            for c in exp.controls:
                self.control_kind[c.control_id] = c.kind

    # ---- 派生量 ----

    def _trusted_values(self):
        """(round, value) 列表：仅 TRUSTED 且非对照、且有数值的观测。"""
        out = []
        for o in self.observations:
            if o.trust != TrustLevel.TRUSTED or o.is_control:
                continue
            v = o.result.value
            if v is not None:
                out.append((o.round_id, float(v)))
        return out

    def best_so_far(self):
        """按 direction 对逐轮最优做 max/min accumulate；返回 (rounds, best, per_round_vals)。"""
        by_round: dict[int, list[float]] = defaultdict(list)
        for r, v in self._trusted_values():
            by_round[r].append(v)
        rounds = self.rounds
        maximize = self.direction != "minimize"
        agg = max if maximize else min
        # 每轮最优（无 TRUSTED 值的轮用 nan 占位，accumulate 时前向填充）
        per_round_best = np.array(
            [agg(by_round[r]) if by_round.get(r) else np.nan for r in rounds]
        )
        best = _dir_accumulate(per_round_best, maximize)
        return rounds, best, by_round

    def overall_best(self):
        """全局最优 TRUSTED 非对照观测：返回 (value, round_id, well_id) 或 None。"""
        maximize = self.direction != "minimize"
        best = None
        for o in self.observations:
            if o.trust != TrustLevel.TRUSTED or o.is_control:
                continue
            v = o.result.value
            if v is None:
                continue
            if best is None or (v > best[0] if maximize else v < best[0]):
                best = (float(v), o.round_id, o.layout_meta.well_id)
        return best

    def trust_counts(self):
        """{round: Counter(trust)}。"""
        out: dict[int, Counter] = {r: Counter() for r in self.rounds}
        for o in self.observations:
            out[o.round_id][o.trust] += 1
        return out


def _dir_accumulate(per_round_best: np.ndarray, maximize: bool) -> np.ndarray:
    """带 nan 前向填充的方向性 accumulate（§13.7 best-so-far 配方）。"""
    acc = np.full(per_round_best.shape, np.nan)
    running = None
    for i, v in enumerate(per_round_best):
        if not np.isnan(v):
            running = v if running is None else (max(running, v) if maximize else min(running, v))
        if running is not None:
            acc[i] = running
    return acc


# ---------------------------------------------------------------- 图 1：loop.png

def plot_loop(data: RunData, out_path: Path) -> None:
    rounds, best, by_round = data.best_so_far()
    counts = data.trust_counts()
    maximize = data.direction != "minimize"

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(7.2, 6.4), height_ratios=[3, 2], constrained_layout=True
    )

    # ---- 上：best-so-far 折线 + 每轮观测散点 ----
    for r in rounds:
        vals = by_round.get(r, [])
        if vals:
            ax_top.scatter(
                [r] * len(vals), vals, s=22, color="#4C78A8", alpha=0.28,
                edgecolors="none", zorder=2,
                label="每轮观测（TRUSTED）" if r == rounds[0] else None,
            )
    ax_top.plot(
        rounds, best, "-o", color="#C44E52", lw=2.0, ms=5, zorder=3,
        label="best-so-far（迄今最优）",
    )
    metric = data.metric or "objective"
    arrow = "↑ 越大越好" if maximize else "↓ 越小越好"
    ax_top.set_title(f"逐轮寻优轨迹　目标：{metric}（{arrow}）")
    ax_top.set_xlabel("轮次 round")
    ax_top.set_ylabel(f"目标值　{metric}")
    ax_top.set_xticks(rounds)
    ax_top.legend(frameon=False, fontsize=9, loc="best")

    # ---- 下：TRUSTED/SUSPECT/FAILED 计数堆叠条 ----
    bottom = np.zeros(len(rounds))
    x = np.arange(len(rounds))
    any_stack = False
    for tl in _TRUST_ORDER:
        heights = np.array([counts[r].get(tl, 0) for r in rounds], dtype=float)
        if heights.sum() == 0 and tl != TrustLevel.TRUSTED:
            continue  # 该 trust 全 0（如 M4 全 TRUSTED）时不占图例
        any_stack = True
        ax_bot.bar(
            x, heights, bottom=bottom, width=0.62,
            color=_TRUST_COLOR[tl], label=_TRUST_LABEL[tl], edgecolor="white", lw=0.5,
        )
        bottom += heights
    ax_bot.set_title("每轮信任裁决计数")
    ax_bot.set_xlabel("轮次 round")
    ax_bot.set_ylabel("观测数")
    ax_bot.set_xticks(x)
    ax_bot.set_xticklabels([str(r) for r in rounds])
    ax_bot.grid(axis="x", visible=False)
    if any_stack:
        ax_bot.legend(frameon=False, fontsize=9, loc="upper right", ncol=3)

    fig.savefig(out_path, metadata=_PNG_METADATA)
    plt.close(fig)


# ---------------------------------------------------------------- 图 2：plate_round<k>.png

def plot_plate(data: RunData, round_id: int, out_path: Path) -> None:
    rows, cols = data.rows, data.cols
    grid = np.full((rows, cols), np.nan)
    obs_at = {}  # (row, col) -> obs
    for o in data.observations:
        if o.round_id != round_id:
            continue
        rr, cc = o.layout_meta.row, o.layout_meta.col
        if 0 <= rr < rows and 0 <= cc < cols:
            grid[rr, cc] = np.nan if o.result.value is None else float(o.result.value)
            obs_at[(rr, cc)] = o

    fig, ax = plt.subplots(figsize=(1.05 * cols + 1.6, 1.05 * rows + 1.0),
                           constrained_layout=True)
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("#E9E9E9")  # 未使用孔：浅灰
    im = ax.imshow(grid, cmap=cmap, aspect="equal", origin="upper")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(f"目标值　{data.metric or 'objective'}")

    # 轴：A..（行）× 1..（列），期刊级简洁刻度
    ax.set_xticks(range(cols))
    ax.set_xticklabels([str(c + 1) for c in range(cols)])
    ax.set_yticks(range(rows))
    ax.set_yticklabels([chr(ord("A") + r) for r in range(rows)])
    ax.set_xlabel("列 col")
    ax.set_ylabel("行 row")
    ax.set_title(f"第 {round_id} 轮孔板值热图（{rows}×{cols}）")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)

    # 描边（SUSPECT 红 / FAILED 黄）与哨兵星标
    used_edge, used_star = set(), False
    for (rr, cc), o in obs_at.items():
        if o.trust in _EDGE_COLOR:
            ax.add_patch(Rectangle(
                (cc - 0.5, rr - 0.5), 1, 1, fill=False,
                edgecolor=_EDGE_COLOR[o.trust], lw=2.4, zorder=4,
            ))
            used_edge.add(o.trust)
        is_sentinel = o.control_id is not None and (
            data.control_kind.get(o.control_id, "sentinel") == "sentinel"
        )
        if is_sentinel:
            ax.plot(cc, rr, marker="*", color="white", ms=11, mec="black",
                    mew=0.6, zorder=5)
            used_star = True
        # 值标注（浅色小字，深底用白字）
        v = grid[rr, cc]
        if not np.isnan(v):
            ax.text(cc, rr + 0.32, f"{v:.2f}", ha="center", va="center",
                    fontsize=6.5, color="white", alpha=0.85, zorder=6)

    # 图例（仅出现的元素）
    handles = []
    for tl in (TrustLevel.SUSPECT, TrustLevel.FAILED):
        if tl in used_edge:
            handles.append(plt.Line2D([], [], marker="s", mfc="none",
                                      mec=_EDGE_COLOR[tl], mew=2.2, ls="none",
                                      ms=11, label=f"{_TRUST_LABEL[tl]} 孔"))
    if used_star:
        handles.append(plt.Line2D([], [], marker="*", color="white", mec="black",
                                  mew=0.6, ls="none", ms=12, label="哨兵孔 sentinel"))
    if handles:
        ax.legend(handles=handles, frameon=False, fontsize=8,
                  loc="upper left", bbox_to_anchor=(1.18, 1.0))

    fig.savefig(out_path, metadata=_PNG_METADATA)
    plt.close(fig)


# ---------------------------------------------------------------- summary.txt

def write_summary(data: RunData, out_path: Path) -> None:
    total = len(data.observations)
    tc: Counter = Counter(o.trust for o in data.observations)
    best = data.overall_best()

    lines = []
    lines.append("expos 运行报告摘要")
    lines.append("=" * 40)
    lines.append(f"轮数        : {len(data.rounds)}  (round {data.rounds[0]}..{data.rounds[-1]})")
    lines.append(f"观测总数    : {total}")
    lines.append(f"优化方向    : {data.direction}  目标={data.metric or 'objective'}")
    if best is not None:
        v, r, well = best
        lines.append(f"最优值      : {v:.6g}  @ 第 {r} 轮 孔位 {well}  (TRUSTED 非对照)")
    else:
        lines.append("最优值      : （无 TRUSTED 非对照观测）")
    lines.append("")
    lines.append("信任裁决计数（全 run）:")
    for tl in _TRUST_ORDER + [TrustLevel.PENDING]:
        n = tc.get(tl, 0)
        if n or tl in _TRUST_ORDER:
            lines.append(f"  {tl.value:<8}: {n}")
    lines.append("")
    lines.append("逐轮 trust 计数:")
    per = data.trust_counts()
    for r in data.rounds:
        parts = " ".join(f"{tl.value}={per[r].get(tl, 0)}" for tl in _TRUST_ORDER)
        lines.append(f"  round {r}: {parts}  (合计 {sum(per[r].values())})")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- CLI

def generate_report(run_dir: Path, out_dir: Path, only_round: int | None) -> list[Path]:
    _apply_style()
    data = RunData(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    produced: list[Path] = []

    loop_png = out_dir / "loop.png"
    plot_loop(data, loop_png)
    produced.append(loop_png)

    if only_round is not None:
        if only_round not in data.rounds:
            raise ExposError(
                f"--round {only_round} 不在该 run 的轮次内: {data.rounds}"
            )
        target_rounds = [only_round]
    else:
        target_rounds = data.rounds
    for r in target_rounds:
        p = out_dir / f"plate_round{r}.png"
        plot_plate(data, r, p)
        produced.append(p)

    summary = out_dir / "summary.txt"
    write_summary(data, summary)
    produced.append(summary)
    return produced


def main() -> int:
    ap = argparse.ArgumentParser(description="expos 运行可视化报告")
    ap.add_argument("run_dir", help="运行目录（RunStore 布局）")
    ap.add_argument("--out", default=None,
                    help="报告输出目录（默认 <run_dir>/report/）")
    ap.add_argument("--round", type=int, default=None,
                    help="只画指定轮次的孔板图；不给则画所有轮")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    out_dir = Path(args.out) if args.out else run_dir / "report"

    try:
        produced = generate_report(run_dir, out_dir, args.round)
    except ExposError as e:
        if not e.user_facing:
            raise  # 内部不变量破坏=bug，不许静默
        print(f"[plot error] {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    for p in produced:
        print(str(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
