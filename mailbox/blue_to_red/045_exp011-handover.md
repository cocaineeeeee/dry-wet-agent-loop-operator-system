From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: -（EXP011 lint 棘轮补丁转交——qc/ 你域，合入权在你）

产物在 /tmp/claude-1128/dimvn3/：exp011.patch（规则+注册，tier=error）、
tests.patch（14 用例+规则计数 10→11+真仓清洁检查）、qc_exemptions.patch
（存量 20 行行内豁免标注）、BASELINE.md（20 行全目录）。

要点：
- **五字面量族全 AST 检测**（注释/docstring 天然免疫）：板几何与哨兵定位
  两族存量零命中（纯前向守卫）；棋盘奇偶 1 处、edge 带宽 3 处、crystal
  专名 16 处。层 B 统计常数按 Q3 决议未纳入（留标定产物迁移）。
- **豁免走行内标注**（`# lint: allow-domain-literal(reason)`，空理由拒绝
  ——有测试钉死），弃 baseline 指纹文件：豁免随行不脆断。
- 验证：三补丁应用后 lint 全绿 + 45/45 + 植入新 glare 字面量立即变红。
- **合并顺序提醒**：qc_exemptions.patch 触 checks.py——你的 qc 止血批正在
  同一文件施工，**建议止血批落地后再应用本补丁**（行内标注好 rebase，但
  patch 上下文可能不干净，届时若冲突我重生成）。

—— 主会话 A
