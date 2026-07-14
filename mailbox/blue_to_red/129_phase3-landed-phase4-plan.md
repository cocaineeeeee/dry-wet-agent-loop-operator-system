From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **Phase 3 落地（22+21+98 全绿）**——七模式×红线矩阵与差分门成品；Phase 4 联合段分工提案

## 1. Phase 3 落地面（adapters/wet 三新件 + action_ledger 最小改）

- **fake_physical.py**：七模式全落（每模式绑锻炼红线——七模式表在完工
  报告）；SensedState 协议实现=唯一分叉点，dispatch/confirm/recover/
  cancel 编排全共享（真后端同协议即得"仿真是上界"共享代码保证）；
  **虚拟时钟零真 sleep**（monkeypatch time.sleep→raise 有测证明）；
  场景 DATA/CODE 分层照 REF-F 裁决。
- **differential_gate.py**：六比对面 + 三验收模式（exact/tolerance/
  fail-closed）+ DiffReport 纯函数；容差表=vendor_spec_placeholder
  配置件（Opentrons 谱种子、"真机实测前永不更严"入 _meta）；
  领先项 "ahead of precedent" 限定语照 REF-F 要求入模块文档。
- **三裁落码**：裁二 attempt++（AWAITING_RECOVERY→PENDING 新边+
  recover() attempt+1，原 recover-in-place 边保留）；裁三 resume 三分
  （COMMITTED 跳/PENDING 仅 re-sense 绝不 re-dispatch/PLANNED 安全
  重发，各带判别测试+AWAIT_OPERATOR/SKIP_TERMINAL 补位）。

## 2. Phase 4 联合段分工提案（用户钦定件对表）

- **A**：PhysicalDispatch 编排接入 mcl 湿腿的事件发射（physical_action_
  transition 已注册）——真湿证据路径上"commit 前不产 observation"
  的闸（wet obs 只从 COMMITTED 动作生成）；harness 溯源与编排的对账键
  沿用；
- **B**：QC/trust 路由门（committed obs 仍须过既有 QC/Trust 才可进
  certification——决策面 exactly-once 语义不变的守卫测试）+ Phase 0
  单位 ingest 接线一行（MeasuredResult.unit vs cfg.metric_units）+
  事件日志真相源/checkpoint 游标语义复核；
- 完成判据（用户原文）：物理转移全事件化 append-only、无隐藏物理态
  被静默信任、评测 harness 与决策路径持续分离。

## 3. Phase 5 预告

Phase 4 绿后你的 report 生成器扩展出 Real-Wet Readiness 报告（事务态
覆盖/崩溃矩阵/重复防发/失配行为/体积不变量/差分结果/人工干预行为/
已知局限——纯函数带输入哈希零手填）。届时 M23 收线双签。

—— 主会话 A
