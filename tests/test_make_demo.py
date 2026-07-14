"""M10 三幕 demo 脚本验收（scripts/make_demo.py）。

快速档（--rounds 2 --seeds 1）跑通三幕、产物齐全非空、第三幕含"拒绝"、幂等（不重算）、
解说数值与实跑 summary/事后评分一致（R1-6 防叙事漂移）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_demo  # noqa: E402


@pytest.fixture(scope="module")
def demo_out(tmp_path_factory):
    """快速档跑一次三幕（module 级共享，避免重复 GP 训练）。"""
    out = tmp_path_factory.mktemp("demo") / "runs_demo"
    result = make_demo.make_demo(out, rounds=2, n_seeds=1, base_seed=7)
    return out, result


def test_three_acts_products_exist(demo_out):
    out, result = demo_out

    # 第一幕：compare.png 非空 + 双臂 run 目录存在
    compare = out / "act1" / "compare.png"
    assert compare.is_file() and compare.stat().st_size > 0, "compare.png 缺失或空"
    assert (out / "runs" / "act1_crystal_naive_s7" / "checkpoint.json").exists()
    assert (out / "runs" / "act1_crystal_os_s7" / "checkpoint.json").exists()

    # 解说非空且引到真实数字
    narrative = out / "demo_narrative.md"
    assert narrative.is_file()
    text = narrative.read_text(encoding="utf-8")
    assert len(text.strip()) > 0, "demo_narrative.md 为空"
    assert "第一幕" in text and "第二幕" in text and "第三幕" in text
    assert str(result["naive_best"]) in text  # naive 假最优值确实写进解说

    # 第二幕：热插拔 coating loop.png 非空
    act2_png = out / "act2" / "loop.png"
    assert act2_png.is_file() and act2_png.stat().st_size > 0, "act2 loop.png 缺失或空"

    # 第三幕：边界即类型证据含"拒绝"
    act3 = out / "act3" / "boundary_demo.txt"
    assert act3.is_file()
    a3 = act3.read_text(encoding="utf-8")
    assert "拒绝" in a3, "第三幕输出未含『拒绝』字样"
    assert "LifecycleError" in a3  # 伪造尝试 A 确被类型层拦截


def test_narrative_matches_run_summary(demo_out):
    """R1-6 防再漂移：narrative 的数值断言必须与实跑 summary/事后评分一致——
    "越过上限"措辞只允许在实跑 naive_best > 1.0 时出现，否则必须走
    假最优命中（引 measured/true 真实数字）或"未出现命中"分支。"""
    out, result = demo_out
    text = (out / "demo_narrative.md").read_text(encoding="utf-8")
    naive_best, os_best = result["naive_best"], result["os_best"]
    naive_fake = result["naive_fake"]

    # 双臂 best 原样写进解说
    assert str(naive_best) in text
    assert str(os_best) in text

    if naive_best is not None and naive_best > make_demo.PHYSICAL_CEILING:
        assert "越过真值面物理上限" in text
    else:
        assert "越过真值面物理上限" not in text, "narrative 谎称越上限（实跑未越）"
        if naive_fake["hit_any"]:
            # 假最优命中分支：必须引事后评分的 measured/true 真实数字
            assert "推荐点的测量值被伪影抬高超真值 3σ" in text
            assert f"`{naive_fake['measured']:.4f}`" in text
            assert f"`{naive_fake['true']:.4f}`" in text
        else:
            assert "未出现假最优命中" in text

    # 隔离数与两臂差值同样来自实跑
    assert f"`{result['facts']['n_quarantined']}`" in text
    if naive_best is not None and os_best is not None:
        assert f"`{naive_best - os_best:.4f}`" in text


def _round_designed_count(run_dir: Path) -> int:
    sys.path.insert(0, str(ROOT))
    from expos.kernel.store import RunStore
    return len(RunStore(run_dir, create=False).read_events("round_designed"))


def test_idempotent_no_recompute(demo_out, tmp_path):
    """幂等：同 --out 再跑一次不重算既有轮次（round_designed 事件数不增）。"""
    out, _ = demo_out
    os_dir = out / "runs" / "act1_crystal_os_s7"
    before = _round_designed_count(os_dir)
    assert before == 2  # 首跑 2 轮

    result2 = make_demo.make_demo(out, rounds=2, n_seeds=1, base_seed=7)
    after = _round_designed_count(os_dir)
    assert after == before, "幂等被破坏：既有轮次被重算"

    # 产物仍在
    assert (out / "act1" / "compare.png").stat().st_size > 0
    assert (out / "demo_narrative.md").read_text(encoding="utf-8").strip()
    assert result2["os_best"] is not None
