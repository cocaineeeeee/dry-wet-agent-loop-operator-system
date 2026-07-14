From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（E2E3 整机复合应力：7 场景 × 7 不变量矩阵——一条 P1 触及 R1-5c 裁定）

## [P1] F1：os 家族 resume 非崩溃等价——单崩溃即触发，需重开 R1-5c

C7 矩阵决定性证据：os/os-soft 在**每个崩溃点** I4 FAIL（best_trusted 0.5124 vs
0.5395 实测漂移），naive/robust/rcgp 三基线臂**全部免疫**（无状态 planner）。
根因逐位定位：`reconcile_redo_rounds` 按设计只回滚物化视图、保留事件日志；但
`TrustAwarePlanner._pending_actions` 从**全量** `action_consumed` 事件判"动作已
消费"——崩溃重做轮读到丢失尝试残留的 4 条 action_consumed，把本应重做的
REMEASURE/DISAMBIGUATION 补救动作全部静默跳过（候选 {arbiter:4,bo:15,sobol:2}
变 {bo:18,sobol:3}），全程无响亮信号。
**修法方向（日志一致性内）**：消费侧过滤——_pending_actions 忽略"最近一次
redo_reconciliation 的 from_round 之后"的 action_consumed（reconcile 事件已带
from_round，零 schema 改动）；或 reconcile 追加补偿事件。配判别测试=C7 式
"崩溃点×os 臂"等价矩阵（最小重现命令在报告）。
**波及评估**：R1-5c "naive/os 逐轮逐位 EQUIVALENT" 需限定重述（当时测的路径未覆盖
"崩溃重做轮含已消费动作"的组合）；在烧的扫描若无节点崩溃不受影响，但凡 resume 过的
os 格建议完工清点时单列核对。方法学注记：注入必须忠实（整份 checkpoint 回滚），
不忠实变体会假报 I5——报告有交叉验证。

## [P2] F2：run_loop 异常路径泄漏 writer.lock fd

取锁后 resume 校验 raise（loop.py:432/436/448）不释放锁 → 同进程"拒绝坏配置→修正
→重试"被自己的泄漏锁误判并发写——恰好击中域指纹守卫本欲支持的运维恢复流。
修：try/finally 或 context manager。独立最小重现在报告。

## 观察项与已核验

O3[P2] 24 轮长战役性能拐点：逐轮 wall-time 0.48s→17.44s（36×，GP O(n³) 无窗口）
——与 IDX3/OS3 的读放大同批考虑；正面：stage FSM 24 轮零振荡、checkpoint 严格单调。
**已核验清单（复合应力下守住的骨架）**：事件日志崩溃+并发下 seq 无交错/半写尾正确
heal/被挡进程零污染；配对、真值隔离、预算恒等（忠实崩溃下 282=282）、6 类非法投递
全响亮、域指纹双拒+逃生门、单写者 flock、基线三臂崩溃逐位等价、改判风暴不双计。
**整机裁定**：审计与安全骨架（I1/I2/I3/I5/I6/I7）在全部叠加场景守住；唯一破裂的是
I4 且仅 os 家族——正是"事件日志不回滚 × 有状态 planner 读全量事件"的组合缝。

报告/脚本/不变量检查器/run 数据：/tmp/claude-1128/dime2e3/。

—— 红队
