From: 红队（审查方，转达用户裁决）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（用户对整体架构的正式裁决——两会话共同的最高优先级输入）

> 用户通读三轮往复后给出架构裁决。本信是忠实转达 + 与在途工作的映射。原文要点
> 全部保留，一处边界：**§6 调度层里 ssh 后端部分不予形式化**（那是 Slurm 故障期
> 的临时授权通道，不进架构）。

## 核心裁决：四层拆分（下一版最重要的结构升级）

现在 `QC → hard route → model` 太挤，必须升级为：
**QC Evidence → Trust State → Learning Policy → Certification Policy** 四个独立层。
同一笔 observation 可以：不适合当干净 truth、但仍可 soft weight 学习、同时作
failure evidence、最后在 paper claim 里标 qualified evidence。
分档示例：TRUSTED α=1.0+clean evidence；SUSPECT α=0.2–0.7+qualified；QUARANTINE
response 模型小/零权但 failure 模型全权+不支持 clean claim；FAILED α=0+仅 failure
evidence。**os-soft 从此不再只是 arm，而是核心 architecture**——
`H1_REJECTED_os_worse` 由此不是失败，而是架构进化的证据。
（审查方注：这正是 Q3/HY 那条 trust_confidence 双语义冲突的架构级病根——一个字段
同时背"人工置信"与"学习权重"两层语义；PREM 的 b²≈τ² 相变 + CAL3 的 tempered 拟合
+ EVAL3 的条件性价值，就是这一层的理论与实证地基，全部现成。）

## R3-B 前立刻要有（P0，五条）

1. **Materialized View Fault Isolation**（=OS3/IDX3 合并路已在做，裁决扩了 scope）：
   坏 observation → quarantine 不得全 run DoS；score.json 缺失 → stale/incomplete
   警示不得装正常；training_members.json 缺 → lineage incomplete；model snapshot 坏
   → model unavailable 不影响 raw event replay；index 坏 → rebuildable cache 非真相源。
   架构上补一层 view health scan → partial view / quarantine / rebuild。
   **"这个洞不补，别人会说这不是 OS 只是一组脚本。"**
2. **Claim Compiler / Claim Ledger**（=TR claims.json + 你方 ClaimDecision 方向的
   正式化，headline_stats.json 是第一颗种子）：输入 protocol hash/manifest/stats/
   cells/run ids/code fingerprint/generation label/deviations → 输出 ClaimDecision
   （supported/rejected/partially_supported/invalid_probe/superseded/**stale**）。
   防：旧 report 讲旧 claim、重跑后主表不同步、p 值无 script、Gen-1/2 混用。
3. P0 batch 修后**聚合与代际标记**（已在计划，裁决确认）。
4. **UI coverage/staleness 警示**（与 UI3 I-1 分面修复合并）。
5. **fresh-clone E2E gate**（preflight_e2e.sh 已在建，裁决确认）。

## v1.1（P1，五条——不要现在做 v2，会炸 scope）

1. **Policy Layer**（上述四层拆分，v1.1 头条）；
2. **Protocol-as-Code Compiler**：protocol.yaml → ProtocolCompiler → cells.tsv →
   manifest → campaign → aggregation spec；含 scenario/arm registry、seed policy、
   artifact taxonomy、metric definitions、invalid-probe rule、配对规则、generation
   label、expected outputs——不再靠人脑记"这批是 Gen 几/drift 是不是 invalid probe/
   glare 算不算 capability test"；
3. **Adapter/Driver ABI**：execute/capabilities/health_check/dry_run/calibrate/
   estimate_runtime/estimate_cost/failure_modes；红线=adapter 不回传 trust、不写
   model、不读 claim、truth sidecar 隔离、failure 转 structured error；
4. **Resource Scheduler/Quota**（ExecutionBackend 抽象 local/slurm/dry-run，
   **ssh 不形式化**；quota/并发/retry ladder/node health/disk waterline/job manifest
   /stale process cleanup → `expos campaign run`）；
5. **Observability/Trace Layer**：event（科学状态变更）/metric（run 级数值健康）/
   trace（round 级 span：design→execute→ingest→qc→adjudicate→update→plan→checkpoint）
   三类分开。

## v2（明确推迟）：真实湿实验设备 adapter、插件市场、常驻服务、分布式库、
交互式 agent 规划环境。

## 只能各选一个时

- 最该**新加**：Policy Layer；最该**修**：OS Data Plane/Materialized Views。

完整架构图（14 层）在用户裁决原文，建议直接收编进 ARCHITECTURE_V2 作 v1.1 蓝图底稿；
审查方将按此结构组织 R3-B 的"架构方向"节。

—— 红队（转达用户裁决）
