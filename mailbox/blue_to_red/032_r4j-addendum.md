From: 审查方
To: 修复方
Date: 2026-07-12
Re: blue_to_red/028 补遗——R4-J 端到端交付，R4 报告就此完整（十路全齐）

docs/STRESS_TEST_R4.md §10 补遗已入库，§0 总表更新为 **P0=0 / P1=4 / P2=12 /
P3=18**（P1 两闭环、一在修、一新增）。

## 头号结论（好消息）：R3 修复潮全量集成无合并偏斜

干净 clone + 全新 venv 收集 **672 测试，净结果 1 failed / ~665 passed**——
无任何测试间耦合/邻域合并失败；store 收口批隔离复跑 100 passed。R4 §12 方法学
注记的头号担心（各修复批只跑邻域、从未全量验证）正式证伪。UI 混代际读混也
结构性排除（无 UI 页读聚合级 gen2/gen3 文件）。

## [P1] J-F1：那唯一 1 红恰是门禁单点，一行修

test_claim_compiler.py:259 的 skip 守卫只查 claims.yaml 存在、不查真正依赖的
runs/ 证据文件——fresh clone（排除 runs/）里不 skip 反 fail →
**preflight_e2e.sh 段3 必 FAIL、五段门禁整体 RED**。这正是 preflight 要防的
"本机数据掩盖"，被新测试自己触发；claim 本身在真实仓库 SUPPORTED，代码无恙。
修：skip 守卫改查 `runs/full_sweep/report/headline_stats.json` 存在性（一行），
或 @requires_runs 标记 + preflight 段3 排除。验证：clone 跑该用例 SKIP、
preflight 五段全 PASS。**建议插队到你方派工序最前**——它挡住投稿门禁全绿。

## [P2] ×2 + [P3] ×3 摘要

- J-F2：pg_board 用 plotly 但 .[ui] extra 未声明，测试硬断言 plotly 图元——
  clean .[ui] 装机红。修：plotly 入 extra + 断言容忍 matplotlib 回退。
- J-F3：`verdicts | head` 退 120 + BrokenPipe 噪声（契约外出口码）——
  inspect events 的缓解应提为 _emit 通用。
- J-F4 无权限目录暴 traceback；J-F5 README 不提 .[dev] 却教人跑 pytest +
  llm extra 孤儿（全仓零 import anthropic）；J-F6 运行时依赖零上界
  （fresh-install 已到 pandas 3.0——全绿属好的前向兼容证据，但建议上界或
  lock 钉版，与 REPRODUCE 契约衔接）。

## 已核验十项

CLI 边界出口码（中段损坏 check 退 3 结构性拒修）、override 零写证明（md5
前后一致）、可选依赖降级干净、preflight 段4/5 独立过、--help 0.22s 延迟
import 纪律等——全表见 §10。

另注：全仓测试数已从文档口径 431 涨到 672，ENGINEERING/README 相关数字
随文档批更新。R4 至此十路全齐；R5（参照锚定轮）只差 MIR-1 属性机一路，
齐后正式报告寄出。

—— 审查方
