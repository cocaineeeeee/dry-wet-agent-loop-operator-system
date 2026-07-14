From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: -（W4 wet 仪器栈落仓——A 域首批内容，22/22 + lint 全绿）

## 已落 expos/adapters/wet/ + tests/test_w8_wet_stack.py

- **构件**：`sim_reader.py`（读板仿真器独立进程，TCP loopback + NDJSON——
  只有活线材才能忠实模拟 offline/慢响应/kill/并发 health 四种设备行为）+
  `driver.py`（WetDriver，ADAPTER_ACTIONS 六态机 + action_goal/state/feedback/
  result 事件）+ `ot_protocol.py`（**真 Opentrons 9.1.0** simulate 腿，
  labware/deck/volume/tip 全真校验；同接口 fallback 校验器带 OT_BACKEND
  降级申报）+ `protocol_spec.py` + `bridge.py`（raw→ExecutionResult，
  喂现有 raw_to_observations 零改动）。
- **仪器七件全实现**：health（可注入 degraded/offline）/ calibration
  （gain-offset 漂移模型，未按期校准读数带可检出偏差）/ reservation（单租户
  设备租约+TTL 自清）/ protocol validation（真 OT 栈响亮拒非法）/
  timeout+retry（预算穷尽→ABORTED）/ device failure（kill 重连失败→ABORTED、
  错误码、部分 dropout→SUCCEEDED+可见 null）/ custody（四段链一条
  `CustodyChain.trace()` 可查，伪造 sample_id 拒收）。
- **落仓时被我们自己的门拦了三类，全部当场修正**（lint 的价值时刻）：
  EXP004×4——仿真器持隐藏真值面与 sim_base 同角色，按现行规则**正名**
  `reader_sim.py→sim_reader.py`、`harvest_truth` 从 bridge 移入 sim 文件、
  bridge 彻底去真值化（EXP004 合法产地语义无需改规则）；EXP005×3——三处
  静默 pass 补 warning 日志。终态：**22/22 passed + expos-lint 全绿**。
- 与 ③ 的接口就位：failed/dropout/canceled 孔即 wet 侧 ABSENT/ERROR kind
  生产者（cause 逐事件区分）；与 W1 的双层租约（runtime 持调度租约、设备
  自持安全联锁租约）在 INTEGRATION.md（沙盒 /tmp/claude-1128/dimw4/）有全说明。
- 顺带观察：刚才 lint 一度报 leases.py:206 静默 pass、现已消失——猜你在修
  互斥批时顺手处理了；若非有意，提醒核对。

W3（PySCF）在建中已改为复用你的 expos/scheduler。它落地后即开 W5 域 profile。

—— 主会话 A
