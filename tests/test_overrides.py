"""overrides pending 通道消费端测试（STRESS_TEST_R1 P2「CLI override 死投递」修复验收）。

覆盖：合法投递被应用（事件/decision 落账 + 文件进 applied/）；缺字段/非法枚举/
非法组合/未知 obs/actor=agent/坏 JSON 全进 rejected/ 带 reject_reason 且响亮告警；
陈旧 base_version 拒绝；幂等重复消费不双改判；README 所述 CLI 端到端
（expos.cli override 投递 → consume → status 计数变化）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from expos.kernel.lifecycle import route_observation
from expos.kernel.objects import (
    Budget,
    Candidate,
    Control,
    DecisionKind,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutAssignment,
    LayoutMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    QCCheck,
    QCReport,
    Routing,
    TrustLevel,
    VariableDef,
    WellAssignment,
)
from expos.kernel.overrides import OverrideError, consume_pending_overrides
from expos.kernel.store import RunStore

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 构造器

def _make_experiment(round_id: int = 0) -> ExperimentObject:
    space = DesignSpace(
        name="crystal",
        variables=[VariableDef(name="supersaturation", low=1.05, high=1.6, unit="S")],
    )
    cand = Candidate(params={"supersaturation": 1.2}, source="sobol")
    ctrl = Control(kind="sentinel", params={"supersaturation": 1.1},
                   expected_band=(0.3, 0.5))
    layout = LayoutAssignment(
        rows=6, cols=8, seed=7,
        wells=[
            WellAssignment(well_id="A1", row=0, col=0,
                           control_id=ctrl.control_id, is_edge=True, block_id="Q0"),
            WellAssignment(well_id="C4", row=2, col=3,
                           cand_id=cand.cand_id, block_id="Q1"),
        ],
    )
    return ExperimentObject(
        round_id=round_id, domain="crystal",
        objective=Objective(name="q", metric="quality_index"),
        design_space=space, active_vars=["supersaturation"],
        candidates=[cand], controls=[ctrl], layout=layout,
        budget=Budget(wells_total=48, rounds_total=4),
        execution_req=ExecutionReq(adapter="sim_crystal"),
        provenance=DesignProvenance(generator="sobol"),
    )


def _make_observation(exp: ExperimentObject) -> ObservationObject:
    return ObservationObject(
        exp_id=exp.exp_id, round_id=exp.round_id,
        cand_id=exp.candidates[0].cand_id,
        result=MeasuredResult(metric="quality_index", value=0.72, uncertainty=0.03),
        layout_meta=LayoutMeta(well_id="C4", row=2, col=3, block_id="Q1"),
        qc=QCReport(checks=[QCCheck(name="value_range", level="hard", passed=True)]),
    )


@pytest.fixture()
def run(tmp_path):
    """带一条已路由 TRUSTED 观测的运行目录。返回 (store, obs)。"""
    store = RunStore(tmp_path / "run")
    exp = _make_experiment()
    store.save_experiment(exp)
    obs = route_observation(store, _make_observation(exp))
    assert obs.trust == TrustLevel.TRUSTED
    return store, obs


def _deliver(store: RunStore, name: str = "ovr_t1.json", **fields) -> Path:
    """向 overrides/pending/ 投递一个提案文件（默认字段可被 fields 覆盖/删除）。"""
    proposal = {
        "obs_id": fields.pop("obs_id", None),
        "to_trust": "SUSPECT",
        "to_routing": "QUARANTINE",
        "reason": "哨兵证实为反光误报",
        "actor": "human",
        "source": "test",
    }
    proposal.update(fields)
    proposal = {k: v for k, v in proposal.items() if v is not ...}  # ... 表示删除该键
    pending = store.root / "overrides" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    dest = pending / name
    dest.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    return dest


def _rejected_files(store: RunStore) -> list[Path]:
    d = store.root / "overrides" / "rejected"
    return sorted(d.glob("*.json")) if d.is_dir() else []


# ---------------------------------------------------------------- 合法投递

def test_valid_override_applied_with_audit_trail(run):
    store, obs = run
    obs_path = store.root / "observations" / f"{obs.obs_id}.json"
    _deliver(store, obs_id=obs.obs_id, base_version=os.path.getmtime(obs_path))

    summary = consume_pending_overrides(store, store.root)
    assert [s["status"] for s in summary] == ["applied"]
    assert summary[0]["obs_id"] == obs.obs_id

    # 观测状态被改判（经 reclassify 既有通道）
    updated = store.load_observation(obs.obs_id)
    assert updated.trust == TrustLevel.SUSPECT
    assert updated.routing == Routing.QUARANTINE
    # 事件 + OVERRIDE decision 落账，actor=human
    rc = store.read_events("reclassification")
    assert len(rc) == 1 and rc[0]["payload"]["actor"] == "human"
    decs = store.list_decisions(kind=DecisionKind.OVERRIDE)
    assert len(decs) == 1 and obs.obs_id in decs[0].refs
    # 文件进 applied/（原名保留 + applied_at 附加），pending 清空
    applied = store.root / "overrides" / "applied" / "ovr_t1.json"
    assert applied.is_file()
    body = json.loads(applied.read_text(encoding="utf-8"))
    assert "applied_at" in body and body["applied_routing"] == "QUARANTINE"
    assert not list((store.root / "overrides" / "pending").glob("*.json"))
    assert not _rejected_files(store)


def test_null_routing_derives_default_readme_form(run):
    """README 形态：override 不带 --routing（to_routing=null）→ 按 adjudicate 约定取默认。"""
    store, obs = run
    _deliver(store, obs_id=obs.obs_id, to_trust="FAILED", to_routing=None)
    summary = consume_pending_overrides(store, store.root)
    assert summary[0]["status"] == "applied"
    assert summary[0]["to_routing"] == "TO_FAILURE_MODEL"
    assert store.load_observation(obs.obs_id).routing == Routing.TO_FAILURE_MODEL


# ---------------------------------------------------------------- 非法投递 → rejected

def _assert_rejected(store, summary, reason_substr: str):
    assert [s["status"] for s in summary] == ["rejected"]
    assert reason_substr in summary[0]["reject_reason"]
    files = _rejected_files(store)
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert reason_substr in body["reject_reason"]
    assert not list((store.root / "overrides" / "pending").glob("*.json"))
    # 改判从未发生
    assert not store.list_decisions(kind=DecisionKind.OVERRIDE)


def test_missing_field_rejected(run, caplog):
    store, obs = run
    _deliver(store, obs_id=obs.obs_id, reason=...)  # 删除 reason 键
    with caplog.at_level(logging.WARNING, logger="expos.kernel.overrides"):
        summary = consume_pending_overrides(store, store.root)
    _assert_rejected(store, summary, "缺必填字段")
    assert any("投递被拒" in r.message for r in caplog.records)  # 响亮不静默


def test_invalid_trust_enum_rejected(run):
    store, obs = run
    _deliver(store, obs_id=obs.obs_id, to_trust="GOLDEN")
    _assert_rejected(store, consume_pending_overrides(store, store.root), "非法枚举")
    assert store.load_observation(obs.obs_id).trust == TrustLevel.TRUSTED


def test_illegal_combo_rejected(run):
    store, obs = run
    _deliver(store, obs_id=obs.obs_id, to_trust="TRUSTED", to_routing="QUARANTINE")
    _assert_rejected(store, consume_pending_overrides(store, store.root), "组合非法")


def test_unknown_obs_rejected(run):
    store, _ = run
    _deliver(store, obs_id="obs_doesnotexist")
    _assert_rejected(store, consume_pending_overrides(store, store.root), "观测不存在")


def test_agent_actor_rejected(run):
    """红线：agent 伪造 actor 的投递不被接受（公理 7）。"""
    store, obs = run
    _deliver(store, obs_id=obs.obs_id, actor="agent")
    _assert_rejected(store, consume_pending_overrides(store, store.root), "非 human")
    assert store.load_observation(obs.obs_id).trust == TrustLevel.TRUSTED


def test_unparseable_json_rejected(run):
    store, _ = run
    pending = store.root / "overrides" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "ovr_bad.json").write_text("{not json", encoding="utf-8")
    summary = consume_pending_overrides(store, store.root)
    _assert_rejected(store, summary, "JSON 不可解析")


def test_stale_base_version_rejected(run):
    store, obs = run
    obs_path = store.root / "observations" / f"{obs.obs_id}.json"
    _deliver(store, obs_id=obs.obs_id,
             base_version=os.path.getmtime(obs_path) - 10.0)  # 确定性陈旧
    _assert_rejected(store, consume_pending_overrides(store, store.root), "陈旧投递")
    assert store.load_observation(obs.obs_id).trust == TrustLevel.TRUSTED


# ---------------------------------------------------------------- 幂等

def test_idempotent_reconsume_no_double_reclassify(run, caplog):
    store, obs = run
    _deliver(store, obs_id=obs.obs_id)
    assert consume_pending_overrides(store, store.root)[0]["status"] == "applied"
    # 同名文件再次出现在 pending（重复投递/崩溃重放）→ 跳过，不双改判
    applied = store.root / "overrides" / "applied" / "ovr_t1.json"
    shutil.copy(applied, store.root / "overrides" / "pending" / "ovr_t1.json")
    with caplog.at_level(logging.WARNING, logger="expos.kernel.overrides"):
        summary = consume_pending_overrides(store, store.root)
    assert [s["status"] for s in summary] == ["skipped_duplicate"]
    assert len(store.list_decisions(kind=DecisionKind.OVERRIDE)) == 1
    assert len(store.read_events("reclassification")) == 1
    assert not list((store.root / "overrides" / "pending").glob("*.json"))


def test_consume_empty_and_root_mismatch(run, tmp_path):
    store, _ = run
    # 无 pending 目录 → 空摘要（overrides/ 尚未创建的全新 run）
    fresh = RunStore(tmp_path / "fresh")
    assert consume_pending_overrides(fresh, fresh.root) == []
    # store 与 run_root 指向不同目录 → 编程 bug，响亮
    with pytest.raises(OverrideError):
        consume_pending_overrides(store, fresh.root)


# ---------------------------------------------------------------- README CLI 端到端

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "expos.cli", *args],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )


def test_cli_override_end_to_end_status_counts(run):
    """expos.cli override 投递 → consume → status 计数变化（README 人类改判通道闭环）。"""
    store, obs = run
    # status 需要 checkpoint + config（消费端本身不需要）
    store.save_config({"domain": "crystal", "mode": "os", "seed": 7, "domain_config": {}})
    store.write_checkpoint({
        "completed_rounds": 1, "round_id": 0,
        "budget": {"wells_total": 48, "wells_used": 2, "rounds_total": 4, "rounds_used": 1},
    })

    proc = _cli("override", str(store.root), "--obs", obs.obs_id,
                "--trust", "suspect", "--routing", "QUARANTINE",
                "--reason", "哨兵证实为反光误报", "--json")
    assert proc.returncode == 0, proc.stderr

    st0 = json.loads(_cli("status", str(store.root), "--json").stdout)
    assert st0["overrides"] == {"pending": 1, "applied": 0, "rejected": 0}

    summary = consume_pending_overrides(store, store.root)
    assert [s["status"] for s in summary] == ["applied"]

    st1 = json.loads(_cli("status", str(store.root), "--json").stdout)
    assert st1["overrides"] == {"pending": 0, "applied": 1, "rejected": 0}
    assert st1["trust"]["SUSPECT"] == 1 and st1["trust"]["TRUSTED"] == 0
