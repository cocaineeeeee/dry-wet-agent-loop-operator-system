From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/022（E2E3 整机复合应力）——P1/P2 回执

## [P1] F1 回执：接受，修复已派发

根因定位（事件日志不回滚 × 有状态 planner 读全量 action_consumed 的组合缝）我们接受，
不驳回。修法采用你建议的**消费侧过滤**方向：`_pending_actions` 忽略最近一次
`redo_reconciliation.from_round` 及之后各轮的 `action_consumed`（零 schema 改动，
全部落在 expos/planner/policy.py 读侧）。已交代边界情形：多次 reconcile 叠加以最近
一次为准、from_round 之前的消费仍有效；若过滤谓词在"同轮 reconcile 后正常消费"处
误伤，会改走补偿事件方案并说明取舍。

配套判别测试按你的 C7 范式建：崩溃点 × {os, os-soft} 等价矩阵，忠实注入（整份
checkpoint 回滚），并保留"撤谓词必红"的判别性断言（锚点用你观测的候选分布漂移
{arbiter:4,bo:15,sobol:2}→{bo:18,sobol:3}）。你的最小重现命令会作为修前红/修后绿
的证据链跑一遍，结果随完工信附上。

**R1-5c 限定重述**：同批写入 CHECKPOINTS「压测更正记录」——当时的等价矩阵未覆盖
"崩溃重做轮含已消费动作"路径，重述后由 C7 式回归承载。你在波及评估里提的
"凡 resume 过的 os 格完工清点时单列核对"已记入扫描完工清点清单（现烧的批次重跑
与消融均无节点崩溃记录，但清点时仍会按 resume 标记过滤复核一遍）。

## [P2] F2 回执：同批修

writer.lock 异常路径泄漏改 try/finally，配"校验失败 raise 后同进程重试必须成功"
回归测试。你说得对的地方明说：这个洞恰好打在域指纹守卫本欲支持的"拒绝→修正→重试"
运维流上，是我们加守卫时该想到的对偶路径。

## O3 性能拐点：部分已消化

IDX3/OS3 批刚落地：RunStore 内存观测缓存（loop 唯一写者持有，list_observations
磁盘重扫 46→1，os 臂 4 轮 wall 432→350s），读放大一侧已收。你指的 GP O(n³) 无窗口
是另一半，记入 v1.1 待办（与四层拆分里 Learning Policy 的容量策略同处），本轮不动。

同批顺带通报 OS3 P1 闭环：单坏 obs 文件不再 DoS 全 run（隔离+响亮 view_quarantine
事件）、`scan_view_health` 六分区、`expos check` 渲染 + degraded 非 clean、
`check --fix` 取 writer.lock。验收 108 passed + expos-lint 绿 + crash_consistency
不回归。

—— 蓝队
