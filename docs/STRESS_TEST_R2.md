# 建设性健壮性审查 · R2 报告(复核 + 新表面 + 答修复方五问)

> 2026-07-11 深夜。审查方对修复方 `STRESS_TEST_R1_RESPONSE.md` 的复核,叠加 R1 后新一轮
> **17 路独立审查**的整合(F 测试有效性 / G 产物一致性 / H 文档漂移 / I UI 工具面 / J agent 数据流 /
> K RCGP 深审 / L 注入器物理 / M 规模标度 / N 确定性 / P QC 数学 / Q 状态机 / R 备份重现 /
> U provenance / W 新人实操 / X1 架构第一性 / X3 平台路线 / X4 理论形式化;另有 O 书目核对与
> 6 份开源对标证据包)。方法说明:本轮审查 agent 前半程 Fable、后半程按新规一律 Opus;
> 重计算走 sbatch,Slurm 故障后经授权 ssh g209/g208;仓库与 runs/ 全程只读,实验限沙盒。
> **重要时间注记**:修复方在本轮审查期间持续改码(loop.py 最后 mtime 15:58),部分 finding
> 针对的快照可能已被后续修复覆盖——凡此均标注快照时间,修复方按最新码复核即可,不算翻案。

---

## 0. 总裁定

**修复方回应合格,方向与顺序全部正确。** 六轴零驳回、"机制→口径→数字→门面"排序落成 ROADMAP 红线、
H1 按预注册纪律记 deviation、三条意外收获主动披露——这些是独立审查想要的全部姿态。
R1 六轴中:**R1-5、R1-6 基本闭环**(见 §2);**R1-2 单元层闭环、接线层仍有结构性缺口**(F 路变异
实锤,恰好是修复方反问 1 的答案素材);**R1-1/R1-3/R1-4 按约定待 resweep 终判**。

但 resweep 正在跑,而本轮新表面审查发现 **5 件直接影响 resweep 有效性的事**(§1)——
**建议修复方先读 §1 再让 2700 格跑完**,否则部分格子可能白跑。

---

## 1. 【紧急】resweep 进行中必须立刻核对的 5 件事

按"不看会白跑"的程度排序:

### 1.1 drift 场景可能结构性检不出(L-2)——resweep ①线的 drift×5 档预计恒 0
`adapters/artifacts.py:203-213` `injectors_for_round` **每轮返回新实例**,`InstrumentDrift._state`
每轮从 0 重启——实现语义是"轮内相关噪声",不是跨轮漂移。而 R1-2c 修复接线的 CUSUM 检查判的是
**跨轮哨兵对冻结基线的游走**。两者机制正交:checks.py 自己的注释都承认"注入器逐轮重置的恒参
AR(1) 低于该通道信息地板"。**若 resweep 的 drift 场景沿用现注入器,检出率恒 0 是数学必然,
届时会被误读成"能力边界发现"。** 另外 `artifacts.py:164` `applied=|drift|>1e-9` 使 S0 全部
43 孔每轮全亮 drift 真值标签(哪怕偏差 1e-4),任何按标签算的检出/归因分母都被污染。
**行动**:跑 drift 档前二选一——(a) 给有状态注入器加 `persist: true`(按 scenario 缓存实例,
状态跨轮持续);(b) 明写"per-round AR(1) 与跨轮检查正交,预期检出 0,作为诚实盲区呈现"。
同时把 `applied` 判据改 `|drift| > k·sigma`。

### 1.2 rcgp 臂带着"容量税"进 resweep(K-P1)——模型层对照的公平性仍不成立
`models/robust_gp.py:54-56` rcgp 超参是 45 格**各向同性**粗网格;`response_gp.py:72-76` naive 是
**ARD** + L-BFGS ML-II + 10 restarts。沙盒实测同 40 点干净各向异性数据:naive RMSE 0.0122 vs
rcgp **0.2261(差 18 倍)**;实际域(4D 含 categorical/log 维)必然各向异性。rcgp 败给 os 时,
无法区分是"模型层稳健救不了结构偏差"还是"你把 rcgp 实现弱了"——审稿必打。
**行动**(按成本升序):最低限度在 S1 零伪影场景把 rcgp−naive 的 regret 差报为"模型税"基线,
结构场景归因时引用扣除;或坐标轮换细化网格(O(45+3d) 次分解)。**另:强烈建议 resweep 加一条
os-lite 消融臂** = QCPolicy × ReplicateVariance × BaselinePlanner × NullAgent(loop 加一分支)——
现在 os vs rcgp 捆绑了 planner/agent/聚合三处差异,"路由层特异性贡献"归因不纯(K-P2)。

### 1.3 "跨臂同伪影实现"的配对声称当前不成立(N-1)——artifact 种子流是装饰性孤儿
`derive_seed(seed,"artifact",scenario_id)` 全仓唯一出现在 `run_cell.py:57`,只落盘不驱动任何
RNG(零消费者)。伪影实际由 per-round "exec" 流驱动(`loop.py:365`),且与逐孔噪声共用同一 rng、
按布局枚举序消费——不同臂布局不同 → 抽取序发散 → **同一物理参数点的伪影实现跨臂并不逐点相同**。
M9_PROTOCOL:166 的配对可比声称比代码能保证的强。
**行动**:要么真接线(伪影走独立 `default_rng(derive_seed(seed,"artifact",scenario_id,round_id))`,
与噪声解耦——这才配得上"配对"),要么删 seeds.artifact 字段并改协议措辞。resweep 若想主张
臂间配对检验的有效性,这条最好在跑完前定性。

### 1.4 NFS 卷容量风险(M-4a)——2700 格 ≈ 再加 100 万文件,卷已 83% 满
`runs/full_sweep` 现有 **591,771 个文件**(~406 文件/run),全部压在 `192.168.0.160:/volume2/Data1`,
`df -h` 已 **83%**。resweep 2700 格按同布局再加 ~110 万文件/inode。双节点 260 并发同时写小文件
也在放大 NFS getattr 压力(M-2:list_observations 每轮全量重扫在 NFS 上比 tmpfs 实测更陡)。
**行动**:盯 df 水位;若吃紧,新格子改"观测写单 jsonl 流"(M 路建议)或先落节点本地盘再归档。

### 1.5 新检出口径仍继承两个已知偏置(L-1 + G-P1 残留)
修复方把检出改为"注入器专属检查名逐孔配对"(方向对,且发现旧口径**低估**),但两个结构性问题
仍在:(a) **量纲混淆**——注入是乘性、edge/thermal 检出阈值是绝对量,同一幅度档检出率随板值
水平漂移 3–8 倍(L 路实测 edge 0.05 档:低值板 0/30、高值板 15/30),臂间板值轨迹不同还会把
臂差混进检出率;(b) **round-0 排除**——G 路实证 round 0 的真检出(归因 confidence=1.0)被排除、
非当伪影警报被计入,两向偏差部分抵消。glare 通道另议:它读的是模拟器种下的 16σ 分离曝光标记,
检出率=1−(1−p)^43 的二项恒等式,**不含统计推断**,建议在图注明确"独立证据通道,不参与能力比较"
或让足迹与 boost 挂钩制造分布重叠(L-3)。
**行动**:检出曲线横轴改(或并列)"实现绝对效应/板噪声尺度";round-0 计入或注明排除理由;
glare 曲线标注口径。

---

## 2. R1 复验裁定表(对修复方"已修绿"逐条)

图例:✅=审查方独立效果证据确认闭环;⚠️=修复真实但留缺口;⏳=待 resweep,审查方无异议。

| 条目 | 修复方状态 | 审查方复验证据 | 裁定 |
|---|---|---|---|
| R1-2a 折扣恒零 | 已修绿 | F 路变异 A(`_global_artifact_rate`→恒 0)被 `test_risk_discount_generator_scores_differ_from_pure_ucb` 击杀(1F/34P) | ✅ 单元层 |
| R1-2b 风险图恒常数 | 已修绿 | F 路变异 B 单元级被杀(edge_history/batch_checkerboard 两测试红);**但环路级存活:12/12 全绿** | ⚠️ 见 2.1 |
| R1-2c drift 零接线 | 已修绿 | F 路变异 C(分数恒 0)被 `test_temporal_drift_cross_round_detects_aging_instrument` 击杀 | ✅ 单元层(但见 §1.1 注入器侧) |
| R1-2 连锁 LayoutError | 已修绿 | W 路实测:README cmd#1 曾 ~7min 后 LayoutError,现 seed=7/1007 均 35s EXIT 0;`layout.py` docstring 自证 balance_first 修复(mtime 15:07:56) | ✅ 端到端 |
| R1-5a torn-tail | 已修绿 | U 路全量 1450 run 正向链零违例(seq/ts/引用);F 路评 test_crash_consistency 为模范级(崩溃窗口用一次跑完参照对比) | ✅ |
| R1-5b 重做幂等 | 已修绿 | 测试面确认;**但 U 路在 runs/full_sweep 落盘数据发现 3 个崩溃-重做 run reconcile 未触发**(redo_reconciliation=0、同轮 obs 47→94、n_train 灌水 +26~47、model_snapshot 悬空)——数据产生于修复前,**请修复方确认新码覆盖该失效模式**(建议:对这 3 格 `S2.batch_shift.-0.07` naive s1019 / robust s1005/s1006 用新码重放崩溃场景,断言 reconcile 触发) | ⚠️ 待定向复验 |
| R1-5c resume 等价 | 已修绿(逐位 EQUIVALENT) | N 路补充:**科学字段**同种子逐字段全等 + PYTHONHASHSEED 免疫实测成立;但 bit 级产物指纹不可复现,唯一元凶是 `cand_id`(uuid4)混入 trajectory/score 头等字段(N-2)——与"逐位 EQUIVALENT"的口径请对齐(修复方测的应是模型指纹,审查方认可;产物指纹需剥离 uuid 才成立,建议 cand_id 换内容指纹) | ✅(附口径建议) |
| R1-3a/b/c/d 协议 | 待 resweep | G 路已独立复算 V2 report:主表 50 行、stats_tests 20 行逐位命中,B-only 过滤生效;唯 B-only 使低幅档全丢(A 集虚线诊断曲线请落实,report.md 已自注未落地) | ⏳ |
| R1-4 场景补全 | 待 resweep | 无异议;唯 §1.1 的 drift 注入器问题必须先定性 | ⏳ |
| R1-6a/b/c 门面 | 已修绿 | G 路确认 README 数字与 V2 report 一致;**遗留**:regret 主数字 os vs naive p=0.0645(不显著)仍居 headline 首位,而极显著的假最优(p≈7.7e-8)/污染(p<1e-4)排后——建议 M14 主张重写时换主角(与修复方反问 5 的方向一致) | ✅(留 M14 项) |
| P2 批(15 条) | 已修绿 | 抽验:well_cost 下界/override 接线/reclassify 守卫等有判别测试;**Q 路矩阵产生于修复中途(lifecycle.py mtime 04:10),其 Q-1(reclassify 无组合守卫)可能已被"转移合法性表"覆盖——请修复方跑审查方沙盒 `dimq/enum_transitions.py` 复核矩阵应变为 planner/human 各 4 ALLOW/16 DENY** | ⚠️ 复核即闭环 |

### 2.1 复验的核心遗留:机制活性的"接线层"仍无守护(F-1/F-2/F-3)

这是 R2 复验最重要的一条,也直接回答修复方反问 1。三个变异实验(沙盒 `scratchpad/mut/`,可复跑):

- **变异 E**:`plan_round` 返回 `risk_map=None`(断开生产接线)→ **63 passed / 0 failed**。
  修复方新加的单元测试直调 `_plate_risk_map`,越过接线,杀不掉 E。名为 `test_stage_and_risk_map_active`
  的测试没断言 risk_map 一个字。
- **变异 D**:risk_discount 生成分支首行 `raise` → 环路测试全绿——**没有任何测试把闭环真正驱动
  进 failure_aware 阶段**(单轮事件 streak=1<2)。stage FSM 的第三状态是端到端盲区。
- **变异 F-3**:`SoftTrustAggregation._weight` 改恒 1(降权机制完全失效)→ test_loop_soft 六测全绿
  (只断言计数账目,对权重值不敏感)。os-soft 臂区别于"直接吃进污染数据"的唯一实质无人守护。

**单元层修复是真的(变异 A/B/C 都被杀),缺的是环路级效果断言**——这正是 ARCH_V2 §2 机制活性
注册表要解决的问题,审查方全力支持该提案(细化意见见 §4 问 1)。M11 验收⑧(审查方逐条复验)在
接线层断言落地前不应勾掉 R1-2 整轴。

---

## 3. 新表面 findings 总表(17 路整合去重,按修复方批次归属)

完整四段式见各路报告(证据/脚本路径见 §7);此处按"归属批次 × 严重度"给行动清单。
**注**:修复方 P2 批已修的项不重复列;标 ★ 的是与 resweep/论文直接相关的优先项。

### 3.1 归 M11(机制/内核批)

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P1★ | 环路级机制活性断言缺失 | 变异 E/D/F-3 全绿;修法=ARCH_V2 机制活性事件 + 环路断言(建议先落 risk_map/failure_aware/soft_weight 三个) | F-1/2/3 |
| P1 | 畸形提案永久打停闭环 | 接受闸门只查 action 不查 content 类型;`params:"oops"` 被 accept 后因 append-only 重放,`plan_round` 每轮裸 ValueError;修法=`_adjudicate_proposals` 与 `_agent_items` 共用校验函数(docstring 已声称对齐,实际只对齐 2 项) | J-2 |
| P1 | domain yaml 零语义校验 | 15/15 错误变体静默接受;trust 阈值倒置直接改写裁决(全干净观测被裁 SUSPECT)、变量重名产生幻影维度"看似正常";修法=TrustSpec/metric_range/DesignSpace 加 model_validator | J-3 |
| P2 | route_observation 无前置守卫 | 人工改判可被再次路由**静默回滚**(实测:human FAILED 改判后 re-route 回 TRUSTED,零 conflict 留痕),与提案侧翻盘守卫不对称;修法=仅 PENDING 可路由,重路由需 force+conflict 事件 | Q-2 |
| P2 | 空 QCReport 路由为 TRUSTED conf=1.0 | "无证据即满分信任";修法=要求 qc.checks 非空 | Q-4 |
| P2 | suspicion/score/trust_confidence 无 [0,1] 约束 | adjudicate 可产出 conf=7.0,下游 arbiter 排序/os-soft 降权直接消费该值;修法=Field(ge=0,le=1)×3 | Q-3 |
| P2 | reclassify 不更新 trust_confidence | FAILED(conf=1.0) 改判 TRUSTED 后 1.0 语义翻转残留,os-soft 把它当 suspicion 读;修法=置 1.0+payload 记 from_confidence | Q-5 |
| P2 | agent priority 无界+NaN 毒化排序 | priority=1e9 压倒内生补救并替换其消歧几何;NaN 破坏全序;修法=钳 [0,1] 拒 NaN,或同 target 内生恒优先 | J-4 |
| P2 | 阶段 FSM 可达每轮振荡 | trusted_ratio<0.5 持续而 streak=0 时 gp↔failure_aware 逐轮翻转(实测 8 轮 8 翻);修法=回边加 ratio 恢复条件或最小驻留 | J-5 |
| P2 | discounted_scores 对 NaN/inf/越界 p 静默 | 一个 NaN 使全体模型排序被无声归一丢弃;p<0 折扣反转为放大;修法=入口 isfinite+[0,1] 断言 | J-6 |
| P3 | 提案配对三边缘洞 | 幽灵裁定/重复提交双计/多 refs 一票裁俩(U 路全量扫描证实生产数据未触发,属加固) | Q-8 |
| P3 | supersedes 全库无消费者 | docstring 承诺的顶替语义未实现(好消息:agent 借提案翻旧判的路也不通);改文档或实现 | J-7 |
| P3 | 原子写 tmp 名确定性+无 writer 锁跨进程面 | 修复方已加 writer.lock,请确认覆盖"两个 --resume 同目录"场景;tmp 名建议加 pid | M-4b |

### 3.2 归 M12(评测协议批)

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P1★ | 检出曲线量纲混淆 | 乘性注入×绝对阈值,同幅度档检出率随板值漂 3–8 倍;横轴改"绝对效应/噪声尺度"或并列 | L-1 |
| P2★ | 批次 WLS z 实为 t 分布 | "z>3→0.3%"在 n_pairs=4 时实测 5.8%(偏乐观 14×),小板 FPR 全靠幅度地板硬扛;改用 t 分位或声明 | P-4 |
| P2★ | 全干净板 FWER 7.5% 无声明 | ~12 检查并行零校正;至少在协议声明族错误率口径(逐孔 QC 税 1.09% 达标不受影响) | P-6 |
| P2 | 检出 round-0 排除+glare 恒等式口径 | 见 §1.5 | G-P1/L-3 |
| P2 | 评分层不可独立复算 | contaminated_in_training 依赖轮内时点信任快照,外部无法重推;rescore 时 dump 每轮训练集成员清单 | G-2 |
| P2 | 归因弃权率实测 48–78% | 修复方已披露 26–46%,P 路端到端混淆矩阵(7 场景×30 种子)实测更高;batch 真因命中仅 22%、板级门 33%——归因质量按"cause 级"报,弃权率如实进 limitation | P-11 |
| P2 | rcgp 的 training_contamination 定义恒等于 naive | 该臂稳健性不体现在入模筛选;加"有效污染权重占比"列(Σw_contam/Σw,与答问 4 统一) | K-P3 |
| P2 | 归因精度按事件池化、种子内聚簇 | 0.997 的 CI 被低估;加种子级 bootstrap CI 双列 | G-P3 |
| P3 | 格子台账 stray 行 | cells_g209.tsv 混入 1 条与 cells.tsv 重复的 naive 行(870+581=1451);加台账断言 | G-P3b |
| P3 | risk_map 具体批 hint 不层级回退 | 修复方已加边际回退,P 路发现**传具体新铸批标签仍走 _agg_full 空桶塌回全局**(同查询三路径 0.56/0.30/0.56);请确认修复覆盖 hint=具体批 | P-8 |
| P3 | 失败模型边界伪确定性 | 4 次全失败→p=1.0、std=0;p̄∈{0,1} 时 EB 收缩退化;加边界正则或 Jeffreys 垫底 | P-7 |

### 3.3 归 M13(重扫/场景批)——§1 全部 5 条 + 以下

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P2★ | edge 检查参照组被注入器自身污染 | d==0 vs d≥1 对比中 13/20 参照孔是被污染的 d=1 孔(稀释 27%),阈值把稀释吸收进标定——牵连半径与阈值都对着 decay≤1 调;S3.wide_edge(decay 3)会加深参照污染,**恰好测这个**;参照组建议改 d≥2 | L-4 |
| P2 | thermal 空间形状文档-实现不一致 | 文档"中心-边缘"、实现"沿 row 轴单调";m=0.5 首尾比 1.67 达不到声称 2×;按实现改文档或加对称变体 | L-5 |
| P2 | batch_suffix 可达性无预检 | n_solution_batches 默认 1 时 B1 永不命中→场景静默变零伪影;execute 末尾加零命中响亮失败 | L-6 |
| P3 | max(0,·) 截断伪正偏 | clean 板 11.4% 观测被夹为 0;truth 记 clipped 标志供审计 | L-7 |

### 3.4 归 M14/M15(门面与论文批)

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P1★ | 预注册矩阵漂移未出修订版 | 协议 41 配置/n=3/2460 run vs 实跑 19 场景/n=2/1450 格;M9_PROTOCOL 出 v5"实跑矩阵备案"增补节(判据原文不动)——修复方 protocol.yaml 方案(ARCH_V2 §4)是根治,M9v1 追认转录那步就是这条的修复 | H-4/L-9 |
| P1 | ARCHITECTURE 公理 2/3 未更新 os-soft | "结构上不可能进入响应模型"在 os-soft 存在后需加限定句;全文无 soft 一词;论文引用该句会被抓口径 | H-3 |
| P1 | 主张②措辞:服务→布局 | U 路实证:回溯五问全可答但需手撸 90 行 join,无 lineage API;降格措辞或补 explain_best()/trace_obs() | U-2 |
| P2 | 终轮 634 条提案无裁定 | 配对不变量措辞限定为"非终轮"(安全未破);或终轮收口时批量 reject 留痕 | U-3 |
| P2 | checkpoint_version/manifest.json 纸面契约 | 三份规范声称的版本拒载与 manifest 载体全仓无实现;最小落地约数十行,或改标"规划中" | H-1/2 |
| P2 | EVENT_SCHEMA 六处漂移 | checkpoint 事件 round_id 恒 null(键名错位真 bug)/top_cause 类型/routing_bulk 语义/§5 自相矛盾/kind 计数 14→18/字段级 CI 缺口 | H-A 组 |
| P2 | UI 三臂对比页混池反转排序 | 跨场景混池使 pilot_sweep 上 naive 显得优于 os(与主报告矛盾);按 scenario 分面 | I-1 |
| P2 | UI 裁决页显示伪造 acceptance 为"采纳" | 不过滤 actor,与 lifecycle._resolutions 口径分歧——恰打在 demo 第三幕卖点;复用内核口径 | I-2 |
| P2 | 备份代码-数据不同步 | R 路 40 格 sbatch 对账:naive 20/20 bit-exact,os 7/20 对不上(关账前重构的批次估计器进 tar、report 是旧码跑的);升 A 清单 5 条见 R 路报告——M16 REPRODUCE 修复时照单 | R-1 |
| P2 | pytest 依赖未声明+pre-commit 出厂不绿 | 照 README/CONTRIBUTING 字面装完 `pytest -q` 必挂(缺 pytest/hypothesis/streamlit);pre-commit 76 文件待重排+27 ruff;dev extra 补齐+出厂跑绿 | W-2/R-3 |
| P3 | UI 缓存三边界+静默缺行+lint 等价写法缺口+board_grid 负索引+make_demo act3 增长 | I 路清单共 9 条,修复面薄(口径对齐+清单补强),归 M14 打扫 | I 组 |
| P3 | LICENSE 缺失+投稿合规 | 无 LICENSE 阻塞 TMLR/D&B;投 D&B 另缺数据 DOI/Croissant/datasheet;投 TMLR 门槛低得多(O 路 checklist) | O-4 |
| P3 | 引用补号 | Martinez-Cantin 补 1707.05729、RCGP 原始补 2311.00463;应引未引:2507.16833(SDL 噪声检测负结果)、1402.4306(STP);全部 28+2 引用验真无假号 | O-1/2 |

### 3.5 独立成果(供修复方直接取用)

- **X4 理论包**:三态路由=Chow(1970) 三支决策的隐式损失特例(SBB 只保序不校准——损失比数字
  不能写);弱档负结果=可加损失假设失效的理论预测;**4 个可证命题**(P1 污染预算解耦 /
  P2 软硬相变 b²≈τ²(给 os-soft v2 的 w(s) 以理论形状,替代拍脑袋线性斜坡,与 ARCH_V2 §3
  校准契约直接互补)/ P3 聚合盲不可辨识 Le Cam 两点法(「robust 统计不够」的定理级内核,
  但只证到 provenance-aware 必要、不证硬路由必要)/ P4 截断正态 QC 税上界)。推导笔记:
  沙盒 `dimx4/derivation_notes.md`。**建议 M15 直接收编**。
- **X1 架构判定表**:13 项核心决策逐项"本质/合理可改/偶然应换";与 ARCH_V2 高度共振
  (机制活性、协议即代码均独立推出同方案);增量建议:失败模型从"伪影率计数"升"偏差场估计
  b̂(w)±σ,处置菜单加校正后纳入"——这是对"os regret 反输"的架构级根治方向,也是审稿人问
  "为什么不直接建模偏差"的正面答案(可作 M13 后的探索臂)。
- **X3 平台路线**:与 ROADMAP_V2 的"不做清单"八成重合(独立验证了取舍);唯一分歧见 §5。
- **G 路复算脚本**(`scratchpad/recompute_report.py` 等):可直接收编为 aggregate 的独立
  对账工具(M12 验收⑤"逐位一致"的第三方实现)。
- **6 份开源对标证据包**(botorch/Ax 现代 BO、event-model、OpenLineage/plumpy/fireworks、
  postgres WAL/git 原子性、benchmark 平台接口、;含 file:line):jsonl 加行级 CRC(postgres
  xl_crc/git oid 同思路)、event-model 的版本化成例(checkpoint_version 落地参照)、
  OpenLineage facet 映射草案素材——按需取用。

---

## 4. 答修复方五问(STRESS_TEST_R1_RESPONSE §5)

**问 1(机制活性断言下沉内核还是留测试层):内核层,但分两级强度。** F 路变异实验是最硬的论据:
测试层守不住接线层(变异 E 存活 63 测试)。建议:(a) `mechanism_effect` 事件按 ARCH_V2 §2 落内核,
每轮 O(1) 发射,成本可忽略;(b) **硬失败只留给"结构性空转"**——配置声明启用而对象未接线/事件
缺席(lint EXP011 红);(c) "效应恒等"(fired=true 但 magnitude 恒等)**不硬崩**,发黄牌事件由
sweep 级活性断言收口——因为单轮恒等可能是合法状态(干净轮折扣本就≈1),硬崩会制造误报。
运行时误报成本用"三级中只有第 1 级(注册缺席)硬失败"来控制。另请把 F-1/F-2/F-3 的三个变异
脚本(沙盒 `scratchpad/mut/`)收编为活性断言的验收负样本:**新守门必须杀掉这三个变异才算落地**。

**问 2(S3.wide_edge 算不算"未见类型"):不够,算"族内泛化"档,应保留但别独占 H4。**
两个理由:(a) 同为指数/平滑衰减族,审稿人可说签名库的 edge 检查"换个带宽就能追上";
(b) L-4 发现 edge 检查参照组用 d≥1、牵连半径硬编码 d≤1——wide_edge(decay 3)会把参照组
污染得更深,它实际测的是"参照组污染下的退化",这本身有价值但口径要写明。**H4 建议加一个真跨族
配置**:drift×dust 组合(时间结构×点状,两个当前检出最弱的家族)或非单调空间模式(斑块/棋盘格
之外的空间形态)。注意跨族配置必须先解决 §1.1 的 drift 注入器问题,否则组合里 drift 分量恒零。

**问 3(batch 全局效应 truth 标签口径):all-affected 为 truth 主口径,归因质量分层报。**
原则:truth sidecar 记**物理事实**(注入器实际改变了哪些孔的测量值),不迁就归因器——所以
all-affected。归因门槛不会因此形同虚设,因为评分可以分两层:检出层按 all-affected 逐孔配对;
**归因质量层按 cause 级配对**(top_cause 判对该板的主导伪影即计对,不苛求逐孔)。P 路混淆矩阵
(7 场景×30 种子)支持这个拆法:batch 真因 cause 级命中仅 22%、板级门仅 33% 触发——问题不在
标签口径而在批次可辨识性弱(棋盘格×去身份残差的结构性削弱,checks.py 已留档),口径怎么选都
洗不白也冤枉不了它。glare/edge 的 0.13–0.18 溢出同意修复方:是真溢出,照实写。
**附加前置**:L-2 的 `applied=|drift|>1e-9` 全亮标签必须先修,否则 drift 参与的任何口径都被污染。

**问 4(污染率二值 vs 加权):双列,主口径加权。** 加权口径 = Σw·1[contaminated]/Σw,
其中 naive/robust w=1、os 隔离 w=0、os-soft w=alpha 降权值、rcgp w=1/infl 归一(K 路已给同式)。
这是唯一能跨五臂统一定义、且能反映"降权机制生效程度"的口径——os-soft 降权到 w=0.1 的污染观测
计 0.1 个,恰是它该得的。二值口径(降权>0 即算入模)保留为兼容列,供与旧数字对照。加权口径
顺带解决 K-P3(rcgp 的 training_contamination 恒等于 naive 的循环论证)。

**问 5(主张降格是否矫枉过正):诚实必要,而且做对了是升级不是降格。** X4 理论包给了三个支点:
(a) P3 命题(Le Cam)把"robust 统计不够"升为定理级——但它证的是"provenance-aware 必要",
所以主张措辞应是"路由是利用设计侧辨识信息的**一种充分机制**",别写"唯一/必要";
(b) P2 命题给出软硬相变解析边界 b²≈τ²,"regret 保费"从经验事实变成有理论边界的量——
ARCH_V2 §1 的保险合同表述(保费≤X% 换 decision_risk 显著降)与此完全同构,red 队支持;
(c) 即便 resweep 后 regret 改善,deviation 已记,主张一按"结构化伪影下的结论可信性保障
(污染防护/假最优拒斥/可审计),regret 代价有相变条件"定稿——这在 TMLR 是比"更优 BO"
更强的定位,不是退让。唯一提醒:X 保费上限别自由预注册(见 ARCH_V2 反问 4,审查方倾向
"由 S1.zero QC 税上界+P4 截断正态解析上界推导",让 X 有推导而非拍定)。

---

## 5. 对 ARCHITECTURE_V2 / ROADMAP_V2 的审查意见

**总体:两份提案与审查方 X1/X3/X4 三路独立规划高度共振,支持采纳主体。** 具体:

1. **机制活性注册表+第六不变量(§2/§6):强烈支持,审查方认领验收**。落地判据:F 路三变异
   (E/D/权重恒 1)必须全部被新守门击杀;首批注册的 10 个机制里,`exploration_quota` 的生产
   切片当前无任何测试消费(F-8),恰可作第一个接入样板。
2. **协议即代码(§4):支持,补三点**。(a) 修复方自己的反问 2(fn 按名引用锁不住函数体)审查方裁定:
   **必须把 fn 所在文件 sha 纳入闭包**——"无害重构换指纹"的代价是对的,协议指纹本就该对实现
   敏感;重构频繁期可用"protocol_sha256 = schema_sha + fn_files_sha 双列",报告引用时只冻结后者。
   (b) `unrun_is_fail: true` 是全文最好的一行。(c) M9v1 追认转录时,H-4/L-9 的"实跑矩阵与预注册
   漂移"清单可直接作 UNRUN/DEVIATION 的初始种子。
3. **信任枚举三权分立(§3):同意保持枚举,X4 补理论地基**。"数值级失效比类型级失效安静得多"
   与 F/J 路实证一致。suspicion 校准契约请注意 X4 的限定:SBB 分是零假设后验**下界**,校准曲线
   要按"suspicion 分位×truth 伪影率"实测绘制(§3 方案正确),但 w(s) 的理论形状建议用 P2 命题的
   tempered 形式(w*∝σ²/(σ²+n_S·b̂²))替代线性斜坡——os-soft v2 就有了推导而非拍定。
4. **recommendation dossier(§1):支持方向,警惕 grade 通胀**。ARCH_V2 反问 3(无 truth 的
   grade 校准在未见伪影下怎么崩)审查方接单为 **R3 审查点**;先给设计约束:grade 判档规则进
   protocol.yaml(与判定同源冻结),A 档必须要求 `mechanisms_active` 含该场景 expected 集
   (活性与置信绑定,防"机制没跑但打 A")。
5. **模块边界重画(§5):支持,唯一分歧是时机**。X3 路线把"aggregate 收编为一等包"排在
   论文配套阶段(与 ROADMAP M12 一致,无分歧);但 `policies/`/`evidence/` 大搬家建议放
   **M13 重扫之后、M15 之前**——ROADMAP §1 说"ARCH_V2 若改内核必须 M13 前合入",纯移动+垫片
   虽无行为改动,但会使 resweep 期间的 hotfix 双线冲突;协议包例外(它必须 M12 就位)。
6. **ROADMAP 不做清单 8 项:全部同意**(X3 独立得出其中 6 项);唯一补充:第 3 项(血缘导出
   缓)成立,但 U 路的"终轮提案收口"与"3 个崩溃 run 定向复验"不属于血缘导出,别被连带缓掉。
7. **ARCH_V2 反问 1/4/5(假活性攻击/保费活动门/分层信任反例)**:审查方接单为 R3 审查点,
   与 grade 校准(反问 3)共四项,列入 R3 计划(§6)。反问 4 先给方向:见 §4 问 5(c)。

---

## 6. 已核验清单(R2 汇总)与 R3 计划

**本轮已核验稳健(与 finding 同等重要)**:真值隔离字段级严格成立(agent 视图 230 键与 truth 交集
为空,J 路);裁决权矩阵 agent 全 DENY、伪造 acceptance 日志层被忽略(J/Q);1450 run 正向
provenance 链全量零违例、非终轮提案配对精确 1:1(U);事件日志本体 O(n) 无隐藏二次项、
append O(1)(M);全仓无全局 RNG 泄漏、A/B 种子集执行面零越界、PYTHONHASHSEED 免疫(N);
QC 稳健原语大面数学正确(MAD/EWMA/CUSUM/SBB/slope_t 与参考实现逐位,P);RCGP 公式级与文献
一致、零成本鲁棒退化性质精确成立(K);四注入器数学与 docstring 精确一致、场景脚手架逐字节
可复现(L);naive 臂 20/20 格 bit-exact 复现(R);28+2 引用全部验真(O);核心 demo 路径
新人一次跑通、错误路径响亮可解(W);18 事件 kind 全有真实发射点、WAL 三路径 log-before-data
逐条对上(H)。

**R3 计划(绑 M13 落地/M15 终审,按 ROADMAP §3-B)**:
1. resweep 数字落地后:H1' 终判的独立复算(复用 G 路脚本)+ §1 五项的整改验证;
2. 机制活性守门落地后:用 F 路三变异做击杀验收 + 尝试 ARCH_V2 反问 1 的"表演性生效"构造;
3. grade 校准在 S3 留出伪影上的崩坏方式(ARCH_V2 反问 3);
4. 保费上限 X 的可证伪性论证(反问 4)与分层信任反例构造(反问 5);
5. M15 论文数字终审(A 路口径:每个 claim 句 → report 产物/limitation 锚)。

---

## 7. 证据索引

| 路 | 完整报告位置 | 关键脚本/数据 |
|---|---|---|
| F 变异验证 | 本轮会话交付(五变异记录) | 沙盒 `scratchpad/mut/`(变异 A–E 可复跑) |
| G 复算 | 同上 | `scratchpad/recompute_report.py`、`verify_v2_*.py` |
| J/Q 数据流与状态机 | 同上 | `scratchpad/dimj/ t1–t6`、`dimq/enum_transitions.py` |
| L 注入器 | 同上 | `scratchpad/diml/probe_injectors.py`、`probe_results.json` |
| K RCGP | 同上 | `scratchpad/dimk/exp1–exp3` |
| M 标度 | 同上 | `dimm/` + `/Data1/ericyang/dimm_bench/`(sbatch 4667116) |
| N 确定性 | 同上 | `dimn/repro/`(sbatch 4667123,三跑产物+compare.py) |
| P QC 数学 | 同上 | `dimp/exp1–exp4` + `/Data1/ericyang/dimp_pdim_scratch/`(4667124/5) |
| U provenance | 同上 | `dimu/`(sbatch 4667119,全量扫描输出) |
| R 备份 | 同上 | `dimr/` + `/Data1/ericyang/dimr_stage/`(40 格对账,1.9GB 可删) |
| W 新人实操 | 同上 | `dimw/logs/`、`dimw/PROVENANCE.txt` |
| X1/X3/X4/O/H/I | 同上 | `dimx4/derivation_notes.md` 等 |

*审查方基调不变:这是加固不是否定。修复方本轮的姿态(零驳回、按序修、主动披露意外收获、送审查点)
是这个往复该有的样子;§1 五条请在 resweep 收数前过目,其余按批次消化。R3 见。*
