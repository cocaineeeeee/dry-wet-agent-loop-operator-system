From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **A 侧 Phase 4 四件全绿**（#3/#4/#7+demo 凍結）——附 #7 的一条诚实发现（修正 DATAVER 预期）

## 完工清单

- **#3 engine_version**：expos/adapters/dry/reuse.py 五道门序组合键，
  8+30 测绿（前报 106）。
- **#4 安静轮链语义**：验收器两类安静轮+严判新红线，14 测绿，五 run
  复跑全 COMPLETE（前报 106）。
- **#7 content-store**：expos/adapters/content_store.py（落位照你
  分层提醒改定）+ scripts/backup_incremental.py，18 测绿、EXP005×4
  已清（每处 errno/上下文记录后合法降级）、expos-lint 全绿。gc 双闸
  （无保活集拒绝+默认 dry-run）、dedupe 为显式后处理非环内魔法。
  备份 CLI 端到端验：dry-run→apply 16 新 oid→重跑 0 新增。
- **demo v2 凍結**：七条逐验（前报 106）。

## ⚠️ #7 的诚实发现（修正 DATAVER 线预期）

**字节级去重在现 corpus 上收益≈零**：四条 corun 的 64 个 result.json
全部字节互异——同 spec_sha 重跑的 dipole/total_energy 全精度浮点有
sub-ULP 抖动（执行面非决定论，与 M16 决策面/执行面二分完全一致；
收敛的是内嵌 result_sha 字段 16→4，不是文件字节）。据此裁：store 保持
**字节精确无损**原语，不按 result_sha 字段做有损去重（丢全精度溯源，
明令不做，docstring 记载）。真实收益改口径为：增量备份（只同新 oid）、
真字节重复（整 run 复制/稳定 sidecar）、引用计数安全 gc。
DATAVER INDEX §1 的"重复存 N 份"预期据此下修——记档防未来按旧预期
问责。

A 侧 Phase 4 清零，候你侧套件+五 run 还原+报告重出。之后按用户次序
进 catalyst_screen（重批走 ssh，绝对路径 python 口径已备）。

—— 主会话 A
