From: 审查方
To: 修复方
Date: 2026-07-12
Re: -（参照锚定轮先行急件：REF-1/2/3 已交付，两 P1 + 三条实质 P2；完整 R5 报告随后）

参照锚定轮（拿前沿开源纪律当镜子）前三路已回。先行转交可动手项；
完整报告等 REF-4/MIR-1~3/R4-J 齐后以 docs/STRESS_TEST_R5.md 寄出。

## [P1] ×2（REF-1，事件模型纪律对标 bluesky event-model / OpenLineage）

1. **终态语义坍缩**：`run_stop.exit_status` 枚举只有 {success}（loop.py:605 硬编码），
   异常路径只释放锁不落终态事件——崩溃/主动中止/逻辑失败在事件流层完全同形，
   `expos check` 只能靠 score.json mtime 旁证推断。修法纯加值非破坏：枚举扩
   {success,abort,fail}（"缺席"保留为第四态 crash）+ run_loop 顶层异常路径落
   run_stop{abort|fail,reason} + scan_view_health 增 terminal 分区消费终态。
   验证：三种终态构造 + kill -9 断言 check 报 crash；"fail 路径改静默"变异必须被杀。
2. **读侧 payload 结构零校验（三层炸点 PoC 已备）**：传输层完好但 payload 损坏的
   事件流——decision 类侥幸有 pydantic 校验；routing 类 KeyError 炸在远端随机读点；
   **grade 拼错类静默折叠成 absent（真 active 机制被读成失活零报错）**。修法对齐
   event-model opt-out 形态：`read_events(validate=False)` 可选闸（默认关零回归），
   check/对账工具默认开，违约收集为 payload_violations 而非硬抛。PoC 转回归用例
   即验证。配套 [P2]：per-kind schema 版本注册表 + payload 按需 pv 字段（缺 grade
   的"合法旧格式"与"损坏新格式"从此可分——R4-H F3 的病根修法）。

## [P2] 三条实质项

3. **REF-3 F1：风险贴现采集是保序变换，选点与纯 UCB 逐点相同**（表演性谱系再现）：
   p_bar 是标量全局率 × 正仿射归一 → argsort 不变（沙盒实证五档 p_bar 全恒等）。
   R1-2a 的守门测试只断言**分数值**不同——值≠效果的教训在采集层复现。真空间避让
   实际全部由布局层 _plate_risk_map 承载。修法二选一：(a) 逐候选风险向量化使折扣
   可重排；(b) 承认风险避让归属布局层，降格 risk_discount 叙述并同步 PAPER 决策层
   表述。**无论哪条，守门测试必须升级为断言选点/排序不同。**
4. **REF-3 F2：模型拟合零遥测**：实测 31% 拟合有 ConvergenceWarning、有 lengthscale
   撞界（=该维被判全局平坦），事件面 model_updated 只有 n_train——与 R4-H F2 同类
   静默面。修：model_fit 事件记逐维撞界+restart 计数，成本极低。
5. **REF-2 F1：agent 补救动作缺消费时现态复核**：内生路径每轮从当前 trust 重新派生
   （自愈），agent 路径消费已 accept 提案时不复核目标观测现态——"同轮先 override
   翻案 TRUSTED、后执行复测"可达。且这违反你我共同维护的 CONTROLLER_MODEL
   不变量⑭（level-triggered 集合差）；AlabOS/MADSci 均有分配前复核。修：
   _pending_actions 消费 agent 项时加 trust∈{SUSPECT,FAILED} 过滤，零 schema。

## 正面（对外叙事素材，随 R5 报告附全表）

- REF-2 五平台交叉验证：append-only 权威/resume/QC 证据信任分类器**无一平台具备**，
  expos 七项领先（两项直接命中综述列为开放难题的能力）；三条"缺口"实为该声明的
  边界（单写者/flock/override push 模型均属正确设计选择，附平台反例佐证）。
- REF-3 十一项稳健：UCB 结构性免疫 LogEI 下溢病理、unit-cube 标准化正确（"0.3 两域
  不等效"疑虑不成立）、f-std 纪律精细正确、KB 批用后验均值正确。
- REF-1 六项领先：seq 单调+torn-tail 愈合、log-before-data、decision 幂等去重
  均为参照系不具备的纪律。

另：REF-1 顺带发现 EVENT_SCHEMA.md 多处写"本轮不实现"但代码已落地（run_start/stop、
torn-tail）——文档落后于代码的漂移，随文档批一并修。

—— 审查方
