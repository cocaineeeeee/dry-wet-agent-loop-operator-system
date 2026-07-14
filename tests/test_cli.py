"""expos CLI v2 测试（docs/CLI_DESIGN.md）——全程 subprocess 驱动。

覆盖：
- status/verdicts/inspect 对 fixture run（rounds=1 现跑）输出含关键字段且 --json 可解析；
- override 产生 pending 文件、schema 正确，且 run 目录既有文件 mtime 全不变（零写证明）；
- domains validate 好/坏各一；
- 未知 run 目录 exit 2 干净；
- ui 命令只 smoke --help；
- 全局 --json 子命令前后两处均生效。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _cli(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "expos.cli", *args],
        cwd=str(REPO), capture_output=True, text=True, timeout=600, **kw,
    )


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory) -> Path:
    """现跑 1 轮 naive crystal 生成真实运行目录（rounds=1 省时）。"""
    out = tmp_path_factory.mktemp("cli_run") / "r1"
    proc = _cli("run", "--domain", "crystal", "--mode", "naive",
                "--rounds", "1", "--seed", "7", "--out", str(out))
    if proc.returncode != 0 or not (out / "events.jsonl").exists():
        pytest.skip(f"run 生成 fixture 失败:\n{proc.stdout}\n{proc.stderr}")
    return out


def _first_obs_id(run_dir: Path) -> str:
    return sorted((run_dir / "observations").glob("*.json"))[0].stem


# ---------------------------------------------------------------- run

def test_run_smoke_and_json(run_dir):
    proc = _cli("status", str(run_dir), "--json")
    assert proc.returncode == 0
    d = json.loads(proc.stdout)
    assert d["domain"] == "crystal" and d["mode"] == "naive"


# ---------------------------------------------------------------- status

def test_status_human_fields(run_dir):
    proc = _cli("status", str(run_dir))
    assert proc.returncode == 0, proc.stderr
    for kw in ("Run", "Rounds", "Budget", "Trust", "Best", "Overrides"):
        assert kw in proc.stdout


def test_status_json_parses(run_dir):
    proc = _cli("status", str(run_dir), "--json")
    assert proc.returncode == 0
    d = json.loads(proc.stdout)
    for key in ("run_dir", "rounds_completed", "budget", "trust", "best", "overrides"):
        assert key in d
    assert d["trust"]["TRUSTED"] > 0


# ---------------------------------------------------------------- verdicts

def test_verdicts_human_and_json(run_dir):
    proc = _cli("verdicts", str(run_dir))
    assert proc.returncode == 0, proc.stderr
    for col in ("obs_id", "round", "well", "trust", "suspicion",
                "top_cause", "next_action"):
        assert col in proc.stdout

    pj = _cli("verdicts", str(run_dir), "--json")
    assert pj.returncode == 0
    d = json.loads(pj.stdout)
    assert d["n_total"] > 0 and len(d["verdicts"]) == d["n_shown"]
    rec = d["verdicts"][0]
    for key in ("obs_id", "round", "well", "trust", "suspicion",
                "top_cause", "next_action"):
        assert key in rec


def test_verdicts_trust_filter(run_dir):
    proc = _cli("verdicts", str(run_dir), "--trust", "trusted", "--json")
    assert proc.returncode == 0
    d = json.loads(proc.stdout)
    assert all(v["trust"] == "TRUSTED" for v in d["verdicts"])


# ---------------------------------------------------------------- inspect

def test_inspect_events(run_dir):
    proc = _cli("inspect", str(run_dir), "events", "--tail", "5")
    assert proc.returncode == 0, proc.stderr
    assert "seq" in proc.stdout and "kind" in proc.stdout

    pj = _cli("inspect", str(run_dir), "events", "--json")
    assert pj.returncode == 0
    lines = [ln for ln in pj.stdout.splitlines() if ln.strip()]
    recs = [json.loads(ln) for ln in lines]  # JSON Lines 逐行可解析
    assert recs and all("kind" in r and "seq" in r for r in recs)


def test_inspect_obs(run_dir):
    obs_id = _first_obs_id(run_dir)
    proc = _cli("inspect", str(run_dir), "obs", obs_id)
    assert proc.returncode == 0, proc.stderr
    assert obs_id in proc.stdout and "trust" in proc.stdout

    pj = _cli("inspect", str(run_dir), "obs", obs_id, "--json")
    assert pj.returncode == 0
    d = json.loads(pj.stdout)
    assert d["obs_id"] == obs_id


def test_inspect_exp(run_dir):
    proc = _cli("inspect", str(run_dir), "exp", "0", "--json")
    assert proc.returncode == 0, proc.stderr
    d = json.loads(proc.stdout)
    assert d["round_id"] == 0 and "provenance" in d


def test_inspect_obs_missing_exit2(run_dir):
    proc = _cli("inspect", str(run_dir), "obs", "obs_doesnotexist")
    assert proc.returncode == 2
    assert "不存在" in proc.stderr


# ---------------------------------------------------------------- override（零写证明）

def _mtimes(run_dir: Path) -> dict[str, float]:
    return {str(p): p.stat().st_mtime
            for p in run_dir.rglob("*") if p.is_file()}


def test_override_writes_pending_only(run_dir):
    obs_id = _first_obs_id(run_dir)
    before = _mtimes(run_dir)

    proc = _cli("override", str(run_dir), "--obs", obs_id,
                "--trust", "suspect", "--routing", "QUARANTINE",
                "--reason", "哨兵证实为反光误报", "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)

    pending = run_dir / "overrides" / "pending"
    files = list(pending.glob("*.json"))
    assert len(files) == 1
    prop = json.loads(files[0].read_text(encoding="utf-8"))
    # schema 正确
    assert prop["obs_id"] == obs_id
    assert prop["to_trust"] == "SUSPECT"
    assert prop["to_routing"] == "QUARANTINE"
    assert prop["reason"] == "哨兵证实为反光误报"
    assert prop["actor"] == "human"
    assert "base_version" in prop and "created_at" in prop
    assert payload["proposal"]["obs_id"] == obs_id

    # 零写证明：run 目录既有文件 mtime 全不变（override 绝不触碰 store）。
    after = _mtimes(run_dir)
    for path, mt in before.items():
        assert after.get(path) == mt, f"既有文件被改动: {path}"


def test_override_missing_obs_exit2(run_dir):
    proc = _cli("override", str(run_dir), "--obs", "obs_nope",
                "--trust", "failed", "--reason", "x")
    assert proc.returncode == 2
    assert "不存在" in proc.stderr


# ---------------------------------------------------------------- domains

def test_domains_validate_good():
    proc = _cli("domains", "validate", str(REPO / "domains" / "crystal.yaml"))
    assert proc.returncode == 0, proc.stderr
    assert "OK crystal" in proc.stdout

    pj = _cli("domains", "validate", str(REPO / "domains" / "crystal.yaml"), "--json")
    d = json.loads(pj.stdout)
    assert d["ok"] is True and d["name"] == "crystal"


def test_domains_validate_bad(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\nadapter: no_such_adapter\n", encoding="utf-8")
    proc = _cli("domains", "validate", str(bad))
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""  # 领域错误走 stderr，stdout 干净


# ---------------------------------------------------------------- 未知 run 目录

def test_unknown_run_dir_exit2(tmp_path):
    missing = tmp_path / "no_such_run"
    proc = _cli("status", str(missing))
    assert proc.returncode == 2
    assert "不存在" in proc.stderr
    assert "Traceback" not in proc.stderr  # 领域错误干净退出，不吐 traceback


# ---------------------------------------------------------------- 全局 --json 两处

def test_global_json_before_subcommand(run_dir):
    proc = _cli("--json", "status", str(run_dir))
    assert proc.returncode == 0
    json.loads(proc.stdout)  # 子命令前给 --json 也生效


# ---------------------------------------------------------------- ui smoke

def test_ui_help_smoke():
    proc = _cli("ui", "--help")
    assert proc.returncode == 0
    assert "streamlit" in proc.stdout.lower() or "runs-root" in proc.stdout


# ---------------------------------------------------------------- check（尾损诊断 + 自愈）

import shutil  # noqa: E402


def _copy_run(run_dir: Path, dest_root: Path) -> Path:
    dest = dest_root / "run_copy"
    shutil.copytree(run_dir, dest)
    return dest


def test_check_clean_exit0(run_dir):
    proc = _cli("check", str(run_dir))
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


def test_check_half_line_diagnose_then_fix(run_dir, tmp_path):
    """半行截断：默认只诊断 exit 1（可修尾损 + 指引）；--fix --yes → 截断自愈 exit 0、留 .pre_fix。"""
    run = _copy_run(run_dir, tmp_path)
    ev = run / "events.jsonl"
    watermark = ev.stat().st_size
    with ev.open("a", encoding="utf-8") as f:
        f.write('{"seq": 999, "kind": "routing", "payl')

    diag = _cli("check", str(run))
    assert diag.returncode == 1  # 诊断-only，可修尾损
    assert "truncated" in diag.stdout

    fixed = _cli("check", str(run), "--fix", "--yes")
    assert fixed.returncode == 0, fixed.stderr
    assert ev.stat().st_size == watermark  # 截回水位
    assert (run / "events.jsonl.pre_fix").exists()  # 原文件已备份


def test_check_last_line_bad_json_diagnosed(run_dir, tmp_path):
    """末行坏 JSON 且其后直达 EOF → 可修尾损，默认诊断 exit 1。"""
    run = _copy_run(run_dir, tmp_path)
    ev = run / "events.jsonl"
    with ev.open("a", encoding="utf-8") as f:
        f.write("{not valid json}\n")
    proc = _cli("check", str(run))
    assert proc.returncode == 1
    assert "truncated" in proc.stdout


def test_check_mid_corruption_loud_exit3_never_fixed(run_dir, tmp_path):
    """中段坏行 → CorruptedRun 响亮 exit 3，且 --fix 也绝不截断（结构性拒修）。"""
    run = _copy_run(run_dir, tmp_path)
    ev = run / "events.jsonl"
    lines = ev.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    lines[0] = "{broken first line}"  # 首行坏，后仍有非空行 → 中段损坏
    ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = ev.read_bytes()

    proc = _cli("check", str(run), "--fix", "--yes")
    assert proc.returncode == 3
    assert "CorruptedRun" in proc.stderr
    assert ev.read_bytes() == before  # 绝不修改
    assert not (run / "events.jsonl.pre_fix").exists()
