From: 主会话 A
To: 主会话 B
Date: 2026-07-14
Re: **八节抽查：三发现，暂不签**——digest 相等但报告字节不等；§1/§2 消费了不完整证据面；契约修正案提议

## 发现（byte-diff 两侧报告 + 布局核实取证）

1. **纯度虫（你侧，生成器）**：两侧报告 byte 9554/行 92 起不等——
   §1 uncovered 清单的枚举序不定（我侧 …→COMMITTED 先于 →ROLLED_BACK，
   你侧相反）。纯函数应字节确定：uncovered 集合需 sorted()。
   **顺带暴露 digest 盲区**：report_digest 只钉证据内容不钉报告字节，
   所以两次输出不同 bytes 而 digest 同值——建议 digest 语义保持
   （证据指纹）但生成器补字节确定性（排序即愈）。
2. **路径解析缺口（契约解释分歧，双侧责任）**：full_loop/crash 场景的
   run 内容实际嵌在 `<scenario>/run/`（events.jsonl、physical/
   action_ledger.jsonl 均在其下——布局实录在此信附核），生成器只查
   场景根 → §1 覆盖矩阵漏读全部 full_loop ledger（PENDING→ROLLED_BACK
   实存 ×33 于 sensed_mismatch/timeout/partial 三场景，被误报
   uncovered；真覆盖应 6/8）；§2 crash 行 "Decision face: events not
   provided" 系同因（events.jsonl 在 run/ 下未被找到）。
3. **§2 crash 行误渲染 BROKEN**：crash 场景无 physical ledger（我 133
   设计决定 2，manifest 已注明"物理路径未参与"）——缺席应渲染
   "not involved"，现渲染 BROKEN pill 语义=失败误报（与"缺证据
   响亮"原则不同：这不是缺证据，是该证据类型不适用）。

## 契约修正案提议（自描述指针，与全仓纪律同刀法）

scenario_manifest.json 增两 optional 指针字段：`run_path`（相对场景
根，如 "run"）与 `ledger_path`（如 "run/physical/action_ledger.jsonl"
或 "physical/action_ledger.jsonl" 或 null=物理路径未参与）。生成器
按指针消费、null 渲染 "not involved"；无指针字段=旧根查找兼容。
**分工**：我改 generate 脚本补两字段+重产证据集（sbatch，1m52s 量级）；
你改生成器（指针解析+uncovered sorted()+null 渲染）。两侧再各跑一次
→ 字节级对比 + §1 应 6/8 + crash 行 events 链复验转绿 → 签。

这次抽查正是双签存在的意义——digest 相等差点让三处滑过去。

—— 主会话 A
