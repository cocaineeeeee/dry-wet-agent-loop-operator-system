# 健壮性审查 R5 —— 参照锚定轮（前沿开源纪律对标）

- **日期**：2026-07-12
- **审查方**：bf315d15 会话（对调后）
- **方法**：以 /Data1/ericyang/r4_os_references/（29 仓 + 22 篇文献，截至 2026-07-12 增量完毕）的前沿纪律为镜子，七路 Opus 并行：REF-1 事件模型 / REF-2 实验室平台 / REF-3 BO 决策栈 / REF-4 SPC 统计与测试纪律 / MIR-1 有状态属性机 / MIR-2 multiverse / MIR-3 第三方自足性。先行急件已随 blue_to_red/030、031 转交并全部获接单（红队回执 038/039）。
- **纪律**：仓库与 runs/ 只读；实跑限 /tmp/claude-1128/dim{ref,mir}*/ 沙盒；四段式；已核验清单同权重。

## §0 严重度总表

| 级别 | 计数 | 条目（接单状态见 038/039 回执） |
|---|---|---|
| P0 | 0 | — |
| P1 | 3 | REF1-F1 终态语义坍缩（接单）；REF1-F3 读侧 payload 零校验（接单）；REF4-F3 cusum sd=1 测试盲区（接单，mutmut 实证） |
| P2 | 11 | REF1-F2 schema 版本化、REF2-F1 消费时现态复核、REF2-F2 adapter 终态、REF3-F1 风险贴现保序（表演性谱系）、REF3-F2 拟合零遥测、REF4-F1 derive_h、REF4-F2 信息地板限定、REF4-F4 统计原语零属性测试、MIR1-F1 多-reconcile supersession gap（偏 P3，见 §5）、MIR2-F1 claim③ 子族披露、MIR3-F2 判据规则不自描述 |
| P3 | 12 | REF1-F4/F5/F6、REF2-F3/F4/F5（边界声明类）、REF3-F3/F4/F5、REF4-F5、MIR2-F2 双分母恒等、MIR3-F1/F3/F5/F6（自足性字段类合记） |

**总体裁定**：对标结果两极分明——**核心姿态被外部交叉验证为领先**（append-only 权威/resume/QC 信任分类器五平台无一具备；三条 confirmatory 主张 87 条 multiverse 路径零翻转；UCB 结构性免疫 LogEI 病理），而缺口集中在**"做对了但没证完/没说全"**：终态与版本语义缺枚举、守护机制有静默软面、统计参数拍定未反解、冻结包方法语义不自描述。

## §1 REF-1 事件模型纪律（vs bluesky event-model / OpenLineage / tiled）

- **[P1] 终态坍缩**：run_stop.exit_status 枚举仅 {success}，异常路径不落终态——crash/abort/fail 事件流层同形。修法纯加值：扩枚举 + 顶层异常落 run_stop + "缺席=crash" 第四态 + scan_view_health 增 terminal 分区。
- **[P1] 读侧 payload 零校验**（三层炸点 PoC）：decision 类侥幸有 pydantic；routing 类 KeyError 远端炸；grade 拼错类**静默折叠 absent**。修：read_events(validate=) opt-out 闸。
- [P2] per-kind schema 版本注册表 + payload 按需 pv（R4-H F3 病根修法）；[P2 评估] facet 化作为 trust_confidence 拆分形态参照（加法迁移）；[P3] descriptor 分离不采纳（体量根因是文件数，已由缓存对症）；[P3] schema-由模型生成+每 kind 黄金样例可收编。
- **已核验（expos 领先）**：seq 单调+torn-tail 愈合（参照系不具备）、log-before-data WAL、run_start provably-first、decision 幂等去重、改判追加式。另抓到 EVENT_SCHEMA 文档落后于代码的漂移（"本轮不实现"实已落地）。

## §2 REF-2 实验室平台纪律（vs AlabOS/MADSci/ChemOS/NIMS-OS/UniLabOS）

- **[P2] 补救动作缺消费时现态复核**：agent 路径消费已 accept 提案不复核目标观测当前 trust——违反自订 CONTROLLER_MODEL 不变量⑭；AlabOS/MADSci 均有分配前复核。修：消费时 trust∈{SUSPECT,FAILED} 过滤，零 schema。
- [P2→P3] adapter 执行无失败终态（ExpStatus 无 ERROR/ABORTED），单点故障停整 campaign——缓解已在 ADAPTER_ACTIONS 六态机规划内。
- 三条**声明为边界**（非缺陷）：单写者串行是可审计性之源；flock 当前口径优于 AlabOS 无 TTL 锁（死亡即释放），MADSci TTL-lease 记为多节点升级参照；override push/never-block 是自治优先的有意选择。
- **已核验（七项领先，论文 system 节素材）**：五平台无一 append-only 权威/无 resume/无 QC 证据信任分类器；expos 的事件溯源重放直接满足综述列为"开放"的 rerun-ability；agent 无裁决权在日志层强制（平台侧 AI 提案直送机器人零 vetting）。设计告诫（UniLabOS §5.2）：QC 确证是"数字-物理漂移"防线的承重接头，严格度应写显。

## §3 REF-3 BO 决策栈数值（vs BoTorch/Ax/LogEI/RAHBO）

- **[P2] 风险贴现是保序变换**：标量 p_bar × 仿射归一 → argsort 不变，选点与纯 UCB 逐点相同（五档 p_bar 沙盒实证）——"值≠效果"教训在采集层第三次复现（R1-2a 守门只断言分数不同）。真空间避让全由布局层承载。修：守门升级为断言选点不同 + 二选一（逐候选风险向量化 / 承认归属布局层并同步 PAPER）。
- **[P2] 模型拟合零遥测**：31% 拟合 ConvergenceWarning、lengthscale 撞界（该维被判平坦）事件面不可见——与 R4-H F2 同类静默面。修：model_fit 事件。
- [P3] κ 退火方向与 GP-UCB 理论相反（工程启发可辩护，须写明理由）；[P3] 无跨轮候选去重（后期 nearest 0.0048 近重测）；[P3] RAHBO 术语仅内部注释混用（PAPER 已干净）。
- **已核验（十一项）**：UCB 结构性免疫 LogEI 下溢；unit-cube 标准化正确（"0.3 两域不等效"疑虑不成立）；f-std 纪律精细正确；KB 批用后验均值正确；discounted_scores NaN 响亮；RobustGP 系 RCGP+Plateau-IMQ 忠实实现。

## §4 REF-4 SPC 统计与测试纪律（vs SPC/变点文献 + mutmut）

- **[P1] cusum sd=1 测试盲区**（mutmut 64.7%，221 存活）：全体调用方与测试钉死 sd=1.0/target=0.0 → 标准化核心 `/s→*s` 变异全套件不可见；self-starting 默认路径死代码零覆盖。修三步：非平凡 sd 测试杀整簇 + 死分支决策 + sd-缩放不变性属性测试。
- [P2] drift CUSUM h=6.0 系经验微调：短窗（~5 armed 轮）下稳态 ARL 表不适用、FPR 仿真方法论正确但未成反解——建议 derive_h（derive_k 的 CUSUM 版）。
- [P2] resident"信息地板"须限定为 **CUSUM 家族地板**：0.02 档同误报率下线性斜率 t 检出 0.55 vs 0.35（老化分量是确定性斜坡，趋势统计量局部最优；PELT 阶跃假设垫底被证伪）。建议措辞限定 + drift 并挂线性斜率项。
- [P2] qc/stats.py 十二统计原语零属性测试（逐原语应有不变量清单已列）；[P3] EWMA 文档"双挂"但零调用。
- **已核验**：CUSUM 冻结基线合规（自脱敏担心不成立）；resident 四档检出独立重建逐位复现；稳健原语核心常数变异全杀；derive_k 是"拍定→可证"的范本。

## §5 MIR-1 有状态属性机（hypothesis RuleBasedStateMachine 搜内核组合态）

- **方法学结论（本轮最重）**：撤回 A-F1 修复 → 状态机自动找到并 shrink 出 2 步最小反例（与 A-F1 逐字同签名）——该 P1 本可被自动发现。
- **[P2 偏 P3] 新反例：多-reconcile supersession gap**（已修复代码上 2500×60 搜出，4 步最小，真实 store+谓词确定性复现）：谓词只认最近一条 marker；被早 marker 回滚、此后未重消费的 uid，遇更晚更高 from_round 的 marker 后不再被 supersede（rid < from_round）→ 保留为已消费。与 A-F1 同失败类、走多-reconcile 路径，A-F1 修复不封。危害条件窄（需源观测存活于 from_round 之下 + 补救连续因预算溢出未重消费 + 再遇更高 from_round 崩溃）故非 P1。**候选修法已沙盒验证**：谓词折叠全部 marker（superseded ⟺ ∃M: M.seq>seq_e ∧ M.from_round<=round_e）——2500×60 转绿零新反例，且撤 A-F1 仍红（两修复正交）。注：此反例修正 R4-A 已核验清单第 1 条的适用范围（当时推演的是"重消费"变体）。
- **交付**：可收编测试草案 /tmp/claude-1128/dimmir1/test_property_store_resume.py（判别性开关 + strict-xfail pin + 恒绿回归，默认档 3-6s CI 可承受）——修复方 038 信已表示等此草案建回归锚。
- 已核验：seq/checkpoint/零孤儿三不变量全预算无反例；一条测试脚手架伪缺陷已排除并立此存照（_tail_healed 实例级系生产正确设计）。

## §6 MIR-2 multiverse 主张稳健性（vs Forking-Paths 文献）

- **强核验**：claim ① 36 路径全同向（jackknife 最坏 p=4e-6）；claim ② 12 路径全同向（5σ 列加强至 effect=−1.0）；claim ③ 39 路径拒斥方向零翻转（os 显著更优出现 0 次），现行池比 edge_only 更保守非择优。"confirmatory 结论是被选中的 exploratory 路径"正式证伪。逐路径台账 /tmp/claude-1128/dimmir2/spec_curve_*.json 可入 ledger 作稳健性证据。
- [P2] claim ③ 显著性由 edge 子族驱动、batch_only 双场景 5/6 路径 NS——披露完整性：散文补子族分解。[P3] 双分母对 os/naive/robust 结构恒等（仅 os-soft 分叉），勿充数为稳健性维度。
- 建议固化敏感性列：①② 的 5σ 列、③ 的 edge/batch 子族分解列 + all_S2r3 列；权威 p 以 exact 置换为准（MC 触底 1e-4 标注下限语义）。

## §7 MIR-3 冻结包第三方自足性（vs RO-Crate Run Crate / Croissant / PROV）

- **完整性自足**（sha 链 22 项第三方可核、Gen-3 子集算术自洽），**方法与关系语义不自足**：六条断链——[高] probe_direction.py 三处引用全仓不存在（**修复方 039 已收编原件入 _tools/，pin+运行存档排账目批**）；[中] 混代 CSV 无机读 scope 谓词（盲求和得 inverted=117 表面矛盾）；[中高] 方向判据规则只活在代码；[中] decision_fn 全语义出包；[中低] m12 产出关系未声明；[低] campaign 锚指向包外备份。修法合计"十来个字段+一个文件"。
- 交付：可机器跑的自足性检查单 C1-C7（C1 完整性今已达标；C4/C5 hard；C2/C3/C6 soft）——修复方已接单收编 claim_compiler --check。
- Run Crate 差距表：object/result 完备；缺 action/agent/instrument 关系骨架与 conformsTo 分级。

## §8 修复优先级建议（合并 038/039 派工序）

1. I-F1 空绿（在修）→ 终态语义 + payload 校验（同批，事件模型面）。
2. 回归锚批：MIR-1 草案 + 窗口崩溃/双 reconcile 用例 + **supersession gap 候选修法**（谓词折叠全部 marker，沙盒已验证，建议随回归锚同批落地）。
3. qc/stats 加固批：cusum 盲区三步 + 十二原语属性测试（同文件同窗）。
4. REF-3 F1/F2 + REF-2 F1（决策面三件）。
5. 账目批：MIR-3 六字段 + probe_direction pin + MIR-2 敏感性列 + claim③ 子族分解。
6. 边界声明批（REF-2 三条 + REF-3 κ 理由）与 P3 长尾。

## §9 方法学注记

- **自动搜索 vs 人工推演的分工实证**：R4-A 人工推演九项边界全对，但漏了"未重消费+递增 from_round"变体；MIR-1 状态机 10 秒内搜到。结论：谓词类组合态缺口应默认配属性机，人工推演负责刻画不变量与忠实性红线（状态机的两处忠实性约束正是人工产物——二者互补而非替代）。
- **参照锚定审查的产出结构**：每路"finding + 领先项"双清单——缺口给修复方，领先项给论文 system 节。五平台/三文献体系交叉验证的领先项（append-only 权威、日志层裁决权强制、multiverse 零翻转）是本轮对外叙事的最大增量。
- 全程仓库与 runs/ 只读；全部先行急件在完整报告前 24 小时内已获接单或即时修复——信箱直通把"报告→修复"延迟压到小时级。
