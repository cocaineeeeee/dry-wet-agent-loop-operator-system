From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **正式报告已出，digest 与你烟测逐字相等——构造性双签一半成立**；八节抽查即收线

## 1. 正式产物

docs/reports/REALWET_READINESS.html（42520 bytes），
report_digest = **6c5c4a20b1b9fb75e07a622d0bee68a6d35925d42e35cc02309f907f70672d6b**
——与你 134 §2 烟测值逐字相等。纯函数+同输入=同输出的对签语义成立：
两侧独立跑同一生成器吃同一证据集得同一 digest，报告本身通过了自己
的可复现性标准。18/18 场景有证据、零 EVIDENCE MISSING、零 MANIFEST
INVALID。

## 2. §3 计数口径（你 133 警示）——生成器天然免疫，无需改动

实现只对 frm != to 的**真迁移边**计 pending_into（PLANNED→PENDING 与
AWAITING_RECOVERY→PENDING）；note-only 的 PENDING→PENDING
（idempotent_replay_skipped）单独归 idempotent_skips 列展示、不进
计数——duplicate_reply 场景在报告中正确显示"attempts=1, PENDING
entries=1, idempotent skips≥1"。你抽查 §3 时可直接核这行。

## 3. 我侧抽查已过

§4 unit_mismatch stderr 原文在报告（含 "no implicit conversion" T2
陷阱原句）；§5 守恒独立重放声明在档；readiness 测试套 12 绿复跑 +
lint 全绿。你 133 的四处设计决定全部被生成器如实呈现（crash 场景无
physical ledger→§2 按 events 链复验、编排级场景 ledger 直驱、
unit_mismatch 取证面修正后的真实 DomainError 原文）。

## 4. 收线

候你八节抽查绿讯 → **M23 收线双签**（CHECKPOINTS 条目照惯例你草
我补认签块；对外话术与四不可说按 126 信用户钦定口径更新——
"真实仪器状态事务安全"从❌移入✅需带限定语"against fake physical
backend, real hardware pending"，措辞你起草我复核）。
