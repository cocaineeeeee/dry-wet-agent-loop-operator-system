"""expos 只读多页仪表盘入口（族6 报告 §3：st.Page/st.navigation 多页 IA）。

四页只读信息架构（原单页四 tab 拆分而来）：
  运行总览 / 板图 / 裁决日志 / 三臂对比

只读红线（见 ui/_common.py 顶部完整说明，docs/REFERENCE_MAP.md §13.13）：
- 全程只以 RunStore(path, create=False) 打开 + 只读方法 + 直接读 JSON/JSONL；
- 零写句柄、零 store 变更调用；**绝不读 truth/ 目录**；
- 所有缓存以 (path, mtime) 为 key；界面全中文；空 run/缺文件只提示不崩。

运行： streamlit run ui/app.py
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="expos 只读监视器", layout="wide")

# 页面为独立文件：既可经 st.navigation 路由，也可 AppTest.from_file 单页冒烟。
_PAGES = [
    st.Page("pg_overview.py", title="运行总览", icon="📊", url_path="overview", default=True),
    st.Page("pg_board.py", title="板图", icon="🔬", url_path="board"),
    st.Page("pg_decisions.py", title="裁决日志", icon="⚖️", url_path="decisions"),
    st.Page("pg_compare.py", title="三臂对比", icon="📈", url_path="compare"),
]

st.navigation(_PAGES).run()
