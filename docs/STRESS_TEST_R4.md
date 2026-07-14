# 健壮性审查 R4 —— 角色对调后首轮全系统审查

- **日期**：2026-07-12
- **审查方**：原修复方会话（bf315d15，2026-07-12 用户裁决对调，mailbox/blue_to_red/024）
- **范围**：十路 Opus 并行——第一波（R3 修复面新鲜视角：A/B/C/D）+ 第二波（法证：E/F）+ 第三波（全系统：G/H/I/J，应用户"完整不局部"指示）。前沿参考库另建（/Data1/ericyang/r4_os_references/，18 仓 + 14 文献 + 全系统架构对照）。
- **纪律**：仓库与 runs/ 只读，实跑限 /tmp/claude-1128/dimr4*/ 沙盒；finding 四段式；已核验清单与 finding 同等重要；复验要效果证据。基调：加固不是否定。
- **先行急件**：两封已提前寄出并获处置——blue_to_red/025（G 两条 P1 + I 一条 P1 + P2 择要；修复方 036 首批已修 P1-1，**审查方复验通过**）、blue_to_red/027（A 的窗口崩溃 P1）。

## §0 严重度总表

| 级别 | 计数 | 条目 |
|---|---|---|
| P0 | 0 | — |
| P1 | 4 | G-F1 账外 headline 漂移（**闭环**）；I-F1 失活预算门空绿（在修）；A-F1 E2E3-F1 窗口崩溃（**闭环**——037 方案 b，审查方独立复跑 EQUIVALENT）；J-F1 干净环境门禁 RED（claim_compiler 测试 skip 守卫查错前提，preflight 段3 单点，一行修） |
| P2 | 12 | G-F3/F4/F5、B-F1、E-F1/F2、I-F2/F3、H-F1/F4、F-措辞、J-F2/F3（详各节） |
| P3 | 18 | 各节详列 |

**总体裁定**：R3 时代的修复实质有效（Gen-3 冻结可完全独立复算、FM3/E2E3 修复机制真实生效、四条结构红线未被补丁潮打穿、新守门测试 9/10 变异击杀）。剩余问题集中在三类：**账外叙事尾部漂移**（ledger 覆盖面之外）、**守护机制的前提/覆盖缺口**（标记门控、遥测覆盖、静态门 scope）、**统计口径 hygiene**（多重比较账目、MC 触底 p、效应量语境）。

---

## §1 R4-A：E2E3-F1 消费侧过滤边界（修复面）

### [P1] A-F1 窗口崩溃：`action_consumed 落账 → save_experiment` 间崩溃不产生 reconcile 标记，读侧过滤退化为修复前行为
- **证据**：写序 planner/policy.py:509-514（事件先落）→ loop.py:518→528（文件后落）；store.py:682-684 零孤儿文件即 return None 不落标记。沙盒实跑（/tmp/claude-1128/dimr4a/repro_window_crash.py，os s81 r3，monkeypatch save_experiment@round1 抛错 + torn tail + resume）：markers=0、盘上 4 条陈旧 action_consumed / 0 experiment、重做轮候选 {arbiter:endogenous:4,bo:15,sobol:2} 崩塌为 {bo:18,sobol:3}——与原 I4 缺陷逐字同签名，零审计信号。
- **为何重要**：修复正确性依赖标记作为权威边界；此相位边界缺席。C7 矩阵只覆盖"整轮已执行、仅 checkpoint 未落"相位。
- **修复**：resume 进重做时无条件落 redo_reconciliation（loop.py:462 或 store.py:684 二选一，removed 计数可为 0）。
- **验证**：复跑 repro 期望 markers=1、分布与 best_trusted==参考；test_resume_equivalence_os.py 补"窗口崩溃"用例 +"连续两次 reconcile"回归。
- **状态**：已随 blue_to_red/027 先行转交。

### 已核验（谓词本身健全，九项）
多次 reconcile 叠加（from_round 增/减/同值重崩）、from_round=0 全回滚、合法消费永不误杀（排除类与回滚边界同构）、标记自身 torn-tail 愈合、os-soft 路径臂无关、round_id 守卫——逐条推演/实跑安全。既有矩阵 24/24 本机复跑通过。问题在标记生成门控（按文件孤儿判断），不在双护栏谓词。

## §2 R4-B：FM3 聚合层次统计（修复面）

### [P2] B-F1 乐观界在层次回退边界丧失"稀疏→保守"语义
- 深层回退继承池化历史紧 std（0.034 < 单点桶 0.164），零本地证据的 band 获得"近乎确定"判断。当前批次跨 band 平稳无实害；非平稳批次（污染时开时关）下会以陈旧均值过度自信。修：回退层 std 膨胀，或 docstring 语义声明。验证：构造跨 band 非平稳场景断言假阳性。

### [P3] ×3
- `FailureModel.risk_map()` 方法仍为修复前行为（hint 走 _agg_full、无 hint 走 batch-marginal，均不经 round-marginal）——生产零调用的语义分叉，未来接线即静默压平批次信号。建议并轨 _agg_hier 或显式弃用。
- 去轮次化隐含"B{k}=空间棋盘奇偶、跨轮平稳"前提（sim_base.py:124 实为 (row+col)%n）——当前统计合法，但字段名暗示物理批次；引入真实逐轮铸新批次时会异质混池。建议前提显式声明 + corpus 不变量测试。
- 剂量响应斜率随聚合口径漂移（+4.29/+4.24/+7.33），弱档近零系诚实边界；引用时需标注口径。

### 已核验（六项）
四级层次互斥无双计；**修复机制真实生效**（fresh-band d(B1−B0)=+0.69 vs 修复前 0.000——修复方未报的补充证据）；桶深 5.88 精确复现、round-marginal 层先验占比仅 17.5%（m=5 无需重标定）；B0 零风险=有证据的干净（tier 层与无知桶区分）；查询/存储键对齐；风险值域在设计区间、弱档恒常图诚实判 warning。

## §3 R4-C：Gen-3 冻结独立复算（修复面）

**裁定：可完全独立复算，冻结面可托付。** 自写脚本从 raw 产物全独立聚合：H1' mean 15 位一致、CI 逐位一致（第三位差异证明系 bootstrap 排序敏感性）、方向 1069/0 逐行一致、消融 8 臂逐位一致、resident 四档一致、污染分母一致；22 项指纹全 match（含聚合脚本 sha 对应当前在盘文件）；claim ledger 证据 sha 逐环验证、claim④ 确实钉 Gen-3 产物、--check 过；诚实披露五项全在案。5 格身份链人工核对自洽。

### [P3] C-F1 deviation 散文数字代际错位
deviations.yaml/claims.yaml 注释中 H1 偏差写 Gen-2 值 +0.01606，所引产物现读 +0.01544——建议就地标注代际，不改任何裁定。

## §4 R4-D：新增守门测试判别性（修复面）

**裁定：三族守门测试实质判别。** 变异×击杀矩阵 9/10：NARR3 四变异全杀（非对称用例与"从 store 独立重算"守门 by construction 击杀）；FB3 的 DEFAULT_K import 时断言实锤触发（两种篡改均导入即炸）、consec-k 语义被 4 测试钉死；coating 两变异全杀。derive_k 独立实现（前向 DP+30 万 MC）三点 k* 精确吻合。仓库 5/5 文件 sha256 无损、逐变异还原核验。

### [P3] ×2
- D-F1（MF2 存活）：Šidák→Bonferroni 全绿存活——k* 整数表只钉方法不敏感点（P(run≥k) 每步 ~4× 陡降，2.5% q 差不跨 k 边界），"Šidák 反解"公式本身未上锁。修：family_q_target≈0.001282 与 pfp_consec_markov 两三点 value-level 断言。
- D-F2：coating `high_conf≥5` 阈值对实测 15 裕度过宽，部分置信度退化（15→6）不触发。修：改比例阈或 confidence==1.0 计数断言。

## §5 R4-E：消融 rc=2 法证（数据完整性）

**定性：干净的全新单跑，非脏目录续跑；Gen-3 无影响面。** 根因=g208 nobatch 分片被并发双启动（两条 SHARD_DONE 480 指纹），每格一胜一负：胜者全新跑完（rc=0），败者被 writer.lock 在**任何写入之前**拦截（rc=2，store.py:134-150 先锁后写）。480/480 全量法证：seq 从 0 连续单调、run_start 恒 1、预算恒等式（obs 376/exp 8）与 g209 控制格逐组一致、零孤儿文件、时间戳单一窗口。r1_resweep 2700 格零同类瞬态。**范围更正**：原完工信自述"少量 rc=2"实为 480 条（立此存照）。

### [P2] ×2 + [P3] ×1
- E-F1 分片级无防重入护栏——本次靠 flock 兜住的是并发重叠；错时重启会走从未实测的 resume 路径。修：分片 flock/pidfile。
- E-F2 完成度断言"rc=0 计数==格数"吞并发失败——正确口径="非零退出=0 且按格去重==预算"。
- E-F3 per-cell log `>` 截断毁首因取证。修：追加式或带 attempt 序号。

## §6 R4-F：os-lite 反常排序定性（科学面）

**判定 (a) 真实科学结论，无装配缺陷。** os-lite 垫底 = os 家族结构档整体反劣（H1_REJECTED，非 os-lite 特有）× 各向同性容量税（+0.0016~0.0019，同/跨 campaign 双口径一致）。判别证据链：工厂逐参数核对无误配（lengthscale 维度与 rcgp 对齐、restarts/bounds/noise 正确）；**税集中在低污染/clean 场景、最高污染下消失反号——数据饥饿×隔离交互解释被数据否证**；沙盒机制确证 ISO 拟合各向异性面 2.46×（n=20）→1.61×（n=80）差（税在闭环早期最重，与分场景分解自洽）；匹配 clean 条件下 os-lite ≥ rcgp，排除"配得更弱"。

### [P2] F-措辞
论文勿用 os-lite−rcgp 声称隔离"路由层贡献"（两臂另有优化器/稳健/先验/noise 四轴差异）；容量税叙事锚定 os vs os-lite（唯一差异=模型工厂的干净单变量隔离）。os-lite 垫底应表述为两效应叠加。[P3] 补一句：税方向经同 campaign 复核（vs os-minus-riskmap +0.00194），比垫底名次更稳。

## §7 R4-G：主张-证据全账（全系统）

### [P1] G-F1（**已修已复验，闭环**）+ [P1] G-F2（同批闭环）
regret p=0.0645 四文档漂移（权威 0.0668 且系 S0.demo 单场景）与 THEORY_P3 的 7.7e-8 旧值——修复方 036 首批六处订正，审查方 grep 复验清零、新值带口径标注与溯源指针。

### [P2] ×3 + [P3] ×1
- G-F3 CLAIM_LEDGER.md §4 示例表自身过期（仍示 claim④ gen12/stale）。
- G-F4 ledger 仅护 4 条主张，对外 headline 远超此数（QC 税、regret 口径、resident 四档、消融排序、P3 定理等账外）——两条 P1 漂移恰全落账外，印证账外区是漂移高发区。修：H3 QC 税与 regret 至少入账；不可证伪主张归"定性主张"白名单。
- G-F5 QC 税四口径并存（0%/2.5%/0.11%/1.09%）无集中对账，README 头排仍用 M5 试点值。
- G-F6 [P3] "batch cause 级命中 15–22%"待与 Gen-3 分档产物对账。

### 已核验（12 项）
四条 ledger 主张 100% 可复算对账；H1 翻案落账完整（删除线+deviation 四处互指）；1450/1000 口径拆解、best 口径统一、消融耦合脚注、普适性措辞抽查未越界、公理自洽。

## §8 R4-H：设计红线与架构结构（全系统）

**六轴裁定：四条核心红线（真值隔离/agent 无裁决权/单一 mode 判定点/五策略注入）经 R1–R3 补丁密集期全部结构成立且多为机器强制**（expos_lint 实跑全绿 + test_expos_lint 31/31）。

### [P2] ×2 + [P3] ×2
- H-F1 EXP001 真值隔离静态门 scope 漏 `expos/design/`（决策路径上"靠约定不靠门"的洞；当前干净）。修：scope 一行增补。
- H-F4 **v1.1 四层拆分提案 §8.8 低估 `trust_confidence` 内核耦合**：该持久字段身兼四职（TRUSTED 置信 / QUARANTINE suspicion 被读作学习权重 / 合成副本上被覆写为 w / arbiter 优先级回退）——Trust State/Learning Policy 真拆分是一次 event-sourced schema 迁移（新字段+旧事件重放+合成副本 alpha 改道），非"策略面 rename"；提案 §8.2 自认病根而 §8.8 降格，内部张力。**建议 v1.1 动工前修订提案并绑定 rebuild 机制排期。**
- H-F2 [P3] QC 通道计算异常→零嫌疑无专属事件（"无静默降级"最软面；有 evidence 持久与 Q-4 缓解）。修：qc_channel_error 事件。
- H-F3 [P3] grade_stream 缺 grade 折叠为 absent——方向保守但 schema 迁移期噪声掩盖真失活。修：单列 n_ungraded。

### 已核验（八项）
truth 唯一产地/读者 + R3 全部新面零触达；failure_model 依赖隔离；agent 无写句柄 + n_submitted 不入裁决；lifecycle 五道守卫；决策六包零 arm-mode 分支；契约键响亮失败；view-health 是"让降级响亮"的正面机制；效果证据三层齐备——expos_lint 实跑全绿 + test_expos_lint 31/31 + 完整后台跑（lint 守门 + 机制活性 + agent 策略变异）**59 passed 0 failed**（初版"沙盒超时保留"已撤销）。

## §9 R4-I：评测协议与统计全链（全系统）

### [P1] I-F1 失活预算门"空绿"（修复方已接受方案 a：abstain + NO_COVERAGE）
should-activate 格（edge≥0.2 os）全为 Gen-2 数据、无 grade 遥测，budget_breached([]) 恒过；带遥测的 600 格全是 batch（正确不入准）——should-activate ∩ 有遥测 = ∅。算法本体无误（正控触红）。

### [P2] ×2 + [P3] ×3
- I-F2 现行 resweep 检出报告缺 §3.5b eff/noise 轴与 §3.5d binary_evidence_channel 标记（被取代的 full_sweep 聚合器反而有——权威报告协议合规度回退）。修：三特征移植。
- I-F3 约 120 个 p 值无族错误率/选择性推断账目（缓解：正式主张仅 4 条、headline p 远超 Bonferroni、H1 系拒绝不虚增有利结论）。修：confirmatory/exploratory 分区声明。
- I-F4 [P3] 两 headline 可信性 p 停在 Gen-1（跨代核验 0.20→0.15/0.0039→0.0040 指标稳定，非实质 stale——建议把该核验入 ledger）；3σ/5σ 口径不统。
- I-F5 [P3] H1 池与 claim④ 的 p=0.0001 系 MC 触底，实际精确 p 远小（claim④≈2⁻²⁰⁰）——报 "≤1e-4" 或套精确枚举。
- I-F6 [P3] H1' 效应量缺相对基线语境：+0.0154 = robust 基线的 +205%（os regret≈3× robust）——绝对值低估负结果强度。

### 已核验（11 项，含两条关键证伪）
**混代配对担心不成立**：配对单元内两臂同代（edge 对双 Gen-2、batch 对双 Gen-3），符号翻转置换交换性保持；剔 batch 拒绝更强。唯一残留假设=跨扫描 os vs naive 配对依赖两代共享代码恒同（仓库非 git 无 SHA 闸），建议登记。置换/bootstrap 实现逐行核验无误；robust 臂非 naive 别名；f* 冻结正确；A/B 分离 0 泄漏；预注册纪律在（消融排序预测"先写后看"且被推翻不改口）。

## §10 R4-J：端到端工程链（全系统）——补遗（2026-07-12 交付）

**核心裁定：R3 修复潮全量集成无合并偏斜。** 干净 clone + 全新 venv（.[dev]）收集 672 测试（文档口径 431 已陈旧），净结果 **1 failed / ~665 passed / ~6 skipped**——唯一失败是环境前提问题（F-1）而非回归，无任何测试间耦合/邻域合并失败。store 收口批隔离复跑 100 passed 确认非中间态。这是本轮最重要的稳健结论（§12 方法学注记的头号担心正式证伪）。

### [P1] J-F1 干净环境门禁 RED：claim_compiler 测试耦合被排除的 runs/ 证据
test_claim_compiler.py:259 `test_real_repo_four_statuses` 的 skip 守卫只查 claims.yaml 存在（clone 里在），不查真正的数据依赖 runs/full_sweep/report/headline_stats.json（clone 排除 runs/）→ 不 skip 反 fail → **preflight_e2e.sh 段3 必 FAIL、五段门禁整体 RED**，投稿/发版门禁前提不成立。这正是 preflight 要防的"本机数据掩盖、干净 runner 炸"，被一个新测试自己触发。修：skip 守卫改查证据文件存在性（一行），或 @requires_runs 标记 + preflight 段3 `-m "not requires_runs"`。验证：clone 无 runs/ 时该用例 SKIP；preflight 五段全 PASS。

### [P2] ×2 + [P3] ×3
- J-F2 pg_board 依赖未声明的 plotly（.[ui] 无此项），测试硬断言 plotly 图元——clean .[ui] 装机红。修：plotly 入 .[ui] extra + 测试容忍 matplotlib 回退。
- J-F3 `verdicts | head` 退 120 + BrokenPipeError 噪声（退出码契约只有 0/1/2/3）——inspect events 的缓解未推广到其他长表分支。修：BrokenPipe 处理提为 _emit 通用。
- J-F4 [P3] 无权限 run 目录暴原始 traceback（应 CliError 退 2）。
- J-F5 [P3] README 安装段不提 .[dev] 却让用户跑 pytest（新人旅程第一段即断）；`llm=["anthropic"]` 为孤儿 extra（全仓零 import）。
- J-F6 [P3] 运行时依赖零上界，fresh-install 已解析到 pandas 3.0.3/numpy 2.5.1——当前全绿是好的前向兼容证据但非锁定；建议保守上界或 lock+REPRODUCE 钉版。

### 已核验（十项）
全量集成无偏斜（头号）；store 收口批一致；**UI 结构上不可能读混代际**（per-cell score.json 单版本，无 UI 页读聚合级 gen2/gen3 文件，grep 证实）；CLI 边界出口码质量（中段损坏 check 退 3 结构性拒修）；override 零写证明（md5 前后一致）；domains validate 响亮；可选依赖降级干净；preflight 段4/5 独立过；依赖声明面无缺失（pillow 真用非孤儿）；--help 0.22s 延迟 import 纪律。

（环境注记：审查期间本机负载 135-220 系并行 agent 群所致，计时类结论不作数；套件结论由主跑 ~90% + 尾段隔离补跑拼齐，方法如实记录。）

## §11 修复优先级建议（依赖序）

1. **A-F1 窗口崩溃**（P1，科学产出静默漂移，修法最小）→ 与 I-F1 abstain 方案（已在修）同批。
2. **H-F4 提案修订**（P2，必须赶在 v1.1 动工前——否则拆分按错误成本模型排期）。
3. G-F3/F4/F5 账目收口 + I-F2 检出报告移植 + I-F3 多重比较声明（文档/聚合层，一批）。
4. B-F1 乐观界语义 + B 系 risk_map() 并轨（FM3 同文件窗口）。
5. E 系工装加固（分片 flock/断言口径/日志追加）+ H-F1 lint scope + D 系测试上锁（value-level 断言、阈值收紧）。
6. P3 长尾按修复方节奏。

## §12 本轮方法学注记

- 角色对调的价值实证：E 路法证直接更正了审查方（前修复方）自己的完工信范围自述与验收口径；A 路抓到修复者自验未覆盖的崩溃相位。"修复者不该自验自己的修复"成立。
- 已核验清单累计 50+ 项，其中两条大担心被证伪（混代配对、rc=2 数据损伤）——证伪与发现同等有价值。
- 前沿参考库（r4_os_references/INDEX.md）含 E2E3 终态语义（event-model run_stop/exit_status）、失败模型稳健化（RCGP 谱系）、v1.1 定位对标（UniLabOS）三条可直接接入修复排期的借鉴。
