"""M4 naive 闭环端到端验收测试：目录结构 / 事件追加 / 断点续跑 / naive 路由 /
truth 不透明 / 双域热插拔 / 依赖隔离。"""

import json
from pathlib import Path

import pytest

from expos.kernel.objects import Routing, TrustLevel
from expos.kernel.store import RunStore
from expos.loop import LoopError, derive_seed, run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"
COATING = ROOT / "domains" / "coating.yaml"


@pytest.fixture(scope="module")
def crystal_run(tmp_path_factory):
    """4 轮 crystal naive（module 级共享，避免重复跑）。"""
    out = tmp_path_factory.mktemp("runs") / "m4_naive"
    summary = run_loop(CRYSTAL, mode="naive", rounds=4, seed=7, out_dir=out)
    return out, summary


def test_crystal_naive_runs_four_rounds(crystal_run):
    out, summary = crystal_run
    assert summary["rounds_completed"] == 4
    assert summary["n_observations"] == summary["n_trusted"] > 0
    assert summary["best_trusted"] is not None and summary["best_trusted"]["value"] > 0


def test_run_directory_structure(crystal_run):
    out, _ = crystal_run
    for sub in ("config.json", "events.jsonl", "checkpoint.json",
                "experiments", "observations", "truth", "models", "report"):
        assert (out / sub).exists(), f"缺 {sub}"
    assert len(list((out / "experiments").glob("*.json"))) == 4
    assert len(list((out / "models").glob("snapshot_r*.json"))) == 4
    assert (out / "report" / "summary.json").exists()
    snaps = [json.loads(p.read_text()) for p in sorted((out / "models").glob("*.json"))]
    assert len({s["snapshot"] for s in snaps}) == 4  # 每轮数据变 → 指纹变


def test_events_append_only_and_lifecycle(crystal_run):
    out, _ = crystal_run
    store = RunStore(out, create=False)
    events = store.read_events()
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # 严格递增无重复
    kinds = {e["kind"] for e in events}
    assert {"round_designed", "status_transition", "routing_bulk",
            "model_updated", "checkpoint"} <= kinds
    # 仅第 0 轮 Sobol 起步（round 0 后 n_train=47 已 ≥ MIN_TRAIN_FOR_BO），其余全为 BO
    designed = store.read_events("round_designed")
    assert designed[0]["payload"]["generator"] == "sobol"
    assert all(d["payload"]["generator"] == "response_gp+ucb" for d in designed[1:])


def test_naive_marks_all_trusted(crystal_run):
    out, _ = crystal_run
    store = RunStore(out, create=False)
    obs = store.list_observations()
    assert len(obs) > 0
    assert all(o.trust == TrustLevel.TRUSTED for o in obs)
    assert all(o.routing == Routing.TO_RESPONSE_MODEL for o in obs)


def test_truth_sidecar_exists_but_opaque(crystal_run):
    out, _ = crystal_run
    truth_files = list((out / "truth").glob("round_*.jsonl"))
    assert len(truth_files) == 4  # 存在（供事后评分）
    # 但 loop/模型/采样器源码不解析真值字段
    for rel in ("expos/loop.py", "expos/models/response_gp.py", "expos/design/sampler.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for pat in ('"true_value"', "'true_value'", "truth_records["):
            assert pat not in src, f"{rel} 解析了 truth: {pat}"


def test_resume_continues_from_checkpoint(tmp_path):
    out = tmp_path / "resume_run"
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=11, out_dir=out)
    ckpt1 = json.loads((out / "checkpoint.json").read_text())
    assert ckpt1["completed_rounds"] == 2
    summary = run_loop(CRYSTAL, mode="naive", rounds=3, seed=11, out_dir=out, resume=True)
    assert summary["rounds_completed"] == 3
    store = RunStore(out, create=False)
    assert len(store.list_experiments()) == 3
    assert [e["payload"]["from_round"] for e in store.read_events("resume")] == [2]
    # 不加 --resume 重复跑同目录必须响亮失败（不覆盖）
    with pytest.raises(LoopError):
        run_loop(CRYSTAL, mode="naive", rounds=3, seed=11, out_dir=out)
    # resume 但配置不匹配必须响亮失败
    with pytest.raises(LoopError):
        run_loop(CRYSTAL, mode="naive", rounds=4, seed=999, out_dir=out, resume=True)


def test_coating_naive_runs_without_kernel_change(tmp_path):
    summary = run_loop(COATING, mode="naive", rounds=2, seed=5,
                       out_dir=tmp_path / "coating_run")
    assert summary["domain"] == "coating"
    assert summary["rounds_completed"] == 2
    assert summary["n_trusted"] > 0


def test_unknown_mode_and_bad_rounds_fail_loudly(tmp_path):
    """M5 起 os 模式合法；未知 mode（compare 留 M9）与非法 rounds 仍响亮失败。"""
    with pytest.raises(LoopError):
        run_loop(CRYSTAL, mode="compare", rounds=1, seed=1, out_dir=tmp_path / "x")
    with pytest.raises(LoopError):
        run_loop(CRYSTAL, mode="naive", rounds=0, seed=1, out_dir=tmp_path / "y")


def test_kappa_schedule_and_provenance(crystal_run):
    from expos.loop import _kappa_for_round

    assert _kappa_for_round(0, 4) == pytest.approx(3.0)
    assert _kappa_for_round(3, 4) == pytest.approx(1.0)
    assert _kappa_for_round(1, 4) == pytest.approx(3.0 - 2.0 / 3)
    assert _kappa_for_round(0, 1) == pytest.approx(2.0)  # 单轮退化分支
    out, _ = crystal_run
    store = RunStore(out, create=False)
    exps = store.list_experiments()
    acq = [e.provenance.acquisition for e in exps]
    assert acq[0] is None  # Sobol 轮
    # κ 视界 = 域配置 budget.rounds_total(8)，与 CLI rounds 无关（resume 等价性）
    assert acq[1] is not None and "kappa=2.71" in acq[1]  # κ(1,8)=3-2/7
    assert "kappa=2.14" in acq[3]  # κ(3,8)=3-6/7


def test_best_so_far_minimize(tmp_path):
    from expos.loop import _best_so_far
    from expos.kernel.objects import LayoutMeta, MeasuredResult, ObservationObject

    store = RunStore(tmp_path / "s")
    for i, v in enumerate([0.8, 0.2, 0.5]):
        store.save_observation(ObservationObject(
            exp_id="e", round_id=0, cand_id=f"c{i}",
            result=MeasuredResult(metric="q", value=v),
            layout_meta=LayoutMeta(well_id=f"A{i + 1}", row=0, col=i),
            trust=TrustLevel.TRUSTED, routing=Routing.TO_RESPONSE_MODEL,
        ))
    assert _best_so_far(store, "maximize")["value"] == pytest.approx(0.8)
    assert _best_so_far(store, "minimize")["value"] == pytest.approx(0.2)


def test_resume_idempotent_when_already_complete(tmp_path):
    out = tmp_path / "idem"
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=13, out_dir=out)
    n_events = len(RunStore(out, create=False).read_events())
    summary = run_loop(CRYSTAL, mode="naive", rounds=2, seed=13, out_dir=out, resume=True)
    assert summary["rounds_completed"] == 2
    # 早返回路径：除 resume 标记事件外不新增任何轮次工作
    events_after = RunStore(out, create=False).read_events()
    # Contract updated for R4-A: a clean resume now always lands exactly one
    # redo_reconciliation marker (see test_clean_resume_emits_no_reconciliation
    # for the rationale) plus the resume event itself -- hence +2, and the two
    # extra kinds are pinned so any OTHER event appearing on an idempotent
    # resume still fails loudly.
    extra = events_after[n_events:]
    assert len(extra) <= 2
    assert {e["kind"] for e in extra} <= {"redo_reconciliation", "resume"}
    assert len(RunStore(out, create=False).list_experiments()) == 2


def test_rounds_budget_exhaustion_fails_loudly(tmp_path):
    from expos.design.budget import BudgetError

    tiny = tmp_path / "tiny.yaml"
    tiny.write_text(
        CRYSTAL.read_text(encoding="utf-8").replace(
            "budget: {wells_total: 384, rounds_total: 8}",
            "budget: {wells_total: 384, rounds_total: 1}",
        )
    )
    with pytest.raises(BudgetError):
        run_loop(tiny, mode="naive", rounds=2, seed=3, out_dir=tmp_path / "b")


def test_cli_smoke(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "cli_run"
    base = [sys.executable, str(ROOT / "scripts" / "run_loop.py"),
            "--domain", "crystal", "--mode", "naive", "--seed", "3", "--out", str(out)]
    r1 = subprocess.run(base + ["--rounds", "1"], capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr
    assert json.loads(r1.stdout)["rounds_completed"] == 1
    # 同目录不加 --resume → 退出码 2
    r2 = subprocess.run(base + ["--rounds", "2"], capture_output=True, text=True)
    assert r2.returncode == 2 and "loop error" in r2.stderr
    # --resume 续跑到 2 轮
    r3 = subprocess.run(base + ["--rounds", "2", "--resume"], capture_output=True, text=True)
    assert r3.returncode == 0
    assert json.loads(r3.stdout)["rounds_completed"] == 2


def test_derive_seed_stable():
    assert derive_seed(7, "gen", 3) == derive_seed(7, "gen", 3)
    assert derive_seed(7, "gen", 3) != derive_seed(7, "gen", 4)
    assert derive_seed(7, "gen", 3) != derive_seed(8, "gen", 3)


def test_loop_dependency_isolation():
    """M5 起 loop 合法引用 expos.qc（VerdictPolicy 接线）；M7 planner 同理；
    M8 起合法引用 expos.agent.policy（第四策略注入点——agent 仍只有提案权，
    写账只走 lifecycle.submit_proposal / store.append_decision，公理 7 由
    kernel 层测试强制）。仍然禁区：绕开策略层直连 agent 后端/视图、UI。"""
    src = (ROOT / "expos" / "loop.py").read_text(encoding="utf-8")
    forbidden = ("expos.agent.backends", "expos.agent.views", "import ui", "from ui")
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"loop.py 引用了禁区: {hits}"
