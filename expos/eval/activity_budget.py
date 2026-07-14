"""失活预算熔断（sweep 级机制活性守门；O3-D 交接建议 2；FB3 重构）。

**语义（红队 FB3 收编，去除拍定与过度声称）**：本工具是 **sweep 级事后门**，不是在线
kill-switch。它对"应激活场景×机制"的合法 run 集合做**去抖判据**：给定去抖长度 ``k``，
*保证抓住任何 ≥k 轮的连续失活段、容忍 ≤k−1 轮的合法静默*。判据形态 = **连续-k 游程**
（consecutive-k：连续 k 轮非 active 即熔断）——已证与原 k-in-w 滑窗在 ``period=intensity``
时逐位等价（10⁵ 次随机序列零失配，见 test_budget_consecutive_equiv）。

**为什么换形（红队 F3 实证）**：原 (intensity=3,period=5) k-in-w 在**每个** should-activate
档位对合法格红牌命中 100%（180/180 合法格误红）——因为"应激活场景里 warning 是异常"这个
前提经验上不成立：即便最高结构伪影档，单轮合法 warning 概率 p_w 仍达 0.58–0.82，且 active
轮从不连续（P(warning|active)=1）。唯一有判别力的统计量是**长连续 warning 游程**（死机制=全
warning 游程=R；活机制被孤立 active 打断）。原 3/5 参数"借自 VS Code CrashTracker 3 次/5 分钟"
是拍定，已被否决。

**参数不拍定——从 F3 重放数据反解**（见 ``derive_k``）：给定轮数 R、should-activate 格数 N、
族误报目标 α=0.05，用 ``q_target = 1−(1−α)^(1/N)`` 反解满足族误报的最小 k*（2 态马尔可夫解析
DP，a=max P(w|w)）。R=8 现值：**k*=7（保守，a_max=0.28）/ 经验 k=6**（纯净集族误报=0）。
{族误报≤5%, 检出延迟≤3} 在此信号上**联合不可行**（合法 3 连 warning 极常见）——故不再承诺
任何检出延迟上界；死机制的检出发生在第 k 轮（游程走满），仅此。

**scope（红队 F3 标定）**：``risk_map`` 是**空间**避让机制——只有高信号空间边缘伪影族、
且合法静默罕见（标定 P(w|w)<~0.40）的档位入 should-activate（见 ``expected_active``）；
``batch_shift``/低中档的静默是**正确行为**不该判红。准入定量门槛：a_max≥0.45 时 R=8 无可行 k，
显式排除。``soft_trust_reweight`` 的 p_w 未标定（F3 数据是 risk_map 重放），暂不入 should-activate
集（须单独标定 a_max 后套同式）。

本工具消费 loop.py 已发射的 ``risk_map_applied`` / ``aggregation_alpha`` 事件的 ``grade``
三态（active/warning/absent，见 EVENT_SCHEMA §1/§6）——**只读派生事实、不重算 grade**。
纯函数 ``budget_breached`` 用合成 grade 序列（已知答案）测试，与磁盘解耦。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# 机制注册名 → 其活性观测面事件 kind（EVENT_SCHEMA §6 迁移映射的先行版两机制）
MECHANISM_EVENT: dict[str, str] = {
    "risk_map": "risk_map_applied",
    "soft_trust_reweight": "aggregation_alpha",
}


# ============================================================ scope：(场景族×机制) 准入
#
# 红队 FB3：mode→{机制} 一刀切错配——同一 mode 下，某档位的静默可能是**正确行为**。
# 准入下沉到 (场景族 × 机制)：从 F3 重放（runs/r1_resweep，os 臂逐轮 n_distinct）标定各族
# 各机制的单轮合法失活率与 2 态马尔可夫 P(w|w)，只有"机制-场景语义匹配 ∧ 合法静默罕见
# （P(w|w)<~0.40）∧ R=8 有可行 k（a_max<0.45）"的档位入 should-activate。


@dataclass(frozen=True)
class _Admit:
    """一条 (场景族×机制) 准入规则。``a_max`` = 标定的 max P(w|w)（驱动 k* 反解）；
    ``min_strength`` = 该族入准的最低伪影强度（低于此则信号不足、静默合法）。"""

    a_max: float
    min_strength: float
    note: str


# risk_map 是**空间**避让机制。F3 标定（os 臂，warning<=>n_distinct<=1）：
#   edge_evaporation .35/.20 → P(w|w)=0.28/0.48;  .05/.10/.15 低档 → 0.93–1.0（信号不足）
#   edge_gradient_batch(S4) → 0.35;  edge_glare(S4) → 0.31 但有合法 7 轮静默尾（须 k>=8）
#   wide_edge .40 → 0.60;  batch_shift .10/.18 → 0.98/0.75（**非空间**：静默=正确行为）
# 入准（保守 k*=7，a_max=0.28）：edge_evaporation 高信号档（strength>=0.2）。其余留待逐族
# k>=8 单独标定（同 soft_trust_reweight，见下）。
_SHOULD_ACTIVATE: dict[tuple[str, str], _Admit] = {
    ("edge_evaporation", "risk_map"): _Admit(
        a_max=0.28, min_strength=0.2,
        note="高信号空间边缘蒸发（strength>=0.2；P(w|w)=0.28）"),
}

# 显式非准入（记录判据来由，供审计；不用于逻辑，逻辑靠 _SHOULD_ACTIVATE 白名单）：
#   ("batch_shift", "risk_map"): 非空间机制，risk_map 静默是正确行为
#   ("wide_edge", "risk_map"):   a=0.60 >= 0.45，R=8 无可行 k
#   ("edge_glare"/"edge_gradient_batch", "risk_map"): a=0.31/0.35，须逐族 k>=8 单独标定
#   (*, "soft_trust_reweight"): p_w 未标定（F3 是 risk_map 重放），须单独标定 a_max 后套同式


def _scenario_family(scenario: str | None) -> tuple[str | None, float | None]:
    """从 group_scenario id（如 ``S2.edge_evaporation.0.2`` / ``S4.edge_glare``）解族名与强度。
    形如 ``S<n>.<family>[.<strength>]``；无强度档（S4 复合）返回 strength=None。"""
    if not scenario:
        return None, None
    parts = scenario.split(".")
    if len(parts) < 2:
        return None, None
    family = parts[1]
    strength: float | None = None
    if len(parts) >= 3:
        try:
            strength = float(".".join(parts[2:]))
        except ValueError:
            strength = None
    return family, strength


# 各 mode 下**候选**机制集（该臂物理上启用的机制）；实际 should-activate 还须过 (族×机制) 门。
_MODE_MECHANISMS: dict[str, set[str]] = {
    "os": {"risk_map"},
    "os-soft": {"risk_map", "soft_trust_reweight"},
}


def expected_active(mode: str | None, scenario: str | None = None) -> set[str]:
    """该 run 应激活的机制集 = (arm 候选机制) ∩ (在本 scenario 族入准的机制)。

    红队 FB3 修正：**scenario 未知时返回空集**（无法判定 should-activate，保守不判红，杜绝
    对静默合法档位的误红）——原 mode→{机制} 一刀切正是误红根源。"""
    family, strength = _scenario_family(scenario)
    if family is None:
        return set()
    candidates = _MODE_MECHANISMS.get(mode or "", set())
    out: set[str] = set()
    for mech in candidates:
        rule = _SHOULD_ACTIVATE.get((family, mech))
        if rule is None:
            continue
        if strength is not None and strength < rule.min_strength:
            continue
        out.add(mech)
    return out


# ============================================================ 参数反解：k 不拍定
#
# 2 态马尔可夫（a=P(w|w), b=P(w|a)）解析 DP：连续-k 游程在 R 轮内出现（=族误报单格概率）。
# 独立性经验强烈失败（lag1ρ 低至 −0.68），故不用 iid，用马尔可夫；解析 DP 对 MC 10⁵ 误差 <0.002。


def pfp_consec_markov(a: float, b: float, k: int, R: int) -> float:
    """R 轮 2 态马尔可夫序列里出现连续 k 个 warning（失活）的概率（=单格族误报 q）。
    a=P(w|w), b=P(w|a)；初始按平稳分布 π_w=b/(1−a+b) 起。"""
    pi_w = b / (1 - a + b)

    @lru_cache(maxsize=None)
    def surv(last: str, c: int, t: int) -> float:
        if c >= k:
            return 0.0
        if t == 0:
            return 1.0
        pw = a if last == "W" else b
        tot = 0.0
        if c + 1 < k:
            tot += pw * surv("W", c + 1, t - 1)
        tot += (1 - pw) * surv("A", 0, t - 1)
        return tot

    s = pi_w * surv("W", 1, R - 1) + (1 - pi_w) * surv("A", 0, R - 1)
    surv.cache_clear()
    return 1 - s


def family_q_target(alpha: float, n: int) -> float:
    """Šidák 反解：为使 N 格独立联合族误报 ≤ α，单格误报须 ≤ 1−(1−α)^(1/N)。"""
    return 1 - (1 - alpha) ** (1 / n)


def derive_k(
    rounds: int, n_cells: int, alpha: float, a_max: float,
    b: float = 1.0, kmax: int | None = None,
) -> tuple[int | None, float]:
    """反解满足族误报 ≤ ``alpha`` 的最小去抖长度 k*（返回 ``(k*, q_target)``）。

    ``a_max`` = should-activate 集里最坏档位的 P(w|w)。b=1.0（死机制=全 warning，保守）。
    无可行 k（如 a_max>=0.45 @ R=8）→ 返回 ``(None, q_target)``。换战役自动重算，禁拍定。"""
    q_target = family_q_target(alpha, n_cells)
    kmax = kmax or rounds
    for k in range(1, kmax + 1):
        if pfp_consec_markov(a_max, b, k, rounds) <= q_target:
            return k, q_target
    return None, q_target


# 参考标定（R=8 现役 sweep；os 臂 edge_evaporation should-activate 集）：
# a_max=0.28（最高信号空间档 ee0.35）、N=40（SET A 纯净集）、α=0.05 → k*=7（保守）。
# 经验 k=6 已使 SET A 族误报=0（见 test_budget_family_fpr_zero_on_f3_legit）。
REF_ROUNDS = 8
REF_N_CELLS = 40
FAMILY_ALPHA = 0.05
REF_A_MAX = 0.28
DEFAULT_K, _REF_Q_TARGET = derive_k(REF_ROUNDS, REF_N_CELLS, FAMILY_ALPHA, REF_A_MAX)
assert DEFAULT_K == 7, f"参考标定应反解 k*=7，实得 {DEFAULT_K}"


@dataclass(frozen=True)
class ActivityBudget:
    """失活预算：连续 ``k`` 轮非 active（去抖长度 k）即熔断。默认 k 由 ``derive_k`` 从
    R=8/N=40/α=0.05/a_max=0.28 反解（=7，保守）——非拍定。"""

    k: int = DEFAULT_K


DEFAULT_BUDGET = ActivityBudget()

_INACTIVE = {"warning", "absent"}


def budget_breached(
    grades: list[str], budget: ActivityBudget = DEFAULT_BUDGET
) -> dict[str, Any] | None:
    """连续-k 游程去抖判据：按轮序遍历 grade 序列，一旦出现**连续 ``budget.k`` 轮**非 active
    即熔断，返回该游程走满轮的信息（走满轮 index + 游程首轮 index + 游程内轮 index 列表）；
    无越界→None。任一合法 active 轮把游程清零（容忍 ≤k−1 轮合法静默）。

    grade ∈ {active, warning, absent}；warning/absent 均计非 active（absent 是更硬的失活）。"""
    run = 0
    for i, g in enumerate(grades):
        run = run + 1 if g in _INACTIVE else 0
        if run >= budget.k:
            start = i - budget.k + 1
            return {
                "breached": True,
                "at_round_index": i,
                "k": budget.k,
                "inactive_run_start": start,
                "inactive_round_indices": list(range(start, i + 1)),
            }
    return None


_VALID_GRADES = {"active", "warning", "absent"}


def grade_stream(
    events: list[dict[str, Any]], kind: str,
    violations: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Extract the grade sequence for one event kind (sorted by round_id).

    REF-1 P1-2 semantics split (the R4-H F3 root cause): a *missing* grade key is
    the legal old format and conservatively counts as ``absent`` (no evidence ==
    inactive, never a pass). A *present but invalid* grade value (e.g. a typo'd
    "actve") is corrupt new format: previously it silently folded into inactive,
    making a truly-active mechanism read as dead with zero errors. Now it is
    recorded into ``violations`` (collected, never raised) and still counted as
    ``absent`` for the budget -- corrupt telemetry must not certify activity."""
    rows = []
    for i, ev in enumerate(events):
        if ev.get("kind") != kind:
            continue
        payload = ev.get("payload", {}) or {}
        g = payload.get("grade", "absent")
        if g not in _VALID_GRADES:
            if violations is not None:
                violations.append({
                    "seq": ev.get("seq"), "kind": kind,
                    "problem": "invalid_grade_value", "value": repr(g),
                })
            g = "absent"
        rows.append((payload.get("round_id", i), g))
    rows.sort(key=lambda r: r[0])
    return [g for _, g in rows]


def scan_run(
    run_dir: Path,
    expect_active: set[str] | None = None,
    budget: ActivityBudget = DEFAULT_BUDGET,
) -> list[dict[str, Any]]:
    """扫一个 run 目录：对每个应激活机制取 grade 序列、跑失活预算，返回越界违规列表
    （空=该 run 全绿）。``expect_active`` 为 None 时按 checkpoint.mode + run 目录名的
    scenario 族派生 should-activate 集（(场景族×机制) 准入）。"""
    from expos.kernel.store import RunStore  # 延迟：pydantic

    store = RunStore(run_dir, create=False)
    events = store.read_events()
    if expect_active is None:
        ckpt = store.read_checkpoint() or {}
        # run 目录名形如 ``<group_scenario>__<arm>__s<seed>``，scenario 族取首段
        scenario = run_dir.name.split("__")[0] if "__" in run_dir.name else None
        expect_active = expected_active(ckpt.get("mode"), scenario)

    violations: list[dict[str, Any]] = []
    for mech in sorted(expect_active):
        kind = MECHANISM_EVENT.get(mech)
        if kind is None:
            continue
        payload_violations: list[dict[str, Any]] = []
        grades = grade_stream(events, kind, violations=payload_violations)
        if payload_violations:
            violations.append({
                "run": str(run_dir), "mechanism": mech, "event_kind": kind,
                "n_rounds": len(grades), "grades": grades,
                "status": "PAYLOAD_VIOLATION", "details": payload_violations,
            })
        # R4 I-F1 fix: absence of telemetry is not a pass. A should-activate
        # mechanism with zero grade events (e.g. data generated before the
        # observation surface landed) must be reported as NO_COVERAGE, never
        # silently folded into "no violations" -- otherwise the gate reads
        # all-green over data it cannot see, and a truly dead mechanism is
        # indistinguishable from an unmonitored one.
        if not grades:
            violations.append({
                "run": str(run_dir), "mechanism": mech, "event_kind": kind,
                "n_rounds": 0, "grades": [], "status": "NO_COVERAGE",
                "reason": "should-activate mechanism has zero telemetry events; "
                          "gate result must not be cited as green",
            })
            continue
        breach = budget_breached(grades, budget)
        if breach is not None:
            violations.append({
                "run": str(run_dir), "mechanism": mech, "event_kind": kind,
                "n_rounds": len(grades), "grades": grades, "status": "BREACH",
                **breach,
            })
    return violations


def scan_runs(
    run_dirs: list[Path],
    expect_active: set[str] | None = None,
    budget: ActivityBudget = DEFAULT_BUDGET,
) -> dict[str, Any]:
    """扫一批 run，汇总红牌报告。返回 {n_runs, n_violations, violations}。"""
    all_viol: list[dict[str, Any]] = []
    for d in run_dirs:
        all_viol.extend(scan_run(d, expect_active, budget))
    return {"n_runs": len(run_dirs), "n_violations": len(all_viol),
            "violations": all_viol}


def _find_run_dirs(roots: list[str]) -> list[Path]:
    """把命令行给的路径展开为 run 目录集：既接受直接 run 目录（含 events.jsonl），也接受
    含多个 run 子目录的 sweep 根（递归找带 events.jsonl 的目录）。"""
    out: list[Path] = []
    for r in roots:
        p = Path(r)
        if (p / "events.jsonl").exists():
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(q.parent for q in p.glob("**/events.jsonl")))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="activity_budget",
        description="失活预算熔断——机制连续 k 轮失活（去抖长度 k）即红牌（sweep 级事后门）")
    ap.add_argument("runs", nargs="+", help="run 目录或含 run 的 sweep 根（递归找 events.jsonl）")
    ap.add_argument("--expect", default=None,
                    help="逗号分隔的应激活机制名（覆盖按 (场景族×机制) 派生的准入集）；"
                         f"可选: {','.join(MECHANISM_EVENT)}")
    ap.add_argument("--k", type=int, default=DEFAULT_BUDGET.k,
                    help=f"去抖长度：连续 k 轮失活即熔断（默认 {DEFAULT_BUDGET.k}，由 derive_k "
                         f"从 R=8/N=40/α=0.05/a_max=0.28 反解；经验紧档 k=6）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 报告")
    args = ap.parse_args(argv)

    budget = ActivityBudget(k=args.k)
    expect = (set(s.strip() for s in args.expect.split(",") if s.strip())
              if args.expect is not None else None)
    run_dirs = _find_run_dirs(args.runs)
    if not run_dirs:
        print("未找到任何含 events.jsonl 的 run 目录", file=sys.stderr)
        return 2

    report = scan_runs(run_dirs, expect, budget)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"扫描 {report['n_runs']} 个 run，去抖长度 k={budget.k}"
              f"（连续 {budget.k} 轮失活即红；容忍 ≤{budget.k - 1} 轮合法静默）")
        if not report["violations"]:
            print("✓ 失活预算全绿：应激活机制无 ≥k 轮连续失活段")
        else:
            print(f"❌ 红牌 {report['n_violations']} 条：")
            for v in report["violations"]:
                print(f"  {v['mechanism']} @ {v['run']}: 连续失活 {v['k']} 轮"
                      f"（游程 idx {v['inactive_run_start']}–{v['at_round_index']}，"
                      f"grades={v['grades']}）")
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
