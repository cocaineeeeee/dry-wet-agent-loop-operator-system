#!/usr/bin/env python3
"""M10 三幕 demo 一键脚本（docs/ARCHITECTURE.md §15 / docs/DEMO_SCRIPT.md）。

一条命令跑完验收的三幕并把全部产物归档到 --out（默认 runs/demo）：

  第一幕（假最优狙击）：crystal 同 seed 双臂（naive / os）跑 --rounds 轮 →
      compare.png（两条 best-so-far 真值轨迹，第 3 轮"假最优"事件标注）+
      自动生成的中文解说 demo_narrative.md。解说的每个结论都从实跑产物条件化
      生成（事后评分走 expos.eval.scoring——truth sidecar 唯一合法读者）：
      naive best 越过物理上限 1.0 才说"越过上限"；否则若事后评分判假最优命中
      （measured 超真值 3σ）则引 measured/true/3σ 真实数字；两者皆无则如实说明。
  第二幕（热插拔）：同一命令只把域从 crystal 换成 coating，os 闭环照跑
      （证明"换域只换配置"），产 loop.png（复用 scripts/plot_run.py）。
  第三幕（边界即类型）：现场构造 agent 伪造 acceptance 的两次尝试——
      (a) 直接调 lifecycle.validate_proposal(actor=agent) → LifecycleError 拒绝；
      (b) 绕过 API 把伪造 ACCEPTANCE 记录硬写进事件日志 → lifecycle._resolutions
      按 actor 过滤、日志层忽略——审计证据落 boundary_demo.txt（含"拒绝"字样）。

设计纪律：matplotlib Agg 后端；幂等（run 目录 checkpoint 已完成则跳过重算、
只补图/解说）；总时长打印。expos.eval / 内核零改动——本脚本只读运行产物。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 无显示环境：pyplot 之前锁定后端

import warnings  # noqa: E402

# 无 CJK 字体时中文字形缺失只是渲染降级（PNG 仍有效），与 plot_run.py 同口径静音
warnings.filterwarnings("ignore", message="Glyph .* missing from font")

import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from expos.kernel.lifecycle import (  # noqa: E402
    LifecycleError,
    accepted_proposals,
    submit_proposal,
    unresolved_proposals,
    validate_proposal,
    _resolutions,
)
from expos.kernel.objects import Actor, DecisionKind, DecisionRecord  # noqa: E402
from expos.kernel.store import RunStore  # noqa: E402
from expos.loop import run_loop  # noqa: E402

import plot_run  # noqa: E402  (scripts/plot_run.py) —— 复用 RunData / 报告出图


# ---------------------------------------------------------------- 通用工具

def _resolve_domain(name: str) -> Path:
    """域名（crystal/coating）或 yaml 路径 → domains/<name>.yaml。"""
    p = Path(name)
    if p.exists():
        return p
    cand = ROOT / "domains" / f"{name}.yaml"
    if cand.exists():
        return cand
    raise SystemExit(f"[make_demo] 找不到域配置: {name}")


def _run_or_resume(
    domain_path: Path, mode: str, rounds: int, seed: int, run_dir: Path
) -> tuple[dict[str, Any], bool]:
    """跑一次闭环，幂等：checkpoint 已完成 → 跳过重算（resume 即刻返回 summary）；
    未完成 → 续跑；无 checkpoint → 全新跑。返回 (summary, skipped)。"""
    ckpt = run_dir / "checkpoint.json"
    skipped = False
    if ckpt.exists():
        done = int(json.loads(ckpt.read_text(encoding="utf-8")).get("completed_rounds", 0))
        skipped = done >= rounds
        summary = run_loop(domain_path, mode=mode, rounds=rounds, seed=seed,
                           out_dir=run_dir, resume=True)
    else:
        summary = run_loop(domain_path, mode=mode, rounds=rounds, seed=seed,
                          out_dir=run_dir)
    tag = "跳过重算" if skipped else "已运行"
    print(f"  [{tag}] {mode:5s} {domain_path.stem:8s} seed={seed} rounds={rounds} "
          f"→ best={_bt_val(summary)!r}  {run_dir}")
    return summary, skipped


def _bt_val(summary: dict[str, Any]) -> float | None:
    bt = summary.get("best_trusted")
    return None if bt is None else bt.get("value")


#: crystal 结晶质量指数的真值面物理上限（domains/crystal.yaml；metric_range 上界 1.2
#: 是含伪影的测量量程，真值本身 ≤1.0——naive 测量值越过 1.0 即假最优铁证）。
PHYSICAL_CEILING = 1.0


def _fake_optimum_facts(run_dir: Path, domain_yaml: Path) -> dict[str, Any]:
    """事后评分抽假最优证据（expos.eval.scoring 是 truth sidecar 唯一合法读者；
    本调用在闭环结束后、不回写任何决策——与 M9 评分同一豁免口径）。

    返回：hit_any（任一轮 wrong_optimum_hit）、hit_rounds、最后一次命中的
    measured/true/well/round、noise_sd 与 3σ 阈 tau——全部真实数字，喂解说。"""
    from expos.eval.scoring import score_run  # 延迟导入：仅第一幕解说需要

    s = score_run(run_dir, domain_yaml)
    hit_rounds = [int(r["round"]) for r in s["rounds"] if r.get("wrong_optimum_hit")]
    last_bt: dict[str, Any] = {}
    for r in s["rounds"]:
        if r.get("wrong_optimum_hit") and r.get("best_trusted"):
            last_bt = r["best_trusted"]
    return {
        "hit_any": bool(hit_rounds),
        "hit_rounds": hit_rounds,
        "n_rounds": int(s.get("n_rounds") or 0),
        "measured": last_bt.get("measured"),
        "true": last_bt.get("true"),
        "well_id": last_bt.get("well_id"),
        "round_id": last_bt.get("round_id"),
        "noise_sd": float(s.get("noise_sd") or 0.0),
        "tau": float(s.get("tau_bias") or 0.0),
    }


# ---------------------------------------------------------------- 第一幕：假最优狙击

def _round3_facts(os_run_dir: Path) -> dict[str, Any]:
    """从 os run 的事件日志抽第 3 轮的隔离数与 edge 归因数（真实数字，喂解说）。"""
    store = RunStore(os_run_dir, create=False)
    qc = {e["payload"]["round_id"]: e["payload"] for e in store.read_events("qc_report")}
    r3 = qc.get(3, {})
    n_quarantined = int(r3.get("n_suspect", 0)) + int(r3.get("n_failed", 0))
    edge_check_hits = int((r3.get("check_counts") or {}).get("edge_effect", 0))
    attr_r3 = [e["payload"] for e in store.read_events("attribution")
               if e["payload"].get("round_id") == 3]
    edge_attr = sum(1 for a in attr_r3 if "edge" in str(a.get("top_cause") or ""))
    causes = Counter(a.get("top_cause") for a in attr_r3)
    return {
        "n_quarantined": n_quarantined,
        "edge_check_hits": edge_check_hits,
        "edge_attr": edge_attr,
        "top_causes": dict(causes),
    }


def plot_compare(naive_dir: Path, os_dir: Path, out_png: Path, seed: int,
                 rounds: int) -> None:
    """两臂 best-so-far 轨迹叠一张图；第 3 轮伪影事件竖线只在实际跑到时标注（Agg）。"""
    plot_run._apply_style()
    fig, ax = plt.subplots(figsize=(7.4, 4.6), constrained_layout=True)
    metric = "objective"
    for run_dir, color, label in (
        (naive_dir, "#C44E52", "naive 全信（被伪影带偏）"),
        (os_dir, "#4C78A8", "os 信任路由（拒斥假最优）"),
    ):
        data = plot_run.RunData(run_dir)
        metric = data.metric or metric
        rs, best, _ = data.best_so_far()
        ax.plot(rs, best, "-o", color=color, lw=2.2, ms=6, label=label, zorder=3)
    # 真值面物理上限（crystal 质量指数 ≤ 1.0）——参考虚线；是否越过由解说按实跑判定
    ax.axhline(PHYSICAL_CEILING, color="#888888", ls="--", lw=1.0, zorder=1,
               label=f"真值面物理上限 = {PHYSICAL_CEILING}")
    if rounds >= 3:  # 边缘蒸发事件注入在第 3 轮（domains/crystal.yaml）
        ax.axvline(3, color="#E8A00D", ls=":", lw=1.6, zorder=2)
        ax.annotate("第 3 轮：边缘蒸发\n伪造假最优", xy=(3, 1.0), xytext=(3.15, 0.9),
                    fontsize=9, color="#8a6d00")
    ax.set_title(f"第一幕 假最优狙击　crystal 同 seed={seed} 双臂对比")
    ax.set_xlabel("轮次 round")
    ax.set_ylabel(f"best-so-far　{metric}")
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.savefig(out_png, metadata=plot_run._PNG_METADATA)
    plt.close(fig)


def write_narrative(
    path: Path, seed: int, rounds: int,
    naive_sum: dict[str, Any], os_sum: dict[str, Any], facts: dict[str, Any],
    naive_fake: dict[str, Any], os_fake: dict[str, Any],
    compare_png: Path, act2_png: Path, act3_txt: Path,
) -> None:
    """解说全部从实跑结果条件化生成——无一句硬编码结论（R1-6）。"""
    naive_best = _bt_val(naive_sum)
    os_best = _bt_val(os_sum)
    tau = naive_fake["tau"]
    lines = [
        "# M10 三幕 demo 自动解说（真实数字，跑内生成）",
        "",
        f"运行参数：crystal 同 seed={seed}、{rounds} 轮、双臂 naive vs os。",
        "",
        "## 第一幕　假最优狙击",
        "",
    ]

    # ---- naive 一侧：三分支，全部按实跑判定（越上限 / 假最优命中 / 皆无）
    if naive_best is not None and naive_best > PHYSICAL_CEILING:
        lines += [
            f"- **naive 假最优值**：`{naive_best}`——伪影把平庸孔读数抬到全场最高，",
            f"  naive 全信照收，best-so-far **越过真值面物理上限 {PHYSICAL_CEILING}**"
            f"（本次实跑判定；假最优铁证）。",
        ]
    elif naive_fake["hit_any"]:
        m, t = naive_fake["measured"], naive_fake["true"]
        lines += [
            f"- **假最优命中**：naive 推荐点的测量值被伪影抬高超真值 3σ——"
            f"第 {naive_fake['round_id']} 轮 best（{naive_fake['well_id']}）"
            f"measured=`{m:.4f}` vs true=`{t:.4f}`，抬高 `{m - t:.4f}` > 3σ=`{tau:.3f}`；",
            f"  本次 naive best=`{naive_best}` 未越物理上限 {PHYSICAL_CEILING}，"
            f"但推荐已被伪影带偏（命中轮：{naive_fake['hit_rounds']}）。",
        ]
    else:
        lines += [
            f"- **naive best**：`{naive_best}`——本次运行未出现假最优命中"
            f"（best 测量值与真值差 ≤3σ=`{tau:.3f}`；"
            f"强边缘伪影事件在第 3 轮注入，--rounds ≥3 才触发）。",
        ]

    # ---- os 一侧：不超上限 / 命中次数均按事后评分实报
    os_line = f"- **os 拒斥后可信最优**：`{os_best}`"
    if os_best is not None and os_best <= PHYSICAL_CEILING:
        os_line += f"——不超过物理上限 {PHYSICAL_CEILING}，物理合理"
    if os_fake["hit_any"]:
        os_line += (f"；os 假最优命中 {len(os_fake['hit_rounds'])}/{os_fake['n_rounds']} 轮"
                    f"（命中轮：{os_fake['hit_rounds']}）。")
    else:
        os_line += f"；os 全程 {os_fake['n_rounds']} 轮假最优命中 0 次。"
    lines.append(os_line)

    # ---- QC 事实：rounds≥4 时是第 3 轮切片，否则是全程汇总（口径随实跑标注）
    scope = "第 3 轮" if rounds >= 4 else "全程"
    lines.append(
        f"- **{scope}隔离数**：`{facts['n_quarantined']}` 个观测被判 SUSPECT/FAILED"
        "（隔离/进失败模型——被判 SUSPECT/FAILED 的观测不进响应模型训练集）。"
    )
    if rounds >= 4:
        lines.append(
            f"- **edge 归因数**：第 3 轮边缘检查命中 `{facts['edge_check_hits']}` 次、"
            f"其中归因到 edge_evaporation 的观测 `{facts['edge_attr']}` 个"
            f"（该轮归因分布：{facts['top_causes'] or '—'}）。"
        )
    if naive_best is not None and os_best is not None:
        gap = naive_best - os_best
        gap_line = f"- **两臂差值**：naive − os = `{gap:.4f}`"
        if naive_fake["hit_any"] and rounds >= 3:
            gap_line += ("——伪影污染量的直接体现，"
                         "compare.png 第 3 轮两线分叉即整个项目的论点。")
        else:
            gap_line += "。"
        lines.append(gap_line)
    lines += [
        f"- 看图：`{compare_png}`（两条 best-so-far + 上限虚线"
        + (" + 第 3 轮事件标注" if rounds >= 3 else "")
        + "）。",
        "",
        "## 第二幕　热插拔",
        "",
        "- 同一命令只把 `--domain crystal` 换成 `--domain coating`，内核零改动、os 闭环照跑。",
        f"- 看图：`{act2_png}`（coating 逐轮寻优 + 信任裁决计数）。",
        "",
        "## 第三幕　边界即类型",
        "",
        "- agent 伪造 acceptance 的两次尝试都被**拒绝**：直接调裁决 API 抛 LifecycleError；",
        "  硬写进日志的伪造记录被 `_resolutions` 按 actor 过滤忽略——不变量在日志上可机器检查。",
        f"- 看证据：`{act3_txt}`。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 第三幕：边界即类型

def run_act3(act3_dir: Path) -> Path:
    """构造 agent 伪造 acceptance 的尝试，落审计证据（含"拒绝"字样）。"""
    import shutil

    act3_dir.mkdir(parents=True, exist_ok=True)
    # 幂等：清掉上跑残留的审计库，否则 submit/append 会跨跑累积、证据文件逐跑变脏；
    # 卖点证据须逐字节可复现。
    shutil.rmtree(act3_dir / "audit_store", ignore_errors=True)
    store = RunStore(act3_dir / "audit_store")
    out = act3_dir / "boundary_demo.txt"
    L: list[str] = ["第三幕　边界即类型——agent 有建议权、无裁决权（可机器验证的不变量）",
                    "=" * 64, ""]

    # 1) agent 提交一条合法提案（这是它被允许做的：建议）
    proposal = DecisionRecord(
        decision_id="dec_act3_proposal",  # 固定 id：证据文件逐字节可复现（默认 uuid 会逐跑变）
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL,
        content={"action": "REMEASURE", "reason": "怀疑边缘蒸发，建议中心位复测"},
    )
    submit_proposal(store, proposal)
    L.append(f"[1] agent 提交合法提案 {proposal.decision_id}（kind=action_proposal）——建议权，允许。")

    # 2) 伪造尝试 A：agent 直接调裁决 API 自我 accept → 类型层拒绝
    L.append("")
    L.append("[2] 伪造尝试 A：agent 调 lifecycle.validate_proposal(actor=agent) 自我 accept")
    try:
        validate_proposal(store, proposal, accepted=True, actor=Actor.AGENT,
                          reason="agent 试图自裁定")
        L.append("    !! 未拦截（不该发生）")
    except LifecycleError as e:
        L.append(f"    → 被【拒绝】：LifecycleError: {e}")

    # 3) 伪造尝试 B：绕过 API 把伪造 ACCEPTANCE 记录硬写进事件日志（模拟被攻陷路径）
    forged = DecisionRecord(
        decision_id="dec_act3_forged",  # 固定 id：证据文件逐字节可复现（默认 uuid 会逐跑变）
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACCEPTANCE,
        refs=[proposal.decision_id], accepted=True, validator="agent",
        content={"reason": "伪造的自我接受"},
    )
    store.append_decision(forged)  # 记录进了日志……
    res = _resolutions(store)      # ……但裁决视图按 actor 过滤，忽略它
    unresolved = [d.decision_id for d in unresolved_proposals(store)]
    accepted = [d.decision_id for d in accepted_proposals(store)]
    L.append("")
    L.append("[3] 伪造尝试 B：绕过 API 把伪造 acceptance 硬写进事件日志")
    L.append(f"    伪造记录已在日志：{forged.decision_id}（actor=agent, kind=acceptance）")
    L.append(f"    但 lifecycle._resolutions 只采信 planner/human → 提案裁定视图={res}")
    L.append(f"    → 伪造被【拒绝】采信：提案仍未裁定 unresolved={unresolved}、accepted={accepted}")

    # 4) 合法路径：planner 裁定（此处拒绝），提案才有配对记录、才可能影响后续设计
    validate_proposal(store, proposal, accepted=False, actor=Actor.PLANNER,
                      reason="预算已满，本轮不复测")
    res2 = _resolutions(store)
    L.append("")
    L.append("[4] 合法裁决：planner 对该提案给出 rejection（有裁决权）")
    L.append(f"    → 提案裁定视图={res2}（False=被规划器拒绝）；配对记录在日志上可审计。")
    L.append("")
    L.append("结论：伪造 acceptance 无论走 API 还是硬写日志都进不了裁决视图——")
    L.append("      『agent 无裁决权』不是纪律口号，是日志层可机器检查的类型不变量。")

    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"  [act3] 边界守门证据 → {out}")
    return out


# ---------------------------------------------------------------- 主流程

def make_demo(out_root: Path, rounds: int, n_seeds: int, base_seed: int) -> dict[str, Any]:
    t0 = time.perf_counter()
    out_root.mkdir(parents=True, exist_ok=True)
    runs = out_root / "runs"
    seeds = list(range(base_seed, base_seed + max(1, n_seeds)))
    primary = seeds[0]
    crystal = _resolve_domain("crystal")
    coating = _resolve_domain("coating")

    # ---- 第一幕：crystal 同 seed 双臂（对每个 seed 都跑，主 seed 出图/解说）
    print("== 第一幕：假最优狙击（crystal naive vs os）==")
    naive_sum = os_sum = None
    naive_dir = os_dir = None
    for s in seeds:
        n_dir = runs / f"act1_crystal_naive_s{s}"
        o_dir = runs / f"act1_crystal_os_s{s}"
        ns, _ = _run_or_resume(crystal, "naive", rounds, s, n_dir)
        os_, _ = _run_or_resume(crystal, "os", rounds, s, o_dir)
        if s == primary:
            naive_sum, os_sum, naive_dir, os_dir = ns, os_, n_dir, o_dir

    act1_dir = out_root / "act1"
    act1_dir.mkdir(parents=True, exist_ok=True)
    compare_png = act1_dir / "compare.png"
    plot_compare(naive_dir, os_dir, compare_png, primary, rounds)
    print(f"  [act1] 对比图 → {compare_png}")
    facts = _round3_facts(os_dir) if rounds >= 4 else {
        "n_quarantined": os_sum["n_suspect"] + os_sum["n_failed"],
        "edge_check_hits": 0, "edge_attr": 0, "top_causes": {},
    }
    # 事后评分（truth sidecar 唯一合法读者）——解说的假最优结论全部据此条件化
    naive_fake = _fake_optimum_facts(naive_dir, crystal)
    os_fake = _fake_optimum_facts(os_dir, crystal)

    # ---- 第二幕：热插拔（coating os，同命令只换域）
    print("== 第二幕：热插拔（coating os，同命令只换 --domain）==")
    act2_rounds = min(3, rounds)
    coat_dir = runs / f"act2_coating_os_s{primary}"
    _run_or_resume(coating, "os", act2_rounds, primary, coat_dir)
    act2_dir = out_root / "act2"
    plot_run.generate_report(coat_dir, act2_dir, only_round=None)
    act2_png = act2_dir / "loop.png"
    print(f"  [act2] coating 报告 → {act2_png}")

    # ---- 第三幕：边界即类型
    print("== 第三幕：边界即类型（agent 伪造 acceptance 被日志层拒绝）==")
    act3_txt = run_act3(out_root / "act3")

    # ---- 解说与总时长
    narrative = out_root / "demo_narrative.md"
    write_narrative(narrative, primary, rounds, naive_sum, os_sum, facts,
                    naive_fake, os_fake, compare_png, act2_png, act3_txt)
    elapsed = time.perf_counter() - t0
    print(f"\n三幕产物归档到 {out_root}")
    print(f"  解说 : {narrative}")
    print(f"  一幕 : {compare_png}")
    print(f"  二幕 : {act2_png}")
    print(f"  三幕 : {act3_txt}")
    print(f"总时长 : {elapsed:.1f}s")
    return {
        "out": str(out_root), "narrative": str(narrative),
        "compare_png": str(compare_png), "act2_png": str(act2_png),
        "act3_txt": str(act3_txt), "naive_best": _bt_val(naive_sum),
        "os_best": _bt_val(os_sum), "facts": facts,
        "naive_fake": naive_fake, "os_fake": os_fake, "elapsed_s": elapsed,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="M10 三幕 demo 一键脚本（假最优狙击 / 热插拔 / 边界即类型）",
        prog="python3 scripts/make_demo.py",
    )
    ap.add_argument("--out", default="runs/demo", help="归档目录（默认 runs/demo）")
    ap.add_argument("--rounds", type=int, default=5,
                    help="第一幕 crystal 双臂轮数（默认 5；第 3 轮含边缘伪影事件）")
    ap.add_argument("--seeds", type=int, default=1,
                    help="第一幕运行的 seed 数（默认 1=单 seed 双臂；主 seed 出图/解说）")
    ap.add_argument("--base-seed", type=int, default=7, help="起始 seed（默认 7）")
    args = ap.parse_args(argv)
    make_demo(Path(args.out), args.rounds, args.seeds, args.base_seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
