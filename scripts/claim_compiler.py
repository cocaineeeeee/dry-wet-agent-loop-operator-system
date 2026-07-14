#!/usr/bin/env python3
"""claim_compiler —— 主张到证据的机器可查账本（Claim Compiler / Claim Ledger）。

用户架构裁决 P0 第 2 条落地：把 README/PAPER/CHECKPOINTS 里手抄、逐版本漂移的
headline 数字，升级为「单一事实源 + 机器可校」的 ClaimDecision 账本。

血缘 schema 沿用 docs/RUN_MANIFEST_SPEC.md §9（主张→判定函数→stats 行→cells 集→
代码指纹五级链），本脚本是其「一次编译的血缘快照」侧落地，不复述 schema。

输入（人写 + 产物）：
  * claims/claims.yaml       —— 人写主张登记（claim_id / 文本 / 判定函数名 / 证据 glob /
                                预期方向 / 代际标签）。
  * claims/deviations.yaml   —— CHECKPOINTS 压测更正记录的机器可读镜像（偏差登记）。
  * 各 report 产物           —— headline_stats.json / aggregate_summary.json / …（证据）。
  * campaign_manifest.json   —— 代际 / cells sha / 代码指纹 / supersedes_report（stale 依据）。

输出：
  * claims/ledger.json       —— 每主张一条 ClaimDecision（机器生成，勿手改）。

状态集（§9 + 裁决扩展）：
    supported / rejected / partially_supported / invalid_probe / superseded / stale

用法：
    python3 scripts/claim_compiler.py            # 编译并落盘 claims/ledger.json
    python3 scripts/claim_compiler.py --check    # 门禁：证据缺失/stale/账本漂移 → exit 1
    python3 scripts/claim_compiler.py --root /path/to/repo   # 指定仓库根（测试用）

零手改纪律（红队护栏 021 §2）：ledger.json 每条 ClaimDecision 由本编译器从 artifact
指纹重算得出，绝不允许人手直接编辑。--check 会重算并与盘上账本比对，任何手改 →
「账本漂移」非零退出。散文（README/PAPER）只转引 ledger 状态，勿再抄数字。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Ensure the repo root is importable so the shared decision_fn registry (M17 K-A)
# resolves whether this script is run standalone (`python scripts/claim_compiler.py`,
# sys.path[0]=scripts/) or via an editable install / pytest conftest.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# M17 K-A: the decision_fn REGISTRY is the single membership authority shared by
# this offline compiler and the run-internal online path (expos.kernel.claims).
# Registering the offline verdict here means the online governance gate recognises
# it too — the online path does not bypass offline decision_fn governance.
from expos.kernel.claims import register_decision_fn  # noqa: E402

# ============================================================
# 常量
# ============================================================
STATUS_SUPPORTED = "supported"
STATUS_REJECTED = "rejected"
STATUS_PARTIAL = "partially_supported"
STATUS_INVALID = "invalid_probe"
STATUS_SUPERSEDED = "superseded"
STATUS_STALE = "stale"
VALID_STATUS = frozenset({
    STATUS_SUPPORTED, STATUS_REJECTED, STATUS_PARTIAL,
    STATUS_INVALID, STATUS_SUPERSEDED, STATUS_STALE,
})

LEDGER_WARNING = (
    "机器生成，请勿手改。修改 claims/claims.yaml 或 claims/deviations.yaml 后重新运行 "
    "`python3 scripts/claim_compiler.py` 重编译。手改会被 `--check` 侦测为账本漂移并非零退出。"
)

# compiled_at 是易变时间戳，比对账本一致性时排除。
_VOLATILE_TOP_KEYS = ("compiled_at",)
_VOLATILE_CLAIM_KEYS = ("compiled_at",)


# ============================================================
# 基础工具
# ============================================================
def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(p: Path) -> str | None:
    try:
        return _sha256_bytes(p.read_bytes())
    except OSError:
        return None


def _mtime_utc(p: Path) -> str | None:
    try:
        ts = p.stat().st_mtime
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()


def _now_utc() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _load_yaml(p: Path) -> dict[str, Any]:
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{p} 顶层必须是映射，实为 {type(data).__name__}")
    return data


def _parse_iso(s: str) -> _dt.datetime | None:
    if not s:
        return None
    txt = s.replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _dig(obj: Any, dotted: str) -> Any:
    """按点路径取值：a.b.c；命中数组用整数索引段。找不到返回 None。"""
    cur = obj
    for seg in dotted.split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        elif isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


# ============================================================
# 证据抽取
# ============================================================
def _extract_evidence(doc: Any, selector: dict[str, Any]) -> dict[str, Any]:
    """按 selector 从证据文档抽 p 值 / 效应量 / cells sha。

    selector.kind:
      * path         —— 点路径直取（p_field / diff_field / value_field / cells_sha_field）。
      * array_match  —— 在 array_key 数组中按 match 字典找元素，再取字段。
    """
    kind = selector.get("kind", "path")
    out: dict[str, Any] = {"p_value": None, "effect": None, "cells_sha256": None}

    if kind == "array_match":
        arr = _dig(doc, selector["array_key"])
        match = selector.get("match", {})
        chosen = None
        if isinstance(arr, list):
            for el in arr:
                if isinstance(el, dict) and all(el.get(k) == v for k, v in match.items()):
                    chosen = el
                    break
        if chosen is None:
            out["extract_error"] = f"array_match 未命中 {match}"
            return out
        if selector.get("p_field"):
            out["p_value"] = chosen.get(selector["p_field"])
        if selector.get("diff_field"):
            out["effect"] = chosen.get(selector["diff_field"])
        if selector.get("value_field"):
            out["effect"] = chosen.get(selector["value_field"])
    elif kind == "path":
        if selector.get("p_field"):
            out["p_value"] = _dig(doc, selector["p_field"])
        if selector.get("diff_field"):
            out["effect"] = _dig(doc, selector["diff_field"])
        if selector.get("value_field"):
            out["effect"] = _dig(doc, selector["value_field"])
    else:
        out["extract_error"] = f"未知 selector.kind={kind!r}"
        return out

    # cells sha（best-effort，选证据文档内字段，如 headline input_values_sha256）
    if selector.get("cells_sha_field"):
        out["cells_sha256"] = _dig(doc, selector["cells_sha_field"])
    return out


# ============================================================
# 判定函数注册表（§9：按名引用，禁闭包）
# ============================================================
def _favorable(effect: float, direction: str) -> bool:
    if direction == "negative":
        return effect < 0
    if direction == "positive":
        return effect > 0
    raise ValueError(f"favorable_direction 须为 negative/positive，得 {direction!r}")


@register_decision_fn("paired_significance_verdict", "1")
def decision_paired_significance(
    p_value: float | None, effect: float | None, alpha: float, favorable_direction: str
) -> tuple[str, str]:
    """配对显著性裁定（用于 superiority 类主张）。

    返回 (status, reason)：
      * p<=alpha 且方向有利   → supported（显著优于对照）
      * p<=alpha 且方向不利   → rejected（显著劣于对照 = 预注册被证否）
      * p>alpha              → partially_supported（未达显著，不能主张优势）
      * p / effect 缺失       → invalid_probe
    """
    if p_value is None or effect is None:
        return STATUS_INVALID, "证据缺 p 值或效应量，无法机器裁定"
    fav = _favorable(float(effect), favorable_direction)
    sig = float(p_value) <= alpha
    if sig and fav:
        return STATUS_SUPPORTED, f"p={p_value:g}<=α={alpha} 且方向有利（effect={effect:g}）"
    if sig and not fav:
        return STATUS_REJECTED, f"p={p_value:g}<=α={alpha} 但方向不利（effect={effect:g}）→ 预注册被证否"
    return STATUS_PARTIAL, f"p={p_value:g}>α={alpha}，未达显著，不能主张优势"


DECISION_FNS = {
    "paired_significance_verdict": decision_paired_significance,
}


# ============================================================
# campaign / deviation 索引
# ============================================================
def _load_campaigns(root: Path) -> list[dict[str, Any]]:
    """扫描 runs/ 下所有 campaign_manifest.json，供 stale 判定用。"""
    out: list[dict[str, Any]] = []
    runs = root / "runs"
    if not runs.is_dir():
        return out
    for p in sorted(runs.rglob("campaign_manifest.json")):
        try:
            with p.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        doc["_manifest_path"] = str(p.relative_to(root))
        out.append(doc)
    return out


def _index_deviations(dev_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for d in dev_doc.get("deviations", []):
        idx[d["deviation_id"]] = d
    return idx


def _campaign_by_path(campaigns: list[dict[str, Any]], manifest_path: str) -> dict[str, Any] | None:
    for camp in campaigns:
        if camp.get("_manifest_path") == manifest_path:
            return camp
    return None


def _stale_reasons(
    claim: dict[str, Any],
    evidence_mtime: str | None,
    campaigns: list[dict[str, Any]],
    dev_index: dict[str, dict[str, Any]],
) -> list[str]:
    """计算 stale 触发原因（可多条）。均由主张显式引用的 deviation 驱动，
    避免目录级 supersedes_report 的粗粒度误判（同 report 里已刷新的 claim 不应受连累）。

    触发 (b) pending_reaggregation：引用的 deviation 处于 open 且 pending_reaggregation
        —— 数据带病、待新代际重聚合（如 batch 方向修复后的 Gen-3）。
    触发 (a) superseded_after_campaign（mtime/sha 机制）：引用的 deviation 声明
        superseded_after_campaign=<manifest 路径>；若证据 artifact 的 mtime 早于该
        campaign 的 created_at → 证据是重跑前的旧产物，未刷新 → 「旧 report 讲旧 claim」。
    """
    reasons: list[str] = []
    ev_dt = _parse_iso(evidence_mtime) if evidence_mtime else None

    for dev_id in claim.get("deviations", []):
        dev = dev_index.get(dev_id)
        if not dev:
            continue
        # (b) 待重聚合
        if dev.get("status") == "open" and dev.get("pending_reaggregation"):
            reasons.append(
                f"pending_reaggregation: deviation {dev_id} 数据带病，待 "
                f"{dev.get('pending_generation', '新代际')} 重聚合"
            )
        # (a) 证据早于其数据源重跑（对比 campaign manifest 的 mtime/sha 机制）
        camp_path = dev.get("superseded_after_campaign")
        if camp_path:
            camp = _campaign_by_path(campaigns, camp_path)
            camp_dt = _parse_iso(camp.get("created_at", "")) if camp else None
            if ev_dt is not None and camp_dt is not None and ev_dt < camp_dt:
                reasons.append(
                    f"superseded_evidence: deviation {dev_id} 记 campaign "
                    f"{camp.get('campaign_id')}（{camp.get('created_at')}）已重跑数据源，"
                    f"证据 mtime={evidence_mtime} 更旧未刷新"
                )
    return reasons


# ============================================================
# 单主张编译
# ============================================================
def _compile_claim(
    claim: dict[str, Any],
    root: Path,
    alpha_default: float,
    campaigns: list[dict[str, Any]],
    dev_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ev_spec = claim["evidence"]
    ev_rel = ev_spec["source_file"]
    ev_path = root / ev_rel
    ev_exists = ev_path.is_file()
    ev_sha = _sha256_file(ev_path) if ev_exists else None
    ev_mtime = _mtime_utc(ev_path) if ev_exists else None

    extracted: dict[str, Any] = {"p_value": None, "effect": None, "cells_sha256": None}
    if ev_exists and ev_spec.get("selector"):
        try:
            with ev_path.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
            extracted = _extract_evidence(doc, ev_spec["selector"])
        except (OSError, json.JSONDecodeError) as exc:
            extracted["extract_error"] = f"读取/解析证据失败：{exc}"

    alpha = float(claim.get("alpha", alpha_default))
    direction = claim.get("favorable_direction", "negative")
    fn_name = claim.get("decision_fn", "paired_significance_verdict")

    # 1) 基础机器裁定（数据侧）。
    if not ev_exists:
        status, reason = STATUS_INVALID, f"证据文件缺失：{ev_rel}"
    elif "extract_error" in extracted:
        status, reason = STATUS_INVALID, extracted["extract_error"]
    else:
        fn = DECISION_FNS.get(fn_name)
        if fn is None:
            status, reason = STATUS_INVALID, f"未注册判定函数 {fn_name!r}"
        else:
            status, reason = fn(extracted["p_value"], extracted["effect"], alpha, direction)

    # 2) stale 覆盖（数据带病/被取代优先于数据侧裁定）。
    stale = _stale_reasons(claim, ev_mtime, campaigns, dev_index)
    if stale:
        status, reason = STATUS_STALE, "；".join(stale)

    # 3) 关联偏差（机器可读镜像）。
    dev_records = []
    for dev_id in claim.get("deviations", []):
        dev = dev_index.get(dev_id)
        if dev:
            dev_records.append({
                "deviation_id": dev_id,
                "class": dev.get("class"),
                "status": dev.get("status"),
                "pending_reaggregation": bool(dev.get("pending_reaggregation")),
                "summary": dev.get("summary"),
                "recorded_in": dev.get("recorded_in", []),
                "checkpoints_ref": dev.get("checkpoints_ref"),
            })

    # 4) campaign 血缘链接（cells sha / 代码指纹）。
    campaign_link = None
    camp_path = claim.get("campaign_manifest")
    if camp_path:
        for camp in campaigns:
            if camp.get("_manifest_path") == camp_path:
                campaign_link = {
                    "campaign_id": camp.get("campaign_id"),
                    "manifest_path": camp_path,
                    "cells_sha256": _dig(camp, "grid.cells_sha256"),
                    "n_cells": _dig(camp, "grid.n_cells"),
                    "manifest_sha256_ref": _dig(camp, "code_fingerprint.manifest_sha256_ref"),
                }
                break

    assert status in VALID_STATUS, f"非法 status {status!r}"

    return {
        "claim_id": claim["claim_id"],
        "text": claim.get("text", ""),
        "status": status,
        "reason": reason,
        "claim_kind": claim.get("claim_kind"),
        "generation": claim.get("generation"),
        "domain": claim.get("domain"),
        "decision_fn": fn_name,
        "test_spec": {"alpha": alpha, "favorable_direction": direction},
        "evidence": {
            "source_file": ev_rel,
            "exists": ev_exists,
            "sha256": ev_sha,
            "mtime_utc": ev_mtime,
            "p_value": extracted.get("p_value"),
            "effect": extracted.get("effect"),
            "cells_sha256": extracted.get("cells_sha256"),
            "campaign": campaign_link,
        },
        "deviations": dev_records,
        "stale_reasons": stale,
    }


# ============================================================
# 编译器指纹与账本组装
# ============================================================
def _compiler_fingerprint(root: Path, claims_path: Path, dev_path: Path) -> str:
    """指纹 = 编译器源码 + 两份登记文件的内容并集 sha256（任一变更即换指纹）。"""
    h = hashlib.sha256()
    for p in (Path(__file__), claims_path, dev_path):
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<missing>")
        h.update(b"\x00")
    return "sha256:" + h.hexdigest()


def compile_ledger(root: Path) -> dict[str, Any]:
    claims_path = root / "claims" / "claims.yaml"
    dev_path = root / "claims" / "deviations.yaml"
    claims_doc = _load_yaml(claims_path)
    dev_doc = _load_yaml(dev_path)

    alpha_default = float(claims_doc.get("alpha_default", 0.05))
    campaigns = _load_campaigns(root)
    dev_index = _index_deviations(dev_doc)
    fingerprint = _compiler_fingerprint(root, claims_path, dev_path)
    now = _now_utc()

    decisions = []
    for claim in claims_doc.get("claims", []):
        dec = _compile_claim(claim, root, alpha_default, campaigns, dev_index)
        dec["compiled_at"] = now
        dec["compiler_fingerprint"] = fingerprint
        decisions.append(dec)

    return {
        "_WARNING": LEDGER_WARNING,
        "ledger_version": 1,
        "compiled_at": now,
        "compiler_fingerprint": fingerprint,
        "inputs": {
            "claims_yaml": {"path": "claims/claims.yaml", "sha256": _sha256_file(claims_path)},
            "deviations_yaml": {"path": "claims/deviations.yaml", "sha256": _sha256_file(dev_path)},
        },
        "status_counts": _status_counts(decisions),
        "claims": decisions,
    }


def _status_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    return counts


def _strip_volatile(ledger: dict[str, Any]) -> dict[str, Any]:
    """去掉易变字段（时间戳），供账本漂移比对。"""
    clone = json.loads(json.dumps(ledger, sort_keys=True))
    for k in _VOLATILE_TOP_KEYS:
        clone.pop(k, None)
    for d in clone.get("claims", []):
        for k in _VOLATILE_CLAIM_KEYS:
            d.pop(k, None)
    return clone


# ============================================================
# CLI
# ============================================================
def _write_ledger(root: Path, ledger: dict[str, Any]) -> Path:
    out = root / "claims" / "ledger.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return out


def run_check(root: Path) -> int:
    """门禁：证据缺失/stale/账本漂移 → 非零。"""
    ledger_path = root / "claims" / "ledger.json"
    problems: list[str] = []

    fresh = compile_ledger(root)

    if not ledger_path.is_file():
        print("✗ claims/ledger.json 不存在——先运行 `python3 scripts/claim_compiler.py` 编译。")
        return 1
    try:
        with ledger_path.open("r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ 读取 ledger.json 失败：{exc}")
        return 1

    # (1) 账本漂移：盘上账本必须与重算逐字段一致（排除时间戳）——防人手直改。
    if _strip_volatile(stored) != _strip_volatile(fresh):
        problems.append(
            "账本漂移：claims/ledger.json 与从 artifact 重算结果不一致——"
            "疑被手改或证据/登记已变；请重新运行 `python3 scripts/claim_compiler.py` 重编译。"
        )

    # (2) 逐主张证据健全性（以重算结果为准）。
    for dec in fresh["claims"]:
        cid = dec["claim_id"]
        ev = dec["evidence"]
        if not ev["exists"]:
            problems.append(f"[{cid}] 证据文件缺失：{ev['source_file']}")
            continue
        # sha 校验：盘上账本记录的 sha 必须与当前证据 sha 一致。
        stored_dec = next((d for d in stored.get("claims", []) if d.get("claim_id") == cid), None)
        if stored_dec and stored_dec.get("evidence", {}).get("sha256") != ev["sha256"]:
            problems.append(
                f"[{cid}] 证据 sha 变更：账本记 {stored_dec.get('evidence', {}).get('sha256')} "
                f"≠ 现算 {ev['sha256']}（证据被改动，需重编译）"
            )
        if dec["status"] == STATUS_SUPPORTED and dec["stale_reasons"]:
            problems.append(f"[{cid}] supported 主张却 stale：{dec['stale_reasons']}")

    if problems:
        print("✗ claim ledger --check 失败：")
        for p in problems:
            print(f"  - {p}")
        return 1

    counts = fresh["status_counts"]
    print(f"✓ claim ledger --check 通过（{len(fresh['claims'])} 主张，状态分布 {counts}）")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Claim Compiler / Claim Ledger（主张到证据的机器可查账本）")
    ap.add_argument("--check", action="store_true", help="门禁模式：证据缺失/stale/账本漂移 → 非零退出")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    if args.check:
        return run_check(root)

    ledger = compile_ledger(root)
    out = _write_ledger(root, ledger)
    counts = ledger["status_counts"]
    print(f"✓ 已编译 {len(ledger['claims'])} 主张 → {out.relative_to(root)}（状态分布 {counts}）")
    for d in ledger["claims"]:
        print(f"  · {d['claim_id']:<48} {d['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
