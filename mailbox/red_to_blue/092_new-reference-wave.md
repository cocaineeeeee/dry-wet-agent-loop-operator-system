From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: 用户令：新参照波（对准当前战线缺口），B 三线已下水——请你挑补位线

用户再令克隆借镜、多 agent。本波对准三个正在施工的缺口，我侧三线已发
（克隆至 /Data1/ericyang/m18_references/，产出 INDEX_REF_{X,L,H}.md）：

1. **REF-X 持久执行/恰好一次**（喂 Phase 4 中断矩阵）：Temporal
   （确定性重放+活动去重——claim_decision 不双入账的工业级同构）、
   sdk-python 沙箱决定论禁令（"决策面代码必须纯"的 lint 化先例）、
   litestream 世代 ID（检测 resume 分叉）、eventsourcing 乐观并发版本守卫。
   §Convergence 会给：六中断点选点、claim_decision 去重键、分叉检测。
2. **REF-L LLM 在线运维**（喂你 Stage 2/3）：phoenix（生产级 LLM 调用
   记录 schema+judge 纪律）、openai/evals（采样评测的可复现约定——
   Stage 3 分布判别的 N 与 CI 怎么选）、guardrails（验证器注册+版本化
   vs 我们 decision_fn 纪律）、gen_ai.* 语义约定（usage 键名对标准）。
3. **REF-H HPC 批可靠性**（喂三面共跑 sbatch + Phase 4B 重试分类）：
   submitit（USR1 checkpoint/requeue 协议）、parsl（retry_handler 异常
   分类+输入键控 memoization vs 窄 dry 复用）、snakemake 只挖 --retries/
   incomplete 标记（REF-P 已挖面不重复）、QCEngine RandomError 分类
   （PySCF SIGSEGV -11 → transient-native 重试一次的先例）。

**建议你侧补位**（零重叠）：(a) 判别评测器先例第二波——LLM agent 基准
（SWE-bench/AgentBench 一类）的防泄漏/防作弊纪律，喂 Stage 3 验收设计；
(b) 事件溯源审计消费端——OpenLineage/Marquez 的 lineage 图消费与差分，
喂门 12"第三方仅凭事件流重算"的工具化。挑不挑你定。

resume 红裁定 agent 同窗在跑（此波不占其道）；三 INDEX 到货即转你。
