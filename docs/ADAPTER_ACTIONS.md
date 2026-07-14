# ADAPTER_ACTIONS.md — 长任务执行 adapter 的 goal/feedback/result 动作语义

> 状态：**设计稿，post-M10 落地**（2026-07-10）。CONTROLLER_MODEL.md §6「BenchAdapter
> 长任务草案」的具体化：把 ROS2 action 的 goal 状态机具体映射到 expos 事件词表，给出
> 三事件 payload schema、频控纪律、cancel×信任路由交互、sim 零迁移证明。**不新增特性、
> 不改代码**——当前 sim adapter 的阻塞 `execute(exp,rng)->ExecutionResult` 保持不变
> （`expos/adapters/base.py:61`）。权威蓝图以 ARCHITECTURE.md 为准，治理红线以
> OS_PRINCIPLES §12 为准，与 CONTROLLER_MODEL.md 冲突时以后者的不变量①-⑫为准。

## 0. 概念来源与不 copy 清单

借 ROS2 action **概念**（学状态机，不抄 DDS 实现）：
- goal 状态机与 server 端迁移：`references/rclpy/rclpy/rclpy/action/server.py:245-258`
  （`succeed`/`abort`/`canceled` = `GoalEvent.SUCCEED/ABORT/CANCELED`）、`:237-243`（`executing`）、
  `:176-190`（`_update_state`：迁移即发布状态 + 终态通知）。
- feedback 只在 EXECUTING 态发布：`server.py:213-235`（非 executing 态 `publish_feedback`
  直接 warn 并丢弃）。
- cancel 语义：`server.py:165-167`（`is_cancel_requested`）、`:509-511`（`CANCEL_GOAL` 迁移）；
  client 侧 `references/rclpy/rclpy/rclpy/action/client.py:139-168`（`cancel_goal`/`get_result`）、
  `:556-559`（`send_goal_async(feedback_callback=…)`）。
- 设计文（design.ros2.org/articles/actions.html）：六态机 + 「result 一次终态 / feedback 连续进度」
  分离 + client 生成 goal_id。

**不 copy 清单（单机回合制不需要）**：DDS/QoS/中间件、topic+service 三服务两话题的传输分层、
分布式 discovery/introspection、result 缓存超时与多 client 并发取 result、client 端 UUID 防碰撞
（expos goal_id 直接 = `exp_id + round_id`，单写者无碰撞）、goal 队列并发调度（loop 顺序驱动，
一次一 goal）。**只借状态机语义与「进度/终态」二分，不借其传输与并发设施。**

## 1. 动作生命周期状态机（ROS2 六态 → expos 事件）

一次「投一轮 ExecutionReq 到长执行台面」= 一个 goal。状态机（非持久，仅 adapter 侧内存 +
落 events.jsonl 留痕）：

```
        send_goal            execute            succeed
 (none) ─────────► ACCEPTED ────────► EXECUTING ─────────► SUCCEEDED  ┐
                      │                  │      └─(feedback*)          │ 终态
                      │ cancel           │ cancel/timeout    abort     │
                      ▼                  ▼          ┌────────► ABORTED  ┤
                   CANCELING ────────────┴──────────┘ canceled         │
                      └──────────────────────────────────► CANCELED ───┘
```

- **ACCEPTED**：adapter 收下 goal（=ExperimentObject.execution_req 快照），未起测。
- **EXECUTING**：台面运行中，唯一允许发 feedback 的态（照 `server.py:222`）。
- **CANCELING**：收到 cancel，已 ack、允许清理（放冷却/存部分结果），未落终态。
- **SUCCEEDED / ABORTED / CANCELED**：三终态，各追加一条 `action_result`。

三事件映射（均 kind=`testing`，Since=下一个 checkpoint_version；登记入 EVENT_SCHEMA §1）：

### action_goal — 长任务提交（进 ACCEPTED）
| 字段 | 类型 |
|---|---|
| goal_id | str（`{exp_id}:{round_id}`，稳定可重解析） |
| exp_id / round_id | str / int |
| adapter | str（ExecutionReq.adapter） |
| n_batches | int |
语义：投递 desired execution_req 快照，goal 不可变（不变量⑪：adapter 不得变异 ExperimentObject）。

### action_feedback — 非终态进度心跳（仅 EXECUTING）
| goal_id / round_id | str / int |
| phase | str（自由，非 ABI：如 "dispense"/"image"） |
| fraction | float∈[0,1] |
语义：纯进度上报；**不得触碰 trust/routing/status**，非 observation、非 decision。频控见 §3。

### action_result — 终态回灌（三终态之一）
| goal_id / round_id | str / int |
| outcome | enum{SUCCEEDED,ABORTED,CANCELED} |
| reason | str\|null（ABORTED/CANCELED 必填，SUCCEEDED 为 null） |
| n_raw | int（回灌 RawResult 条数，可 0） |
语义：终态标记 + 产物游标。raw/truth **走现有 ingest**（`raw_to_observations`+`store.save_truth`），
不透明透传，绝不绕过 `adjudicate`（不变量⑪：truth 仍只写 sidecar）。

## 2. 与 ExpStatus 的关系（板级不动，动作级是子粒度）

`ExpStatus`（DESIGNED→EXECUTED→QC_DONE→ROUTED→CLOSED，`objects.py:27`）是**板级实验对象**
生命周期，**不动、不新增态**。动作状态机是 **EXECUTED 这一步内部的子粒度**：
DESIGNED 后投 goal，goal 走 ACCEPTED→EXECUTING→SUCCEEDED 期间 `exp.status` 仍是 DESIGNED；
只有 `action_result(SUCCEEDED)` 且 raw 落账后，才由 `lifecycle.advance_status` 迁到 EXECUTED。
即：**动作态是 EXECUTED 转移的「执行凭证」，两层正交**——`status_transition` 事件记板级、
`action_*` 记子粒度，consumer 不得混用。ABORTED/CANCELED 则 exp 停在 DESIGNED，本轮不进 EXECUTED。

## 3. feedback 频控纪律（防日志洪泛）

feedback 可秒级高频，直写 events.jsonl 会淹没日志、拖垮 resume 重放（printk 洪泛教训，§13.1）。
纪律（adapter 侧强制，进 events.jsonl 前）：
1. **节流**：同一 goal 的 `action_feedback` 最小间隔 T_min（拟默认 ≥5s）落盘；间隔内的中间帧丢弃。
2. **最后值语义（LVC）**：feedback 是可丢弃快照，非累积日志——漏掉中间 fraction 无害，只保证
   「最新进度」最终可见；resume 从不重放 feedback（它不进派生态，只供 UI 观测）。
3. **单调裁剪**：fraction 非单调回退帧丢弃（防抖动刷屏）。
4. **绝不作 trust 输入**：CI 守门断言 feedback payload 不含 trust/routing 键（防 consumer 误依赖）。

## 4. cancel 语义与信任路由交互（绝不静默丢）

cancel（human override 经 `overrides/pending/` 文件通道，不变量⑩单写者；或 timeout 由 adapter 自触）
→ goal 入 CANCELING → adapter 清理 → `action_result(outcome=CANCELED, reason="canceled"|"timeout")`。
被取消/超时的孔**必须落为可见失败观测**：对未完成孔生成 `RawResult(value=null)` 经 `adjudicate`
→ trust=**FAILED**、routing=QUARANTINE，并在 routing 事件 evidence 记 `reason=canceled`。
**绝不静默丢孔**（不变量⑫无静默回退）：取消不是「这些孔不存在」，是「这些孔失败且原因已知」，
下轮 planner 可据此重排。重试策略在 **adapter 侧**（Temporal 五字段 / systemd Restart+StartLimit），
非内核侧；resume 时按不变量③④对被取消轮**幂等重做**，不叠加双份真值（`save_truth` 覆盖写）。

## 5. sim 适配器兼容路径（零迁移成本证明）

现 sim adapter 的阻塞 `execute(exp,rng)->ExecutionResult` **一行不改**。长任务接口只是它的
**超集包装**：loop 侧一个薄 shim 把阻塞调用包成「单 goal 立即 SUCCEEDED」——
```
ACCEPTED → EXECUTING → SUCCEEDED   # 同一 tick 内塌缩，无 feedback，无 CANCELING
result = ExecutionResult(sim.execute(exp, rng))   # 原样透传
```
即 sim 是长任务语义的**退化特例**（feedback 空集、cancel 不可达、单终态 SUCCEEDED）。故新语义
对 sim **零迁移**：不接 BenchAdapter 时，loop 走同步分支，`action_*` 三事件可选（sim 下默认不发，
或发 goal+result 两条、feedback 恒空）。这证明「阻塞 execute」与「长任务 goal」是同一状态机的
两个投影，接真实台面时只是把 EXECUTING 从「零时长」展开成「可 feedback/可 cancel 的实时段」。

## 6. 处置与里程碑归属

| 项 | 处置 | 里程碑 |
|---|---|---|
| 六态机 + action_goal/feedback/result 三事件 schema（§1） | 本文 design note，登记 EVENT_SCHEMA testing 待落地 | post-M10 |
| ExpStatus 正交子粒度（§2）、feedback 频控（§3） | design note，追认 ExpStatus 不动 | post-M10 |
| cancel→FAILED(reason=canceled) 信任路由（§4） | design note，绑不变量③④⑪⑫ | post-M10 |
| sim 阻塞→单 goal 退化包装（§5） | 零迁移证明，当前 sim 路径不改 | 追认现状 |

本文不新增任何当前里程碑内实现承诺；三事件晋级 testing→stable 须待真实 BenchAdapter 接线 +
§5 CI 覆盖（EVENT_SCHEMA §2 准入流程）。

## 7. 前沿对标与已知缺口（LAP, arXiv:2606.03755）

前沿调研（scratchpad/frontier_A_actions.md）确认本文与 2026 前沿无冲突；三处缺口登记如下，
**均 post-M10**：

1. **partial-result 语义**：长任务中途已可用的部分观测怎么落账。建议：以 `action_feedback`
   携带 `partial=true` 的**只读预览**（非 ABI 附加键，供 UI/agent 观望）；正式观测**仍只在
   `action_result` 终态后走 ingest**——半成品绝不进 `adjudicate` 裁决（防未定型数据污染
   trust/routing；与 §3「feedback 不作 trust 输入」同一红线的正向补全）。
2. **RECALIBRATE 动作类目 + 漂移反馈通道**：失败模型若归因 `instrument_drift`，应能建议下轮
   `RECALIBRATE` 动作（ActionType 新枚举值，走 M7 仲裁器既有预算通道）。借 GANIL 自监督校准
   （arXiv:2606.29466）「校准系数当时间序可观测量」思路：校准结果作为普通观测落账
   （metric=calibration_*），使漂移本身可建模、可追溯，而非仪器侧静默自愈。
3. **cancel 的 accept/reject 握手**：借 ROS2 `CancelResponse.ACCEPT/REJECT`
   （`references/rclpy/rclpy/rclpy/action/server.py:101-105`，默认回调即 REJECT `:282-284`，
   仅 ACCEPT 才迁 CANCELING `:506`）：adapter 处于不可中断段（如离心中）可**拒绝取消**；
   拒绝必须追加事件留痕（拟 `action_cancel_rejected`：goal_id/round_id/reason），绝不静默
   吞掉取消请求（不变量⑫）——§4 的 cancel 流程据此细化为「请求→握手→(ACCEPT 才)CANCELING」。
