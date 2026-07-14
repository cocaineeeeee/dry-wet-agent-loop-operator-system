"""claim_compiler 测试 —— 覆盖四主张状态、stale 判定（pending + mtime/sha 机制）、
sha 校验、账本漂移侦测、--check 门禁。"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "claim_compiler",
    Path(__file__).resolve().parents[1] / "scripts" / "claim_compiler.py",
)
cc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cc)


# ------------------------------------------------------------
# 单元：判定函数
# ------------------------------------------------------------
def test_decision_supported_when_significant_favorable():
    status, _ = cc.decision_paired_significance(1.9e-6, -0.14, 0.05, "negative")
    assert status == cc.STATUS_SUPPORTED


def test_decision_rejected_when_significant_adverse():
    # 预注册期望 negative（os 更好），实测显著为正（os 更差）→ 拒绝
    status, _ = cc.decision_paired_significance(1e-4, +0.01606, 0.05, "negative")
    assert status == cc.STATUS_REJECTED


def test_decision_partial_when_not_significant():
    status, _ = cc.decision_paired_significance(0.4, -0.01, 0.05, "negative")
    assert status == cc.STATUS_PARTIAL


def test_decision_invalid_when_missing():
    status, _ = cc.decision_paired_significance(None, -0.1, 0.05, "negative")
    assert status == cc.STATUS_INVALID


# ------------------------------------------------------------
# 单元：sha / mtime
# ------------------------------------------------------------
def test_sha256_file(tmp_path: Path):
    p = tmp_path / "a.json"
    p.write_bytes(b'{"x":1}')
    assert cc._sha256_file(p) == cc._sha256_bytes(b'{"x":1}')
    assert cc._sha256_file(tmp_path / "missing.json") is None


# ------------------------------------------------------------
# 端到端：合成迷你仓库
# ------------------------------------------------------------
def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "claims").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    # 复制编译器源码（compiler_fingerprint 需读 __file__；此处直接指 real 脚本即可，
    # 但 compile_ledger 用 root 定位 claims/，故只需在 root 放 claims/ 与 campaign）。
    # headline 型证据
    _write(root / "runs" / "full_sweep" / "report" / "headline_stats.json", {
        "results": [
            {"metric": "contamination", "comparison": "os_vs_naive",
             "p_exact_permutation": 1.9e-6, "observed_mean_diff": -0.142},
            {"metric": "wrong_optimum", "comparison": "os_vs_naive",
             "p_exact_permutation": 3.05e-5, "observed_mean_diff": -0.8},
        ],
        "input_values_sha256": "deadbeef",
    })
    # aggregate 型证据（H1 拒绝）
    _write(root / "runs" / "r1_resweep" / "report" / "aggregate_summary.json", {
        "h1_verdict": {"pool_S2r3_mid_high": {
            "p_value": 1e-4, "mean_diff_os_minus_robust": 0.01606}},
    })
    # batch 证据
    _write(root / "runs" / "full_sweep" / "report" / "agg_batch.json",
           {"detection_failure_points": {"batch_shift": -0.07}})
    # campaign manifest（用于 mtime 机制测试）
    _write(root / "runs" / "r1_resweep" / "campaign_manifest.json", {
        "campaign_id": "r1_resweep", "created_at": "2026-07-11T16:36:00Z",
        "grid": {"cells_sha256": "cef926", "n_cells": 2700},
    })
    return root


def _write_registries(root: Path, claims: list[dict], deviations: list[dict]) -> None:
    (root / "claims" / "claims.yaml").write_text(
        yaml.safe_dump({"version": 1, "alpha_default": 0.05, "claims": claims},
                       allow_unicode=True), encoding="utf-8")
    (root / "claims" / "deviations.yaml").write_text(
        yaml.safe_dump({"version": 1, "deviations": deviations},
                       allow_unicode=True), encoding="utf-8")


_C1 = {
    "claim_id": "contam.os_vs_naive", "text": "污染防护",
    "claim_kind": "superiority", "decision_fn": "paired_significance_verdict",
    "generation": "gen-1", "domain": "contamination",
    "evidence": {"source_file": "runs/full_sweep/report/headline_stats.json",
                 "selector": {"kind": "array_match", "array_key": "results",
                              "match": {"metric": "contamination", "comparison": "os_vs_naive"},
                              "p_field": "p_exact_permutation", "diff_field": "observed_mean_diff",
                              "cells_sha_field": "input_values_sha256"}},
    "favorable_direction": "negative",
}
_C3 = {
    "claim_id": "h1.os_vs_robust", "text": "H1",
    "claim_kind": "superiority", "decision_fn": "paired_significance_verdict",
    "generation": "gen-2", "domain": "h1_regret",
    "evidence": {"source_file": "runs/r1_resweep/report/aggregate_summary.json",
                 "selector": {"kind": "path",
                              "p_field": "h1_verdict.pool_S2r3_mid_high.p_value",
                              "diff_field": "h1_verdict.pool_S2r3_mid_high.mean_diff_os_minus_robust"}},
    "favorable_direction": "negative", "deviations": ["H1_REJECTED"],
}
_C4 = {
    "claim_id": "batch.os", "text": "batch",
    "claim_kind": "superiority", "decision_fn": "paired_significance_verdict",
    "generation": "gen-1", "domain": "batch_attribution",
    "evidence": {"source_file": "runs/full_sweep/report/agg_batch.json",
                 "selector": {"kind": "path", "value_field": "detection_failure_points.batch_shift"}},
    "favorable_direction": "negative", "deviations": ["batch_diseased"],
}
_DEVS = [
    {"deviation_id": "H1_REJECTED", "class": "prereg_rejected", "status": "closed",
     "pending_reaggregation": False, "summary": "方向相反"},
    {"deviation_id": "batch_diseased", "class": "data_diseased", "status": "open",
     "pending_reaggregation": True, "pending_generation": "gen-3", "summary": "判反"},
]


def _compile(root: Path):
    _write_registries(root, [_C1, _C3, _C4], _DEVS)
    ledger = cc.compile_ledger(root)
    return {d["claim_id"]: d for d in ledger["claims"]}, ledger


def test_four_claim_statuses(tmp_path: Path):
    root = _mini_repo(tmp_path)
    by_id, _ = _compile(root)
    assert by_id["contam.os_vs_naive"]["status"] == cc.STATUS_SUPPORTED
    assert by_id["h1.os_vs_robust"]["status"] == cc.STATUS_REJECTED
    # ④ 自动 stale（引用 open+pending 偏差）
    assert by_id["batch.os"]["status"] == cc.STATUS_STALE
    assert by_id["batch.os"]["stale_reasons"], "stale_reasons 应非空"


def test_supported_records_evidence_sha_and_cells(tmp_path: Path):
    root = _mini_repo(tmp_path)
    by_id, _ = _compile(root)
    ev = by_id["contam.os_vs_naive"]["evidence"]
    assert ev["exists"] is True
    assert ev["sha256"] is not None and ev["sha256"].startswith("sha256:")
    assert ev["cells_sha256"] == "deadbeef"
    assert ev["p_value"] == 1.9e-6


def test_stale_via_mtime_supersession(tmp_path: Path):
    """(a) mtime 机制：证据早于 superseded_after_campaign 的 created_at → stale。"""
    root = _mini_repo(tmp_path)
    # 让 batch 证据 mtime 远早于 campaign（2000 年）
    ev = root / "runs" / "full_sweep" / "report" / "agg_batch.json"
    old = time.mktime((2000, 1, 1, 0, 0, 0, 0, 0, -1))
    os.utime(ev, (old, old))
    devs = [d.copy() for d in _DEVS]
    # 把 batch 偏差改为「已重跑 + 非 pending」，仅靠 mtime 触发
    devs[1] = {"deviation_id": "batch_diseased", "class": "superseded", "status": "closed",
               "pending_reaggregation": False,
               "superseded_after_campaign": "runs/r1_resweep/campaign_manifest.json",
               "summary": "已被 r1_resweep 重跑"}
    _write_registries(root, [_C4], devs)
    by_id = {d["claim_id"]: d for d in cc.compile_ledger(root)["claims"]}
    assert by_id["batch.os"]["status"] == cc.STATUS_STALE
    assert any("superseded_evidence" in r for r in by_id["batch.os"]["stale_reasons"])


def test_not_stale_when_evidence_newer_than_campaign(tmp_path: Path):
    """证据 mtime 晚于 campaign → 不 stale（同机制的反例，防误报）。"""
    root = _mini_repo(tmp_path)
    ev = root / "runs" / "full_sweep" / "report" / "agg_batch.json"
    future = time.mktime((2030, 1, 1, 0, 0, 0, 0, 0, -1))
    os.utime(ev, (future, future))
    devs = [{"deviation_id": "batch_diseased", "class": "superseded", "status": "closed",
             "pending_reaggregation": False,
             "superseded_after_campaign": "runs/r1_resweep/campaign_manifest.json",
             "summary": "x"}]
    _write_registries(root, [_C4], devs)
    by_id = {d["claim_id"]: d for d in cc.compile_ledger(root)["claims"]}
    assert by_id["batch.os"]["status"] != cc.STATUS_STALE


def test_missing_evidence_is_invalid_probe(tmp_path: Path):
    root = _mini_repo(tmp_path)
    (root / "runs" / "full_sweep" / "report" / "headline_stats.json").unlink()
    _write_registries(root, [_C1], _DEVS)
    by_id = {d["claim_id"]: d for d in cc.compile_ledger(root)["claims"]}
    assert by_id["contam.os_vs_naive"]["status"] == cc.STATUS_INVALID
    assert by_id["contam.os_vs_naive"]["evidence"]["exists"] is False


def test_check_passes_on_fresh_ledger(tmp_path: Path):
    root = _mini_repo(tmp_path)
    _write_registries(root, [_C1, _C3, _C4], _DEVS)
    cc._write_ledger(root, cc.compile_ledger(root))
    assert cc.run_check(root) == 0


def test_check_fails_when_evidence_deleted(tmp_path: Path):
    root = _mini_repo(tmp_path)
    _write_registries(root, [_C1, _C3, _C4], _DEVS)
    cc._write_ledger(root, cc.compile_ledger(root))
    (root / "runs" / "full_sweep" / "report" / "headline_stats.json").unlink()
    assert cc.run_check(root) == 1


def test_check_fails_on_ledger_tamper(tmp_path: Path):
    """人手改 ledger.json → 账本漂移 → 非零（sha 校验/自一致）。"""
    root = _mini_repo(tmp_path)
    _write_registries(root, [_C1, _C3, _C4], _DEVS)
    cc._write_ledger(root, cc.compile_ledger(root))
    lp = root / "claims" / "ledger.json"
    doc = json.loads(lp.read_text())
    for c in doc["claims"]:
        if c["status"] == cc.STATUS_REJECTED:
            c["status"] = cc.STATUS_SUPPORTED  # 篡改
    lp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
    assert cc.run_check(root) == 1


def test_check_fails_on_evidence_sha_change(tmp_path: Path):
    """证据内容被改（sha 变）但未重编译 → --check 非零。"""
    root = _mini_repo(tmp_path)
    _write_registries(root, [_C1], _DEVS)
    cc._write_ledger(root, cc.compile_ledger(root))
    hp = root / "runs" / "full_sweep" / "report" / "headline_stats.json"
    doc = json.loads(hp.read_text())
    doc["input_values_sha256"] = "changed"
    hp.write_text(json.dumps(doc, ensure_ascii=False))
    assert cc.run_check(root) == 1


def test_real_repo_four_statuses():
    """真实仓库编译：四主张状态如裁决所期。"""
    root = Path(__file__).resolve().parents[1]
    # R4 J-F1: also require the runs/ evidence artifact this ledger check actually
    # consumes -- a fresh clone ships claims.yaml but excludes runs/, and without
    # this guard the test fails instead of skipping, turning preflight stage 3 red.
    if not (root / "claims" / "claims.yaml").is_file() or not (
        root / "runs" / "full_sweep" / "report" / "headline_stats.json"
    ).is_file():
        pytest.skip("claims.yaml or runs/ evidence artifacts missing; skip real-repo ledger check")
    by_id = {d["claim_id"]: d for d in cc.compile_ledger(root)["claims"]}
    assert by_id["contamination_protection.S0demo.os_vs_naive"]["status"] == cc.STATUS_SUPPORTED
    assert by_id["false_optimum_rejection.S0demo.os_vs_naive"]["status"] == cc.STATUS_SUPPORTED
    assert by_id["h1_structural_regret.S2r3pool.os_vs_robust"]["status"] == cc.STATUS_REJECTED
    # Claim id/status updated when re-pinned to Gen-3 evidence (mailbox blue 022:
    # stale -> supported after the P0 batch-direction rerun and re-aggregation).
    assert by_id["batch_detection_attribution.gen3.os"]["status"] == cc.STATUS_SUPPORTED
