"""scripts/plot_run.py 的端到端测试。

流程：现跑一个 1 轮小 run（run_loop，rounds=1 seed=3）→ subprocess 跑 plot_run →
断言三产物存在、png 非空、summary 数字与 RunStore 一致；坏目录 exit 2。
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from expos.kernel.store import RunStore  # noqa: E402

RUN_LOOP = ROOT / "scripts" / "run_loop.py"
PLOT_RUN = ROOT / "scripts" / "plot_run.py"


@pytest.fixture
def small_run(tmp_path: Path) -> Path:
    """现跑 1 轮 naive run（seed=3）到 tmp_path，返回运行目录。"""
    run_dir = tmp_path / "run"
    r = subprocess.run(
        [sys.executable, str(RUN_LOOP), "--domain", "crystal", "--mode", "naive",
         "--rounds", "1", "--seed", "3", "--out", str(run_dir)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"run_loop 失败:\n{r.stderr}"
    assert run_dir.is_dir()
    return run_dir


def test_produces_three_artifacts(small_run: Path, tmp_path: Path):
    out = tmp_path / "report"
    r = subprocess.run(
        [sys.executable, str(PLOT_RUN), str(small_run), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"plot_run 失败:\n{r.stderr}"

    store = RunStore(small_run, create=False)
    obs = store.list_observations()
    rounds = sorted({o.round_id for o in obs})

    loop_png = out / "loop.png"
    summary = out / "summary.txt"
    plate_pngs = [out / f"plate_round{k}.png" for k in rounds]

    # 三类产物齐全
    assert loop_png.is_file()
    assert summary.is_file()
    for p in plate_pngs:
        assert p.is_file(), f"缺孔板图 {p.name}"

    # png 非空且是真 PNG（magic number）
    for p in [loop_png, *plate_pngs]:
        data = p.read_bytes()
        assert len(data) > 0
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{p.name} 不是有效 PNG"

    # summary 数字与 store 一致
    text = summary.read_text(encoding="utf-8")

    def _int_after(label: str) -> int:
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(label))
        return int(line.split(":", 1)[1].split()[0].split("(")[0])

    assert _int_after("观测总数") == len(obs)
    assert _int_after("轮数") == len(rounds)

    # 逐轮 TRUSTED 计数与 store 一致
    per_round = Counter(o.round_id for o in obs if o.trust.value == "TRUSTED")
    for k in rounds:
        assert f"round {k}: TRUSTED={per_round[k]}" in text


def test_default_out_is_run_report_dir(small_run: Path):
    """不给 --out 时默认写进 <run_dir>/report/。"""
    r = subprocess.run(
        [sys.executable, str(PLOT_RUN), str(small_run)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (small_run / "report" / "loop.png").is_file()
    assert (small_run / "report" / "plate_round0.png").is_file()
    assert (small_run / "report" / "summary.txt").is_file()


def test_missing_dir_exits_2(tmp_path: Path):
    r = subprocess.run(
        [sys.executable, str(PLOT_RUN), str(tmp_path / "does_not_exist")],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "plot error" in r.stderr


def test_empty_run_exits_2(tmp_path: Path):
    empty = tmp_path / "empty"
    (empty / "observations").mkdir(parents=True)
    r = subprocess.run(
        [sys.executable, str(PLOT_RUN), str(empty)],
        capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "plot error" in r.stderr
