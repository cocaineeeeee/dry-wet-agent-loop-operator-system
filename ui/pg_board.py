"""页：板图 —— 逐轮 plate 热图 + trust 着色 + 哨兵标记。

只读：仅经 _common 缓存读方法；不写、不读 truth/。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import streamlit as st  # noqa: E402

try:
    import plotly.graph_objects as go  # noqa: F401

    _HAS_PLOTLY = True
except Exception:  # pragma: no cover - 环境相关
    _HAS_PLOTLY = False

_TRUST_IDX = {"PENDING": 0, "TRUSTED": 1, "SUSPECT": 2, "FAILED": 3}


# _try_cjk_font 已迁至 _common.py（页面是脚本式模块，裸 import 会执行整页——
# 测试为触达该函数曾被迫执行页面主体，bare-mode 下 st.stop() 不停导致环境相关假败）
from _common import _try_cjk_font  # noqa: F401  （页面内既有调用点继续用本名）


def _draw_board(rows: int, cols: int, grid) -> None:
    row_labels = [chr(ord("A") + r) for r in range(rows)]
    col_labels = [str(c + 1) for c in range(cols)]
    if _HAS_PLOTLY:
        import plotly.graph_objects as go

        discrete_scale = [
            [0.0, C.TRUST_COLORS["PENDING"]], [0.25, C.TRUST_COLORS["PENDING"]],
            [0.25, C.TRUST_COLORS["TRUSTED"]], [0.5, C.TRUST_COLORS["TRUSTED"]],
            [0.5, C.TRUST_COLORS["SUSPECT"]], [0.75, C.TRUST_COLORS["SUSPECT"]],
            [0.75, C.TRUST_COLORS["FAILED"]], [1.0, C.TRUST_COLORS["FAILED"]],
        ]
        z = [[float(_TRUST_IDX.get(grid[r][c]["trust"], 0)) if grid[r][c] else 0.0
              for c in range(cols)] for r in range(rows)]

        def _cell_text(cell):
            if not cell:
                return ""
            mark = "◆哨兵<br>" if cell.get("is_control") else ""
            val = f"{cell['value']:.3f}" if cell["value"] is not None else "—"
            return f"{mark}{cell['well_id']}<br>{cell['trust']}<br>{val}"

        text = [[_cell_text(grid[r][c]) for c in range(cols)] for r in range(rows)]
        fig = go.Figure(
            data=go.Heatmap(
                z=z, x=col_labels, y=row_labels, text=text, texttemplate="%{text}",
                hoverinfo="text", zmin=-0.5, zmax=3.5,
                colorscale=discrete_scale, showscale=False,
            )
        )
        # 哨兵标记：在对照孔中心叠一个白边菱形。
        sx, sy = [], []
        for r in range(rows):
            for c in range(cols):
                cell = grid[r][c]
                if cell and cell.get("is_control"):
                    sx.append(col_labels[c])
                    sy.append(row_labels[r])
        if sx:
            fig.add_trace(
                go.Scatter(
                    x=sx, y=sy, mode="markers",
                    marker=dict(symbol="diamond-open", size=26, color="white",
                                line=dict(width=2, color="white")),
                    hoverinfo="skip", showlegend=False,
                )
            )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("plotly 不可用，使用 matplotlib 回退。")
        import matplotlib.pyplot as plt

        _try_cjk_font()
        fig, ax = plt.subplots(figsize=(cols * 0.8, rows * 0.8))
        for r in range(rows):
            for c in range(cols):
                cell = grid[r][c]
                color = C.TRUST_COLORS.get(cell["trust"], "#9e9e9e") if cell else "#eeeeee"
                ax.add_patch(plt.Rectangle((c, rows - 1 - r), 1, 1, facecolor=color,
                                           edgecolor="white"))
                if cell and cell.get("is_control"):
                    ax.plot(c + 0.5, rows - 1 - r + 0.5, marker="D", ms=10,
                            mfc="none", mec="white", mew=2)
                if cell and cell["value"] is not None:
                    ax.text(c + 0.5, rows - 1 - r + 0.5, f"{cell['value']:.2f}",
                            ha="center", va="center", fontsize=6, color="white")
        ax.set_xlim(0, cols)
        ax.set_ylim(0, rows)
        ax.set_xticks([c + 0.5 for c in range(cols)])
        ax.set_xticklabels(col_labels)
        ax.set_yticks([rows - 1 - r + 0.5 for r in range(rows)])
        ax.set_yticklabels(row_labels)
        ax.set_aspect("equal")
        st.pyplot(fig)


st.title("板图（plate 热图 · trust 着色 · 哨兵标记）")

run_root = C.sidebar_run_selection()
if run_root is None:
    st.info("请在左侧选择一个有效的 run。")
    st.stop()

view = C.load_view_for(run_root)
exps = C.round_experiments(view)
if not exps:
    st.info("该运行暂无 experiment（板图不可用）。")
    st.stop()

rounds = sorted(exps.keys())
rsel = st.selectbox("选择轮次", rounds, key="board_round")
exp = exps[rsel]
round_obs = [o for o in view["observations"] if o.get("round_id") == rsel]
rows, cols, grid = C.board_grid(exp, round_obs)

legend = "  ".join(
    f"<span style='color:{c}'>&#9632;</span> {k}" for k, c in C.TRUST_COLORS.items()
)
st.markdown(
    f"颜色按 trust：{legend} &nbsp;&nbsp; ◆ = 哨兵/对照孔（is_control）",
    unsafe_allow_html=True,
)

_draw_board(rows, cols, grid)

# 孔选择 → 完整 ObservationObject JSON
wells = [
    grid[r][c]["well_id"]
    for r in range(rows)
    for c in range(cols)
    if grid[r][c] is not None
]
if wells:
    wsel = st.selectbox("查看某孔完整 ObservationObject", wells, key="board_well")
    cell = next(
        (grid[r][c] for r in range(rows) for c in range(cols)
         if grid[r][c] is not None and grid[r][c]["well_id"] == wsel),
        None,
    )
    if cell and cell["obs"] is not None:
        st.json(cell["obs"])
    else:
        st.caption(f"孔 {wsel} 尚无观测（PENDING）。")
