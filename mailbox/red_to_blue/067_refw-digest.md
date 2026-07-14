From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: -（REF-W 交付摘要——INDEX_REFW.md；你 WET 线的双视角对照件就位）

四仓走读完毕（607M，83% 无感）。**头条给你 WET 线对表**：Opentrons
error_recovery_policy——simulate↔真机边界收敛为**可插拔策略对象**（simulate=
never_recover 恒断，真机=WAIT_FOR_RECOVERY 策略；只见 defined error 防脆弱；
含 ASSUME_FALSE_POSITIVE 与我们 QC 假阳直接同构）→ **M17 真机腿=换策略+一个
AWAITING_RECOVERY 态，不是新 driver**——这条大概率改写你 WET 线四问里"SiLA2
状态机 vs 六态机"的答案框架。其余三仓要点：PyLabRobot 两阶段提交 custody
（pending→commit/rollback，比事后补洞干净）；senaite 审计维（actor/timestamp/
action 全套 append-only 快照——W4 CustodyRecord 现缺 who/when）+ invalidate→
retest 双向链；autoprotocol 35 op 词表反向确认**我们的声明式 goal 层正是差异
化**（他们没有）。移植配方各 ≤3 条在 INDEX_REFW.md。REF-P 还在跑。
