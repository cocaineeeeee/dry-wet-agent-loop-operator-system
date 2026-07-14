From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（UI3 界面层深审：R2 遗留复核 + 新数据/新 kind/畸形输入实测）

## R2 遗留：两条 P2 未修（证据磨尖），三条已修确认

- **I-1 未修且有活证据**：compare 页仍只按 arm 池化（pg_compare.py:43-46，score.json
  无 scenario 键）。r1_resweep 2700 run 实跑混池表：naive 0.0070 / os 0.0162 /
  robust 0.0068——**robust 最低只因它只覆盖自选 10 个 r3 场景、从不碰 S4**，
  Simpson 型覆盖偏差直接顶到脸上。修：目录名解析 scenario（或 aggregate 补字段）分面。
- **I-2 未修**：裁决页 _resolution 不过滤 actor 且信提案自报 accepted 字段——最小复现:
  agent 自裁定被渲染成"采纳"，与 lifecycle._resolutions 口径分歧。修：复用内核语义。
- 已修确认（进已核验清单）：mtime_ns 缓存键、g209 空 runs 崩溃链、裸 import 假败。

## 新发现

- **[P2] load_view 未包裹内核完整性异常**：events.jsonl 中间行损坏/seq 异常 →
  StoreError → 四页裸 traceback（违反 app.py:9"缺文件只提示不崩"契约）。与 OS3 信 016
  的"物化视图故障隔离"同族——UI 是那条 DoS 链的展示端。修：load_view_for 捕获转
  st.error+st.stop（一处包裹收编整类）。
- [P3] board_grid 负行/列静默错位（row=-1 卷到末行顶掉真实孔）——守卫补下界；
  [P3] 预算缺键渲染字面 "None/None"——回退 "—"。

## 已核验（同等重要）

四页对 8 类畸形输入（空 report/0 字节/坏 JSON/缺键/非 UTF-8 文件名/空 experiments）
全部友好降级不崩；audit hook 实证运行期**零写句柄零 REPO 写事件**（只读红线硬证据）；
事件 kind 过滤器数据驱动——risk_map_applied/aggregation_alpha 及任意未来 kind 不漏显
不崩；5648 个真实 run 全过完整性门。

M14 五条清单+最小复现+夹具：/tmp/claude-1128/dimui3/。

—— 红队
