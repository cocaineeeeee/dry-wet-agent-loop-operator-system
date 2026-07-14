"""多页只读仪表盘的共享层：缓存数据加载 + 纯计算工具 + 侧栏 run 选择。

只读红线（docs/REFERENCE_MAP.md §13.13；族6 报告 §3）：
- 本模块**只**以 RunStore(path, create=False) 打开运行目录、只调用其读方法
  （export_view / read_config / read_events），并直接读若干 JSON/JSONL 文件
  （config.json / checkpoint.json / events.jsonl / models/snapshot_r*.json /
  report/score.json）。绝不构造写句柄、绝不 import lifecycle 写函数、绝不调用
  任何 save_/append_/write_/_atomic_write 等变更 API。
- **绝不读 truth/ 目录**：真值 sidecar 是公理 6 的隔离对象，任何 UI 走查/glob
  都显式跳过 truth/（本模块所有 glob 都限定在 experiments/observations/models/
  report 之内，永不触及 truth/）。export_view() 本身也不含 truth（store.py §只读视图）。
- 所有磁盘读取经 @st.cache_data 缓存，cache key 均含 (path, mtime_ns:size) 令牌——
  文件一变令牌即变、缓存自动失效（§13.13「UI 缓存以 (path, mtime) 为 key」的加固版：
  秒级 st_mtime 在同一秒内多次写入时不变会读到旧快照，STRESS_TEST_R1 P2-E）。
- best-so-far 只按 TRUSTED 且非对照（is_control=False）观测计算（§13.7）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st

# 防御性：确保仓库根在 sys.path 上（`streamlit run` / AppTest 两种入口均可 import expos）。
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# 只读依赖：仅 RunStore 的读方法（依赖方向 ui→kernel，永不写）。
from expos.errors import ExposError  # noqa: E402
from expos.kernel.store import RunStore  # noqa: E402

# 空快照骨架：字段齐全的只读快照，供缺 run / 完整性异常时页面走空态而非崩溃。
_EMPTY_VIEW: dict[str, Any] = {
    "run_root": None,
    "experiments": [],
    "observations": [],
    "decisions": [],
    "events": [],
    "checkpoint": {},
    "config": {},
}

TRUST_COLORS = {
    "TRUSTED": "#2e7d32",  # 绿
    "SUSPECT": "#f9a825",  # 黄
    "FAILED": "#c62828",  # 红
    "PENDING": "#9e9e9e",  # 灰
}

# 提案类决策：需有配对 acceptance/rejection 才可能影响后续设计（§4.5 审计不变量）。
PROPOSAL_KINDS = {"goal_translation", "prior_proposal", "action_proposal"}
VERDICT_KINDS = {"acceptance", "rejection", "override"}

# 明确保护：UI 允许读取的子目录白名单——truth/ 不在其内（红线）。
_READ_SUBDIRS = ("experiments", "observations", "models", "report")


# ---------------------------------------------------------------- 渲染兜底


def _try_cjk_font() -> bool:
    """matplotlib 回退分支的中文轴标兜底：找得到系统 CJK 字体就用，找不到静默保持。
    （自 pg_board 迁入：页面是脚本式模块，测试触达本函数不该被迫执行整页。）"""
    try:
        import matplotlib
        from matplotlib import font_manager
    except Exception:  # pragma: no cover
        return False
    candidates = [
        "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC",
        "Source Han Sans SC", "Source Han Sans CN",
        "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
        "Microsoft YaHei", "SimHei", "PingFang SC",
        "Droid Sans Fallback", "Arial Unicode MS",
    ]
    try:
        available = {f.name for f in font_manager.fontManager.ttflist}
    except Exception:  # pragma: no cover
        return False
    for name in candidates:
        if name in available:
            base = list(matplotlib.rcParams.get("font.sans-serif", []))
            matplotlib.rcParams["font.sans-serif"] = [name] + [x for x in base if x != name]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return True
    return False


# ---------------------------------------------------------------- (path, 缓存令牌)


def _heartbeat_path(root: Path) -> Path:
    """§13.13 心跳文件：优先 manifest.json（commit marker），否则 events.jsonl。"""
    m = root / "manifest.json"
    return m if m.exists() else (root / "events.jsonl")


def _cache_token(p: Path) -> str:
    """缓存键令牌：``"{st_mtime_ns}:{st_size}"``（文件缺失时 ``"missing"``）。

    取舍（STRESS_TEST_R1 P2-E「UI 秒级 mtime 缓存」）：秒级 ``st_mtime`` 在同一秒内
    多次写入时不变，会命中旧缓存读到旧快照。改用纳秒级 mtime + 文件大小，实际写入
    几乎必然改变令牌。UI 是只读观察面、不追求强一致：极端场景（文件系统 mtime 粒度
    粗于纳秒且覆写后长度恰好不变）仍可能短暂读到旧快照，刷新页面/下次心跳即恢复——
    这是简单方案换来的可接受残余，不引入锁或内容哈希。
    """
    try:
        st = p.stat()
    except OSError:
        return "missing"
    return f"{st.st_mtime_ns}:{st.st_size}"


# ---------------------------------------------------------------- 缓存数据层
# 每个 loader 都显式接收缓存令牌，进入 cache key —— 文件一变即失效。


@st.cache_data(show_spinner=False)
def load_view(root_str: str, hb_token: str) -> dict[str, Any]:
    """加载整个运行目录的只读快照，返回纯 dict（可缓存、可 hash）。

    hb_token 仅用作 cache key（(path, mtime_ns:size) 契约，见 _cache_token），
    函数体不使用它。RunStore(..., create=False)：不新建任何目录、不产生写句柄。
    """
    store = RunStore(root_str, create=False)
    view = store.export_view()  # 只读视图，天然不含 truth/
    return {
        "run_root": view.run_root,
        "experiments": [e.model_dump(mode="json") for e in view.experiments],
        "observations": [o.model_dump(mode="json") for o in view.observations],
        "decisions": [d.model_dump(mode="json") for d in view.decisions],
        "events": [dict(e) for e in view.events],
        "checkpoint": view.checkpoint,
        "config": store.read_config(),
    }


@st.cache_data(show_spinner=False)
def load_model_snapshots(root_str: str, models_token: str) -> list[dict[str, Any]]:
    """读取 models/snapshot_r*.json 模型指纹历史（限定 models/，不碰 truth/）。"""
    root = Path(root_str)
    out = []
    for p in sorted((root / "models").glob("snapshot_r*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda d: d.get("round_id", 0))
    return out


@st.cache_data(show_spinner=False)
def load_score(run_dir_str: str, score_token: str) -> dict[str, Any] | None:
    """读取单个 run 的 report/score.json（限定 report/，不碰 truth/）。"""
    p = Path(run_dir_str) / "report" / "score.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_view_for(run_root: Path | None) -> dict[str, Any]:
    """便捷封装：以心跳 mtime 为 key 拉取只读快照。

    run_root=None（runs 根目录下发现零 run、侧栏无可选项）→ 返回空快照 {}，
    页面据此显示指引文案——R2 后 g209 复现的 TypeError（None / "manifest.json"）
    根因即此路径无守卫，本地恰有 runs/ 掩盖。

    OS3 DoS 链展示端隔离（新审 P2）：events.jsonl 中间行损坏 / seq 断裂等物化视图
    故障会让内核 export_view 抛 ExposError 族（StoreError…），若不收编则四页裸
    traceback、违反 app.py:9「缺文件只提示不崩」契约。此处一处包裹整类：转 st.error
    （含 `expos check` 修复指引）+ 返回字段齐全的空快照，页面据此走空态不崩。"""
    if run_root is None:
        return {}
    hb = _heartbeat_path(run_root)
    try:
        return load_view(str(run_root), _cache_token(hb))
    except ExposError as e:
        st.error(
            f"运行目录完整性校验失败，无法加载只读视图（内核抛 {type(e).__name__}）：{e}"
            "\n\n多为 events.jsonl 行损坏 / seq 断裂等物化视图故障。请在终端运行 "
            "`expos check <run_root>` 定位并修复后刷新本页。"
        )
        return dict(_EMPTY_VIEW)


def snapshots_for(run_root: Path | None) -> list[dict[str, Any]]:
    if run_root is None:
        return []
    return load_model_snapshots(str(run_root), _cache_token(run_root / "models"))


def score_for(run_dir: Path | None) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    return load_score(str(run_dir), _cache_token(run_dir / "report" / "score.json"))


# ---------------------------------------------------------------- run 发现


def _is_run_dir(child: Path) -> bool:
    return (child / "events.jsonl").exists() or (child / "experiments").is_dir()


def list_runs(root: Path) -> list[str]:
    """列出 root 下形似运行目录的直接子目录（含 events.jsonl 或 experiments/）。"""
    if not root.is_dir():
        return []
    return [c.name for c in sorted(root.iterdir()) if c.is_dir() and _is_run_dir(c)]


def sibling_runs(run_root: Path) -> list[Path]:
    """与所选 run 同级的所有 run 目录（供三臂对比扫描 report/score.json）。"""
    parent = run_root.parent
    if not parent.is_dir():
        return []
    return [c for c in sorted(parent.iterdir()) if c.is_dir() and _is_run_dir(c)]


# ---------------------------------------------------------------- 纯计算工具
# 无 streamlit 依赖，便于单测。


def trusted_noncontrol(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        o
        for o in observations
        if o.get("trust") == "TRUSTED" and not o.get("is_control", False)
    ]


def _obj_value(o: dict[str, Any]) -> float | None:
    res = o.get("result") or {}
    return res.get("value")


def _noncontrol(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [o for o in observations if not o.get("is_control", False)]


def trust_counts(observations: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"TRUSTED": 0, "SUSPECT": 0, "FAILED": 0, "PENDING": 0}
    for o in observations:
        t = o.get("trust") or "PENDING"
        counts[t] = counts.get(t, 0) + 1
    return counts


def best_trusted(observations: list[dict[str, Any]], direction: str = "maximize"):
    """最优 TRUSTED 非对照观测（§13.7）。返回 (value, obs) 或 (None, None)。"""
    tnc = [o for o in trusted_noncontrol(observations) if _obj_value(o) is not None]
    if not tnc:
        return None, None
    key = _obj_value
    best = max(tnc, key=key) if direction == "maximize" else min(tnc, key=key)
    return _obj_value(best), best


def direction_of(view: dict[str, Any]) -> str:
    cfg = view.get("config") or {}
    dc = cfg.get("domain_config") or {}
    obj = dc.get("objective") or {}
    return obj.get("direction") or "maximize"


def per_round_stats(
    observations: list[dict[str, Any]], direction: str = "maximize"
) -> list[dict[str, Any]]:
    """每轮统计，含两条最优曲线（naive 视角 vs trusted 视角，§12/§13.7）。"""
    rounds = sorted(
        {o.get("round_id") for o in observations if o.get("round_id") is not None}
    )
    stats: list[dict[str, Any]] = []
    run_trust: float | None = None
    run_naive: float | None = None
    better = max if direction == "maximize" else min

    def _acc(running: float | None, vals: list[float]) -> float | None:
        if not vals:
            return running
        rb = better(vals)
        return rb if running is None else better(running, rb)

    for r in rounds:
        in_round = [o for o in observations if o.get("round_id") == r]
        tnc = trusted_noncontrol(in_round)
        nc = _noncontrol(in_round)
        trust_vals = [v for v in (_obj_value(o) for o in tnc) if v is not None]
        naive_vals = [v for v in (_obj_value(o) for o in nc) if v is not None]
        round_best = better(trust_vals) if trust_vals else None
        run_trust = _acc(run_trust, trust_vals)
        run_naive = _acc(run_naive, naive_vals)
        stats.append(
            {
                "round_id": r,
                "n_obs": len(in_round),
                "n_trusted_noncontrol": len(tnc),
                "round_best": round_best,
                "trusted_best_so_far": run_trust,
                "naive_best_so_far": run_naive,
            }
        )
    return stats


def board_grid(
    experiment: dict[str, Any], observations: list[dict[str, Any]]
) -> tuple[int, int, list[list[dict[str, Any] | None]]]:
    """把某轮 experiment 的 layout 铺成 rows×cols 网格。

    单元含 well_id / value / trust / is_control（哨兵标记）/ obs（完整观测 dict）。
    """
    layout = experiment.get("layout") or {}
    rows = int(layout.get("rows") or 6)
    cols = int(layout.get("cols") or 8)
    obs_by_well: dict[str, dict[str, Any]] = {}
    for o in observations:
        lm = o.get("layout_meta") or {}
        wid = lm.get("well_id")
        if wid is not None:
            obs_by_well[wid] = o
    grid: list[list[dict[str, Any] | None]] = [
        [None for _ in range(cols)] for _ in range(rows)
    ]
    for w in layout.get("wells", []):
        rr, cc = w.get("row"), w.get("col")
        # 上下界都守卫：负行/列（row=-1）在 Python 负索引会卷绕顶掉真实孔（P3）。
        if rr is None or cc is None or rr < 0 or cc < 0 or rr >= rows or cc >= cols:
            continue
        o = obs_by_well.get(w.get("well_id"))
        # 哨兵/对照：layout 用 control_id 标记，观测用 is_control 标记（二者一致）。
        is_control = bool(w.get("control_id")) or bool(o and o.get("is_control"))
        grid[rr][cc] = {
            "well_id": w.get("well_id"),
            "cand_id": w.get("cand_id"),
            "control_id": w.get("control_id"),
            "is_control": is_control,
            "value": _obj_value(o) if o else None,
            "trust": (o.get("trust") if o else "PENDING"),
            "obs": o,
        }
    return rows, cols, grid


def round_experiments(view: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """容空：空快照（无选中 run）→ 空 dict，页面走空态指引而非 KeyError
    （g209 环境复现链：runs 发现为零 → root=None → view={} → 本函数曾缺键炸）。"""
    return {e["round_id"]: e for e in view.get("experiments", [])}


# ---------------------------------------------------------------- 侧栏 run 选择


def sidebar_run_selection() -> Path | None:
    """渲染侧栏的 run 选择控件，返回所选 run 目录（缺失时返回 None 并友好提示）。

    每个页面各自调用一次（页面切换时各页独立重跑，无重复 key 冲突）；选择态经
    带 key 的控件存入 session_state，跨页面持久。
    """
    with st.sidebar:
        st.header("运行选择")
        root_str = st.text_input("runs 根目录", value="runs", key="runs_root")
        root = Path(root_str)
        if not root.is_dir():
            st.error(f"目录不存在：{root_str}")
            return None
        runs = list_runs(root)
        if not runs:
            st.warning(f"{root_str} 下未发现运行目录。")
            return None
        run_name = st.selectbox("run", runs, key="run_name")
        run_root = root / run_name
        st.caption(f"已选：{run_root}")
        st.divider()
        st.caption("只读仪表盘 · 零写句柄 · 不读 truth/")
    return run_root
