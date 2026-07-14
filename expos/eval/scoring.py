"""事后评分器 —— truth sidecar 的**唯一合法读者**（docs/M9_PROTOCOL.md §3；公理 6 豁免）。

本模块在闭环**结束后**离线运行，读 `truth/round_*.jsonl` 与观测存储，逐轮计算
M9_PROTOCOL §3 的核心指标：simple regret（vs 真值面全局最优 `true_optimum`）、
污染样本利用率（训练集内**真污染** `|bias|>τ` 的观测比例，τ=3·noise_sd）、
错误最优命中率（当前推荐是否被伪影抬高）、逐轮 n_trusted/suspect/failed。

公理 6 豁免声明：跑内内核（qc/models/planner/agent/loop）严禁读 truth；只有本评分器
读它，且它不回写任何决策——评分是闭环之外的叶子。读 truth 在这里是合法且必要的。

**真污染定义（M9_PROTOCOL §3.3 对抗审查 + 试点修正）**：`bias = y_measured − y_clean`
（绝对偏差；`y_clean` = truth 的 `true_value`，无伪影无噪声），`|bias| > 3·noise_sd`
才算真污染——与 wrong_optimum_hit 的 3σ 判准同构，纯噪声误报率 ≈0.3%。
试点实锤的量纲 bug：旧版用**相对**偏差 `ym/yc−1` 对比**绝对** τ=noise_sd，
yc≈0.3 时纯噪声即超阈，零伪影场景污染率虚高至 ~0.72。不用裸 `artifacts` 标签
（EdgeEvaporation/ThermalGradient 幅度趋零仍标 applied，会虚高低幅档污染率）。

**污染分母双口径（STRESS_TEST_R1 R1-3(b) 修复）**：

- 旧口径 `contaminated_in_training` / `injected_in_training`（**保留兼容**）：
  分母 = 累积 raw TRUSTED 候选观测。缺陷：os-soft 软并入的 QUARANTINE 观测在
  实际消费污染却不进分母；对 robust 臂它数的是聚合前 raw 行而非模型消费面。
- 新口径 `training_contamination` / `training_injected`：分母 = **该臂实际喂给
  model.fit 的训练样本对应的原始观测集合**，按 run config 的 mode 还原聚合语义
  （`_effective_training_set`）——naive/robust/rcgp = 全 TRUSTED（信任盲裁决下
  全体皆 TRUSTED；robust 的中位/Huber 合成行由该副本组的全部原始观测背书）、
  os = TRUSTED（硬隔离不入模）、os-soft = TRUSTED ∪ routing==QUARANTINE 的
  软并入观测（loop._quarantined → SoftTrustAggregation 内存态复归的同一集合）。
  两口径分子分母同过滤（候选、非控制、有测量值——None 值喂不进 fit）。
  os-soft 消费被污染 QUARANTINE 时新口径 > 旧口径（不再与 os 硬隔离逐位相同）。

**训练集成员清单可复算（G-2，R2 修订）**：`contaminated_in_training` 依赖轮内时点的
信任/路由快照，外部无 truth+obs 时无法重推。score_run 额外落 `report/training_members.json`
——逐轮 dump 该臂实际入模的原始观测清单（obs_id/well_id/round_id + 逐孔 bias/contaminated/
injected 标志），第三方据此可独立重算污染率，无需复跑闭环。

**加权污染口径（K-P3 / §4 问4，R2 spec）**：rcgp 的 `training_contamination` 定义上恒等于
naive——rcgp 稳健性在**损失加权**（w=1/infl）而非入模筛选，训练集成员本就与 naive 同集合
（诚实事实非 bug）。「有效污染权重占比」`Σw·1[contam]/Σw`（naive/robust w=1、os w=0、
os-soft w=alpha、rcgp w=1/infl）需 models 侧导出 per-obs influence 权重（robust_gp 的 infl /
SoftTrust 的 alpha 均为跑内局部量，未落盘）——属**需 models 侧配合的 spec**（见
docs/M9_PROTOCOL.md §3.3 R2 段），本评分器不擅自重算模型内部权重。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from expos.domain import build_adapter, load_domain
from expos.errors import ExposError
from expos.kernel.objects import Routing, TrustLevel
from expos.kernel.store import RunStore


class EvalError(ExposError):
    pass


_ROUND_RE = re.compile(r"round_(\d+)\.jsonl$")


def load_truth(run_dir: str | Path) -> dict[int, dict[str, dict]]:
    """读 `run_dir/truth/round_*.jsonl` → {round_id: {well_id: record}}。

    truth 目录缺失 / 无 round 文件 → **响亮失败**（EvalError）：评分绝不在缺真值时静默降级。
    每行含 well_id/true_value/measured_value/artifacts（sim_base 产地契约）。
    """
    run_dir = Path(run_dir)
    tdir = run_dir / "truth"
    if not tdir.is_dir():
        raise EvalError(f"truth sidecar 目录缺失，无法事后评分: {tdir}")
    files = sorted(tdir.glob("round_*.jsonl"))
    if not files:
        raise EvalError(f"truth 目录无 round_*.jsonl（真值缺失）: {tdir}")
    out: dict[int, dict[str, dict]] = {}
    for f in files:
        m = _ROUND_RE.search(f.name)
        if m is None:
            continue
        rid = int(m.group(1))
        recs: dict[str, dict] = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            recs[rec["well_id"]] = rec
        out[rid] = recs
    if not out:
        raise EvalError(f"truth 目录未解析出任何轮次: {tdir}")
    return out


def _bias(rec: dict) -> float:
    """artifact+noise 绝对偏差 = measured − clean（M9_PROTOCOL §3.3，量纲=指标原单位）。
    与 τ=3·noise_sd 同量纲可比；相对偏差版会把小真值处的纯噪声误判成污染（试点 finding）。"""
    yc = rec.get("true_value")
    ym = rec.get("measured_value")
    if yc is None or ym is None:
        return 0.0
    return float(ym) - float(yc)


def _is_candidate_sample(o) -> bool:
    """训练样本资格的共同过滤：候选（非控制、cand_id 非空）且有测量值。
    None 值观测喂不进 model.fit，两口径一致排除（口径差异只在信任/路由集合）。"""
    return (not o.is_control) and o.cand_id is not None and o.result.value is not None


def _effective_training_set(obs: list, mode: str | None, upto_round: int) -> list:
    """新口径分母（R1-3(b)）：截至 ``upto_round``，该臂实际喂给 model.fit 的
    训练样本对应的**原始观测集合**，按 run config 的 mode 还原聚合语义：

    - ``naive`` / ``robust`` / ``rcgp``：全 TRUSTED 候选观测（信任盲裁决下全体皆
      TRUSTED；robust 的 MedianAggregation 合成行由该副本组全部原始观测背书，
      故原始集合仍是全 TRUSTED 副本）。
    - ``os``：TRUSTED 候选观测（SUSPECT/FAILED 硬隔离，绝不入模）。
    - ``os-soft``：TRUSTED ∪ routing==QUARANTINE 的软并入观测——与
      loop._quarantined → SoftTrustAggregation.prepare 内存态复归的集合一致
      （routing==QUARANTINE 且有测量值；TO_FAILURE_MODEL/FAILED 绝不并入）。
    - mode 未知/缺失：保守退化为 TRUSTED（与旧口径同集合，不虚增）。
    """
    trusted = [
        o
        for o in obs
        if o.round_id <= upto_round
        and o.trust == TrustLevel.TRUSTED
        and _is_candidate_sample(o)
    ]
    if mode == "os-soft":
        soft = [
            o
            for o in obs
            if o.round_id <= upto_round
            and o.routing == Routing.QUARANTINE
            and _is_candidate_sample(o)
        ]
        return trusted + soft
    return trusted


def _true_optimum_value(adapter, space, direction: str, n: int) -> float:
    """按目标方向估计真值面全局最优（离线密集 Sobol 扫描，仅评分用）。"""
    if not hasattr(adapter, "true_value"):
        raise EvalError("adapter 无 true_value（真值面）——非仿真 run 无法事后评分")
    if direction == "maximize" and hasattr(adapter, "true_optimum"):
        _, v = adapter.true_optimum(space, n=n)
        return float(v)
    # minimize（或无 true_optimum 便利方法）：自行扫描
    import numpy as np
    from scipy.stats import qmc

    from expos.design.space import dim, from_unit

    sob = qmc.Sobol(d=dim(space), scramble=True, seed=0)
    best = np.inf if direction == "minimize" else -np.inf
    for u in sob.random(n):
        val = float(adapter.true_value(from_unit(space, u)))
        if (direction == "minimize" and val < best) or (
            direction != "minimize" and val > best
        ):
            best = val
    return float(best)


def score_run(
    run_dir: str | Path, domain_yaml: str | Path, n_opt_scan: int = 4096
) -> dict[str, Any]:
    """逐轮事后评分，返回 summary dict 并写 `run_dir/report/score.json`。

    逐轮字段（M9_PROTOCOL §3）：
    - best_true_so_far：当前推荐（best-trusted）候选真值的**累积最优**（保证单调）；
    - simple_regret：`|f*_true − best_true_so_far|`，clamp≥0。f* **只由场景真值面
      的密集 Sobol 扫描决定**（seed=0 固定 → 同场景恒同值，跨臂/种子可配对；R1 修复：
      不再把该 run 观测真值并入 f*，扫描下界不足时 clamp 承接 + logging.warning 留痕）；
    - contaminated_in_training / injected_in_training：**旧口径**（保留兼容）——
      分母 = 截至本轮累积 raw TRUSTED 候选观测；
    - training_contamination / training_injected：**新口径**（R1-3(b)）——分母 =
      该臂实际喂给 model.fit 的训练样本对应的原始观测集合（见模块 docstring 与
      `_effective_training_set`；os-soft 含软并入 QUARANTINE，其余臂 = TRUSTED）；
    - wrong_optimum_hit：当前 best 是否被伪影抬高（measured 超真值 + 3σ，取有利方向）；
    - wrong_optimum_hit_5sigma：同上但 5σ 阈——赢者诅咒敏感性列（判据不改，佐证用）；
    - n_trusted / n_suspect / n_failed：本轮各信任级观测数。
    """
    run_dir = Path(run_dir)
    truth = load_truth(run_dir)
    cfg = load_domain(domain_yaml)
    adapter = build_adapter(cfg)

    direction = cfg.objective.direction
    sign = 1.0 if direction == "maximize" else -1.0
    noise_sd = float(cfg.simulator.get("noise_sd", 0.02))
    tau = 3.0 * noise_sd  # M9_PROTOCOL §3.3：τ = 3σ（绝对偏差阈，纯噪声误报 ≈0.3%）

    # f*（STRESS_TEST_R1 P2 修复）：只由场景域真值面的密集 Sobol 扫描决定
    # （true_optimum 内部 seed=0 固定）——同场景所有臂/种子恒同 f*，保住跨臂
    # 配对置换检验的配对性。旧版把该 run 观测真值并入 f*（防负 regret），导致
    # 同场景不同臂/种子 f* 不同、配对差混入 f* 噪声。扫描下界若确实低于该 run
    # 观测真值：负 regret 由下方 clamp≥0 承接，此处响亮留痕（不静默）。
    f_star = _true_optimum_value(adapter, cfg.design_space, direction, n_opt_scan)
    all_true = [
        float(r["true_value"])
        for rr in truth.values()
        for r in rr.values()
        if r.get("true_value") is not None
    ]
    if all_true:
        obs_extreme = max(all_true) if sign > 0 else min(all_true)
        if sign * obs_extreme > sign * f_star + 1e-12:
            logging.getLogger(__name__).warning(
                "run %s 观测到的真值 %.6g 超过 Sobol 扫描 f*=%.6g（n=%d）——"
                "regret 将被 clamp 到 0；如系统性出现请提高 n_opt_scan",
                run_dir, obs_extreme, f_star, n_opt_scan,
            )

    store = RunStore(run_dir, create=False)
    obs = store.list_observations()
    rounds = sorted({o.round_id for o in obs})
    cfg_run = store.read_config() or {}
    mode = cfg_run.get("mode")  # 新口径按臂聚合语义还原训练集（R1-3(b)）

    def _truth_of(o):
        return truth.get(o.round_id, {}).get(o.layout_meta.well_id)

    def _contam_ratios(subset: list) -> tuple[float, float]:
        """(真污染比例, 注入标签比例)——分子分母同集合，无 truth 记录的观测计入
        分母但不计分子（与旧实现一致，跨臂同口径可比）。"""
        n_contam = n_injected = 0
        for o in subset:
            rec = _truth_of(o)
            if rec is None:
                continue
            if abs(_bias(rec)) > tau:
                n_contam += 1
            if rec.get("artifacts"):
                n_injected += 1
        if not subset:
            return 0.0, 0.0
        return n_contam / len(subset), n_injected / len(subset)

    def _member_record(o) -> dict[str, Any]:
        """训练集成员的可复算记录（G-2）：obs 定位 + truth 逐孔物理事实。"""
        rec = _truth_of(o)
        bias = _bias(rec) if rec is not None else None
        return {
            "obs_id": o.obs_id,
            "well_id": o.layout_meta.well_id,
            "round_id": o.round_id,
            "trust": o.trust.value if hasattr(o.trust, "value") else str(o.trust),
            "routing": (o.routing.value if o.routing is not None
                        and hasattr(o.routing, "value") else
                        (str(o.routing) if o.routing is not None else None)),
            "bias": bias,
            "contaminated": (abs(bias) > tau) if bias is not None else None,
            "injected": bool(rec.get("artifacts")) if rec is not None else None,
        }

    rows: list[dict[str, Any]] = []
    train_members: list[dict[str, Any]] = []  # G-2：逐轮入模成员清单（可独立复算污染率）
    running_best_good: float | None = None  # 累积最优推荐真值（以 sign*value 度量）

    for r in rounds:
        round_obs = [o for o in obs if o.round_id == r]
        n_trusted = sum(o.trust == TrustLevel.TRUSTED for o in round_obs)
        n_suspect = sum(o.trust == TrustLevel.SUSPECT for o in round_obs)
        n_failed = sum(o.trust == TrustLevel.FAILED for o in round_obs)

        # ---- 训练集污染率·旧口径（保留兼容）：截至本轮累积 raw TRUSTED 候选观测
        train = [
            o
            for o in obs
            if o.round_id <= r
            and o.trust == TrustLevel.TRUSTED
            and not o.is_control
            and o.cand_id is not None
        ]
        # 文献标准双列（Huber ε-污染按注入标签定义；RCGP 家族按注入比例报 corruption）：
        # injected=是否被注入（标签），contaminated=注入是否有效（|bias|>3σ）。
        contaminated, injected = _contam_ratios(train)

        # ---- 训练集污染率·新口径（R1-3(b)）：该臂实际入模原始观测集合
        eff_train = _effective_training_set(obs, mode, r)
        training_contamination, training_injected = _contam_ratios(eff_train)

        # ---- G-2：dump 该轮实际入模成员清单（外部据此独立重算污染率）
        train_members.append({
            "round": r,
            "mode": mode,
            "n_effective": len(eff_train),
            "n_legacy_trusted": len(train),
            "members": [_member_record(o) for o in eff_train],
        })

        # ---- 当前推荐 best-trusted（按测量值，取有利方向），及其真值
        best_o = None
        best_good = -float("inf")
        for o in obs:
            if o.round_id > r or o.trust != TrustLevel.TRUSTED or o.is_control:
                continue
            if o.result.value is None:
                continue
            good = sign * o.result.value
            if good > best_good:
                best_good, best_o = good, o

        best_trusted: dict[str, Any] | None = None
        wrong_hit = False
        wrong_hit_5 = False
        if best_o is not None:
            rec = _truth_of(best_o)
            true_v = float(rec["true_value"]) if rec else None
            meas_v = float(rec["measured_value"]) if rec else best_o.result.value
            best_trusted = {
                "cand_id": best_o.cand_id,
                "well_id": best_o.layout_meta.well_id,
                "round_id": best_o.round_id,
                "measured": best_o.result.value,
                "true": true_v,
            }
            if true_v is not None:
                good_true = sign * true_v
                running_best_good = (
                    good_true
                    if running_best_good is None
                    else max(running_best_good, good_true)
                )
                # 伪影把 measured 抬到真值有利侧 3σ 之外 = 假最优命中。
                # 赢者诅咒边界（R1 已知）：best 本身是 argmax(measured)，选择偏差
                # 使纯噪声也可能过 3σ——预注册判据不改，另报 5σ 敏感性列佐证
                # （M9_PROTOCOL §3 指标 2 的 R1 修订段）。
                delta_fav = sign * (meas_v - true_v)
                wrong_hit = delta_fav > 3.0 * noise_sd
                wrong_hit_5 = delta_fav > 5.0 * noise_sd

        if running_best_good is None:
            best_true_so_far = None
            regret = None
        else:
            best_true_so_far = sign * running_best_good
            regret = max(0.0, (sign * f_star) - running_best_good)

        rows.append(
            {
                "round": r,
                "best_true_so_far": best_true_so_far,
                "simple_regret": regret,
                "best_trusted": best_trusted,
                "contaminated_in_training": contaminated,
                "injected_in_training": injected,
                "training_contamination": training_contamination,
                "training_injected": training_injected,
                "wrong_optimum_hit": wrong_hit,
                "wrong_optimum_hit_5sigma": wrong_hit_5,
                "n_trusted": n_trusted,
                "n_suspect": n_suspect,
                "n_failed": n_failed,
            }
        )

    final = rows[-1] if rows else {}
    summary: dict[str, Any] = {
        "run_dir": str(run_dir),
        "domain": cfg.name,
        "arm": mode,
        "direction": direction,
        "f_star": f_star,
        "noise_sd": noise_sd,
        "tau_bias": tau,
        "n_rounds": len(rows),
        "contaminated_in_training": final.get("contaminated_in_training", 0.0),
        "injected_in_training": final.get("injected_in_training", 0.0),
        "training_contamination": final.get("training_contamination", 0.0),
        "training_injected": final.get("training_injected", 0.0),
        "final_regret": final.get("simple_regret"),
        "wrong_optimum_hit_any": any(row["wrong_optimum_hit"] for row in rows),
        "wrong_optimum_hit_any_5sigma": any(
            row["wrong_optimum_hit_5sigma"] for row in rows
        ),
        "rounds": rows,
    }

    report = run_dir / "report"
    report.mkdir(parents=True, exist_ok=True)
    (report / "score.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # G-2（R2）：训练集成员清单侧车——第三方可独立重算污染率（无需复跑闭环）。
    (report / "training_members.json").write_text(
        json.dumps({"run_dir": str(run_dir), "arm": mode, "tau_bias": tau,
                    "rounds": train_members}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary
