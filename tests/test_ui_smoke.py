"""多页只读 UI 冒烟测试（族6 §3：st.Page/st.navigation 四页 IA）。

策略：
- 用 fixture 现跑生成 tmp run 目录（rounds 少省时）。
- 用 streamlit.testing.v1.AppTest 对**每个页面文件**做冒烟：断言无异常、关键控件/图表
  存在；并覆盖入口 app.py 的 st.navigation。
- 只读红线自查：以静态源码扫描断言 ui/ 下无写句柄、无 store 变更调用、不读 truth/。
- 若本机 streamlit 版本 AppTest 不可用，退化为编译检查 + 纯函数单测，并如实标注。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "ui"
APP = UI / "app.py"
PAGES = {
    "overview": UI / "pg_overview.py",
    "board": UI / "pg_board.py",
    "decisions": UI / "pg_decisions.py",
    "compare": UI / "pg_compare.py",
}
UI_FILES = [APP, UI / "_common.py", *PAGES.values()]

_HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None
_HAS_APPTEST = False
if _HAS_STREAMLIT:
    _HAS_APPTEST = importlib.util.find_spec("streamlit.testing.v1") is not None


# ---------------------------------------------------------------- fixtures


def _gen_run(out: Path, rounds: int) -> Path:
    proc = subprocess.run(
        [
            sys.executable, str(REPO / "scripts" / "run_loop.py"),
            "--domain", "crystal", "--mode", "naive",
            "--rounds", str(rounds), "--seed", "7", "--out", str(out),
        ],
        cwd=str(REPO), capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0 or not (out / "events.jsonl").exists():
        pytest.skip(f"run_loop.py 生成 dev run 失败：\n{proc.stdout}\n{proc.stderr}")
    return out


@pytest.fixture(scope="module")
def dev_run(tmp_path_factory) -> Path:
    return _gen_run(tmp_path_factory.mktemp("ui_run") / "r1", rounds=1)


@pytest.fixture(scope="module")
def dev_run_multi(tmp_path_factory) -> Path:
    return _gen_run(tmp_path_factory.mktemp("ui_run_multi") / "r3", rounds=3)


# ---------------------------------------------------------------- 只读红线自查
# 静态扫描 ui/ 全部源码：不得出现写句柄 / store 变更调用 / 读 truth/。


def test_readonly_redline_static_scan():
    # 变更类 API / 写句柄的**调用形态**（带 . 前缀 + ( 后缀，避免误伤注释里的名词）。
    forbidden_calls = [
        ".save_experiment(", ".save_observation(", ".save_config(", ".save_truth(",
        ".append_event(", ".append_decision(", ".write_checkpoint(",
        "._atomic_write", ".mkdir(", "open(", ".write_text(", ".write_bytes(",
        "create=True",
    ]
    for f in UI_FILES:
        src = f.read_text(encoding="utf-8")
        # RunStore 必须以 create=False 打开。
        assert "create=False" in src or "RunStore(" not in src, f
        for bad in forbidden_calls:
            assert bad not in src, f"{f} 触碰只读红线：{bad}"
        # 绝不以路径字面量访问 truth/ 目录（注释里作为红线名词提及不算违规）。
        assert '"truth"' not in src and "'truth'" not in src, f"{f} 不得访问 truth/"


def test_app_files_compile():
    for f in UI_FILES:
        proc = subprocess.run(
            [sys.executable, "-c",
             f"import py_compile; py_compile.compile(r'{f}', doraise=True)"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"{f} 编译失败：{proc.stderr}"


# ---------------------------------------------------------------- 纯函数单测
# _common 的纯计算函数不依赖 streamlit runtime，始终执行。


def _common():
    if str(UI) not in sys.path:
        sys.path.insert(0, str(UI))
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import importlib

    import _common  # noqa
    return importlib.reload(_common)


def _read_view(run_root: Path) -> dict:
    from expos.kernel.store import RunStore

    store = RunStore(str(run_root), create=False)
    view = store.export_view()
    return {
        "experiments": [e.model_dump(mode="json") for e in view.experiments],
        "observations": [o.model_dump(mode="json") for o in view.observations],
    }


@pytest.mark.skipif(not _HAS_STREAMLIT, reason="streamlit 未安装（.[ui]）——_common 顶层 import 依赖")
def test_pure_funcs(dev_run):
    C = _common()
    view = _read_view(dev_run)

    tnc = C.trusted_noncontrol(view["observations"])
    assert tnc and all(o["trust"] == "TRUSTED" and not o.get("is_control") for o in tnc)

    stats = C.per_round_stats(view["observations"], "maximize")
    assert len(stats) >= 1
    for key in ("naive_best_so_far", "trusted_best_so_far"):
        seen = [s[key] for s in stats if s[key] is not None]
        assert seen == sorted(seen), f"{key} 非单调"
    for s in stats:  # 1 轮全 TRUSTED 时两视角重合
        assert s["naive_best_so_far"] == s["trusted_best_so_far"]

    exp = view["experiments"][0]
    rows, cols, grid = C.board_grid(exp, view["observations"])
    assert (rows, cols) == (6, 8)
    filled = [grid[r][c] for r in range(rows) for c in range(cols) if grid[r][c]]
    assert filled and any(cell["value"] is not None for cell in filled)
    # 哨兵/对照孔应带 is_control 标记
    assert any("is_control" in cell for cell in filled)

    counts = C.trust_counts(view["observations"])
    assert counts["TRUSTED"] > 0
    assert dev_run.name in C.list_runs(dev_run.parent)


# ---------------------------------------------------------------- AppTest 冒烟


def _point(page: Path, run_root: Path):
    """加载单个页面文件，把侧栏 root 指向 run_root 的父目录并渲染。"""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(page), default_timeout=120)
    at.run()
    assert not at.exception, f"{page.name} 首次渲染异常：{at.exception}"
    at.text_input[0].set_value(str(run_root.parent)).run()
    assert not at.exception, f"{page.name} 指向 run 后异常：{at.exception}"
    return at


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
@pytest.mark.parametrize("name", list(PAGES))
def test_apptest_each_page_smoke(name, dev_run):
    """四页各自 AppTest 冒烟：无异常且有标题。"""
    at = _point(PAGES[name], dev_run)
    assert at.title, f"{name} 应有标题"


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_entry_navigation(dev_run):
    """入口 app.py 的 st.navigation 渲染默认页（运行总览）不崩。"""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    assert not at.exception, f"入口首渲染异常：{at.exception}"
    at.text_input[0].set_value(str(dev_run.parent)).run()
    assert not at.exception
    assert at.title, "导航默认页应有标题"


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_missing_dir_friendly(dev_run, tmp_path):
    """指向不存在目录 → 友好提示、不崩（对每页）。"""
    from streamlit.testing.v1 import AppTest

    missing = str(tmp_path / "does_not_exist")
    for page in PAGES.values():
        at = AppTest.from_file(str(page), default_timeout=60)
        at.run()
        at.text_input[0].set_value(missing).run()
        assert not at.exception, f"{page.name} 目录不存在不应抛异常：{at.exception}"
        assert at.error, f"{page.name} 应给出错误提示"


# ---------------------------------------------------------------- 交互


def _board_spec(at) -> str:
    charts = at.get("plotly_chart")
    assert charts, "板图应渲染 plotly_chart"
    return charts[0].proto.spec


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_board_round_switch(dev_run_multi):
    """板图切换轮次 selectbox → plotly 数据变化。"""
    at = _point(PAGES["board"], dev_run_multi)
    sel = next(s for s in at.selectbox if s.label == "选择轮次")
    assert len(sel.options) >= 2
    spec0 = _board_spec(at)
    sel.set_value(sel.options[1]).run()
    assert not at.exception
    assert spec0 != _board_spec(at), "切换轮次后板图数据应变化"


def _events_df(at):
    for d in at.dataframe:
        if "kind" in list(d.value.columns):
            return d.value
    return None


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_event_kind_filter(dev_run_multi):
    """裁决日志页事件 kind multiselect 过滤 → 行数减少。"""
    at = _point(PAGES["decisions"], dev_run_multi)
    df_all = _events_df(at)
    assert df_all is not None and len(df_all) > 0
    ms = next(m for m in at.multiselect if m.label == "kind 过滤器")
    assert len(ms.value) >= 2
    first = ms.value[0]
    ms.set_value([first]).run()
    assert not at.exception
    df_one = _events_df(at)
    assert df_one is not None and len(df_one) < len(df_all)
    assert set(df_one["kind"].unique()) == {first}


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_mechanism_activity_quick_filter_and_summary(dev_run_multi):
    """裁决日志页「机制活性」快捷过滤 + risk_map_applied/aggregation_alpha 人读摘要。"""
    mech_kinds = {"risk_map_applied", "aggregation_alpha", "config_drift"}
    at = _point(PAGES["decisions"], dev_run_multi)
    df_all = _events_df(at)
    assert df_all is not None and len(df_all) > 0
    assert "摘要" in df_all.columns

    present = mech_kinds & set(df_all["kind"].unique())
    assert present, "dev run 应发射至少一个机制活性观测面事件"

    risk_rows = df_all[df_all["kind"] == "risk_map_applied"]
    if len(risk_rows):
        s = risk_rows.iloc[0]["摘要"]
        assert "风险图：" in s or "风险图缺席" in s

    alpha_rows = df_all[df_all["kind"] == "aggregation_alpha"]
    if len(alpha_rows):
        s = alpha_rows.iloc[0]["摘要"]
        assert "alpha 条目" in s
        # 数组本身不展开渲染进摘要列
        assert "obs_id" not in s and "[" not in s

    non_mech = df_all[~df_all["kind"].isin(mech_kinds)]
    if len(non_mech):
        assert (non_mech["摘要"] == "").all()

    cb = next(c for c in at.checkbox if c.label == "机制活性")
    cb.set_value(True).run()
    assert not at.exception
    df_mech = _events_df(at)
    assert df_mech is not None and len(df_mech) > 0
    assert set(df_mech["kind"].unique()) <= mech_kinds


# ---------------------------------------------------------------- 三臂对比


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_compare_single_run_guidance(dev_run):
    """同级仅一个 run 时，三臂对比页显示指引文案（有 info 提示、不崩）。"""
    at = _point(PAGES["compare"], dev_run)
    assert at.info, "单 run 时应显示指引 info"


@pytest.mark.skipif(not _HAS_APPTEST, reason="streamlit.testing.v1.AppTest 不可用")
def test_apptest_compare_multi_arm_regret_table():
    """指向含多 run×多 arm 的 sweep 目录 → 出 regret 表（读 report/score.json）。"""
    from streamlit.testing.v1 import AppTest

    sweep = REPO / "runs" / "pilot_sweep" / "runs"
    if not sweep.is_dir():
        pytest.skip("无 runs/pilot_sweep/runs 现成 sweep 目录")
    scored = [d for d in sweep.iterdir() if (d / "report" / "score.json").exists()]
    if len(scored) < 2:
        pytest.skip("sweep 目录下不足 2 个已评分 run")

    at = AppTest.from_file(str(PAGES["compare"]), default_timeout=120)
    at.run()
    at.text_input[0].set_value(str(sweep)).run()
    assert not at.exception
    arm_df = None
    for d in at.dataframe:
        if "arm" in list(d.value.columns):
            arm_df = d.value
    assert arm_df is not None and len(arm_df) >= 1, "应渲染按 arm 聚合的 regret 表"
    assert "mean_final_regret" in arm_df.columns


# ---------------------------------------------------------------- CJK 字体兜底


@pytest.mark.skipif(not _HAS_STREAMLIT, reason="streamlit 未安装（.[ui]）——_common 顶层 import 依赖")
def test_cjk_font_fallback_no_error():
    """无论环境有无 CJK 字体，兜底函数都返回 bool 不抛（g209 无中文字体环境曾暴露：
    函数原住在脚本式页面模块里，裸 import 执行整页 + bare-mode st.stop() 不停 →
    环境相关假败——函数已迁 _common，测试不再执行页面主体）。"""
    pytest.importorskip("matplotlib")
    if str(UI) not in sys.path:
        sys.path.insert(0, str(UI))
    import _common

    ok = _common._try_cjk_font()
    assert isinstance(ok, bool)
