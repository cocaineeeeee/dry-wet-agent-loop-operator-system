"""M5 os 模式端到端验收：三级 QC 接线 / 假最优拒斥 / QC 税 / 双策略零分支 / resume。"""

import json
from pathlib import Path

import pytest
import yaml

from expos.kernel.objects import Routing, TrustLevel
from expos.kernel.store import RunStore
from expos.loop import LoopError, run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"


@pytest.fixture(scope="module")
def os_run(tmp_path_factory):
    """4 轮 crystal os（module 级共享；第 3 轮含强边缘伪影事件）。"""
    out = tmp_path_factory.mktemp("runs") / "m5_os"
    summary = run_loop(CRYSTAL, mode="os", rounds=4, seed=7, out_dir=out)
    return out, summary


@pytest.fixture(scope="module")
def clean_yaml(tmp_path_factory):
    cfg = yaml.safe_load(CRYSTAL.read_text(encoding="utf-8"))
    cfg["simulator"]["artifact_scenario"] = []
    p = tmp_path_factory.mktemp("dom") / "crystal_clean.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


def test_os_mode_runs_and_quarantines(os_run):
    out, summary = os_run
    assert summary["rounds_completed"] == 4
    assert summary["n_suspect"] > 0  # 伪影场景下必须有隔离
    assert summary["n_trusted"] + summary["n_suspect"] + summary["n_failed"] == summary["n_observations"]


def test_os_rejects_fake_optimum(os_run):
    """demo 论点：naive 同 seed 追到 >1.0 的伪影假最优；os 的 best_trusted 必须物理合理。"""
    out, summary = os_run
    assert summary["best_trusted"]["value"] <= 1.0  # 真值面上限——假最优被拒
    # 假最优所在的第 3 轮边缘事件被精确命中
    store = RunStore(out, create=False)
    qc_events = {e["payload"]["round_id"]: e["payload"] for e in store.read_events("qc_report")}
    assert qc_events[3]["check_counts"].get("edge_effect", 0) >= 10  # 边缘检查大量触发
    early = sum(qc_events[r]["n_suspect"] for r in (0, 1, 2))
    assert qc_events[3]["n_suspect"] > early  # 事件轮显著高于常驻轮


def test_suspects_never_enter_response_model(os_run):
    """公理 2 的闭环级断言：训练集大小 == TRUSTED 数（含哨兵），SUSPECT 结构性排除。"""
    out, summary = os_run
    store = RunStore(out, create=False)
    n_train_final = store.read_events("model_updated")[-1]["payload"]["n_train"]
    assert n_train_final == summary["n_trusted"]
    for obs in store.list_observations(trust=TrustLevel.SUSPECT):
        assert obs.routing in (Routing.TO_FAILURE_MODEL, Routing.QUARANTINE)
        assert obs.qc is not None and obs.qc.suspicion >= 0.3  # 有证据才隔离


def test_qc_events_present_and_naive_absent(os_run):
    out, _ = os_run
    store = RunStore(out, create=False)
    assert len(store.read_events("qc_report")) == 4  # 每轮一条
    assert store.read_events("routing_bulk") == []   # os 臂不走 naive 批量路由
    assert len(store.read_events("routing")) > 0     # 逐观测裁决事件


def test_zero_artifact_qc_tax(clean_yaml, tmp_path):
    """M5 验收线：零伪影场景假阳性 SUSPECT 率 ≤5%（DEEP_REVIEW §2C）。"""
    summary = run_loop(clean_yaml, mode="os", rounds=3, seed=11, out_dir=tmp_path / "clean")
    tax = summary["n_suspect"] / summary["n_observations"]
    assert tax <= 0.05, f"QC 税 {tax:.3f} 超过 5% 验收线"


def test_os_resume(clean_yaml, tmp_path):
    out = tmp_path / "os_resume"
    run_loop(clean_yaml, mode="os", rounds=2, seed=13, out_dir=out)
    summary = run_loop(clean_yaml, mode="os", rounds=3, seed=13, out_dir=out, resume=True)
    assert summary["rounds_completed"] == 3
    assert len(RunStore(out, create=False).list_experiments()) == 3


def test_unknown_mode_fails_loudly(tmp_path):
    with pytest.raises(LoopError):
        run_loop(CRYSTAL, mode="compare", rounds=1, seed=1, out_dir=tmp_path / "x")


def test_loop_has_single_mode_branch():
    """零 mode 分支红线：mode 字符串判定只允许出现在 _policies_for_mode。"""
    src = (ROOT / "expos" / "loop.py").read_text(encoding="utf-8")
    body = src.split("def _policies_for_mode", 1)[1].split("def ", 1)[0]
    outside = src.replace(body, "")
    # 主体中不允许再有对 mode 值的判定（保存/校验配置里的字符串键除外）
    assert 'mode == "naive"' not in outside and 'mode == "os"' not in outside
    # 消融臂经 _os_family_policies 布尔/工厂参数化装配（非新类爆炸）——该装配函数
    # 亦不得含 mode 字符串判定（判定全留在分支里）。
    helper = src.split("def _os_family_policies", 1)[1].split("\ndef ", 1)[0]
    assert "mode ==" not in helper and "mode in" not in helper


# ================================================================ M13 消融臂矩阵（R2 §1.2）

_ABLATION_MODES = ["os-lite", "os-minus-riskmap", "os-minus-arbiter",
                   "os-minus-attribution"]


@pytest.mark.parametrize("mode", _ABLATION_MODES)
def test_ablation_arm_smoke_two_rounds(mode, tmp_path):
    """五元组注入点接线冒烟：每个消融臂 2 轮跑通、产 QC 检出事件、账目自洽。
    消融只改一处机制，QC 三级检出/路由（os 家族公共栈）在所有臂上照常。"""
    summary = run_loop(CRYSTAL, mode=mode, rounds=2, seed=7,
                       out_dir=tmp_path / mode)
    assert summary["rounds_completed"] == 2
    assert (summary["n_trusted"] + summary["n_suspect"] + summary["n_failed"]
            == summary["n_observations"])
    store = RunStore(tmp_path / mode, create=False)
    # os 家族公共栈：三级 QC 检出照常（每轮一条 qc_report，不走 naive 批量路由）
    assert len(store.read_events("qc_report")) == 2
    assert store.read_events("routing_bulk") == []
    # SUSPECT 结构性排除响应模型（公理 2）在所有 os 家族臂上不变
    n_train_final = store.read_events("model_updated")[-1]["payload"]["n_train"]
    assert n_train_final == summary["n_trusted"]


def test_os_lite_uses_isotropic_capacity_model():
    """os-lite 容量对齐：工厂产 ard=False（各向同性）ResponseModel，与 os 全栈其余四策略
    逐一相同——只有模型容量档不同（隔离路由层贡献 vs 代理容量税，R2 §1.2）。"""
    from expos.domain import load_domain
    from expos.loop import _policies_for_mode
    cfg = load_domain(CRYSTAL)
    os_pol = _policies_for_mode("os", cfg, 7)
    lite_pol = _policies_for_mode("os-lite", cfg, 7)
    # 五元组前四策略同型（裁决/聚合/规划/agent 类名一致）
    for a, b in zip(os_pol[:4], lite_pol[:4]):
        assert type(a).__name__ == type(b).__name__
    # 第五注入点（model_factory）：os-lite 产各向同性模型
    lite_model = lite_pol[4](cfg, 7)
    os_model = os_pol[4](cfg, 7)
    assert lite_model._ard is False
    assert os_model._ard is True