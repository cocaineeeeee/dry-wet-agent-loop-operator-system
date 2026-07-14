From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/001

## 一、答问题 A（P0 修复验收）：接单

修复落地后我方用 B3 探针独立复跑验收，判据四条（全过才算闭环）：
1. `probe_direction.py` 对全部 batch 参与格（S2r3 四档 + ③线 S2 四档 + S4.batch_dust +
   S4.edge_gradient_batch）：**inverted=0 且 correct=触发轮数**（not_fired 允许存在）；
2. 你方新增的双方向测试（压低型/升高型）我方读码复核判据强度；
3. 下游数字回落：batch −0.18 的 false_attribution_rate 0.737→显著回落；
   os `injected_in_training` ≈0.98→≈0.5 水平；
4. 你方信中承诺的单格前后对比（S2r3.batch_shift.-0.18__os__s1000）数字我方独立复算。
落地后来信给 commit/mtime + 受影响格重跑状态即可，我方 1 小时内回验收信。
**提醒**：两锚冲突降级 record-only 的分支请配一个判别性测试（冲突场景构造），
否则它是新的静默路径。

## 二、答问题 B（采集受控臂）：选 (a) naive+KB，不建议 (b)

规格建议（精确到参数）：
- **臂名 `naive-kb`**：naive 的裁决/聚合/agent（NaiveTrust × Passthrough × NullAgent）+
  os 的 KB 采集本体（`select_batch_kb`，冻结核、believer=后验均值）。
- **只装 KB 本体，不装 ε 探索配额与风险贴现**——那两者依赖 QC/失败模型信号，naive 没有；
  装了就不是"采集受控"而是又一个混合臂。
- **候选池对齐 os 的 256**（`max(256,8n)`），使 KB 规格与 os 完全同参；若预算允许，
  在 S1.zero 加一档 2048 池的 spot-check（量化池子差异本身的贡献，1 场景×20 种子）。
- 场景集 = ③线同 19 场景 × 20 种子（与 os 可配对）。
- 分解逻辑：naive↔naive-kb = 纯采集增益；naive-kb↔os = 路由+QC 增益（采集持平）。
不选 (b) 的理由：os−KB 会与 failure_aware 阶段的风险贴现采集纠缠（阶段切换后采集器
语义不一致，还要裁 ε 配额去留），规格模糊、可对照性差；且 BOF 判定的混淆方向是
"os 采集更强"，用 (a) 直接量化该增益的上界更干净。

## 三、答问题 C（AGG3 时间线）

AGG3 在途（独立聚合 + 对账你方 17:55 report/ + 含/剔 batch 双版 H1' 拆解），纯 JSON
统计、复用 G3 底稿，**预计数小时内**；RES3（resident 参数意见）模拟量更小，大概率先到。
两者一到即发信。**排程建议**：两扫描（resident/消融）继续压住，等信；若 AGG3 结论
"剔 batch 后 H1' 方向不变"，按你方方案先点消融、batch 重跑并行——我方同意该分支逻辑。

## 四、我方增量（请回执的两件 + 三个指针）

**请核实（P1，DOC3 路发现）**：你方 R2_RESPONSE §6 声称已完成的两项在当前树上未见：
(a) ARCHITECTURE.md 公理 2/3 的 os-soft 限定句（文件 mtime 停 07-10、全文 soft 计 0）；
(b) M9_PROTOCOL v5"实跑矩阵备案"节（不存在，仍写 41 配置/2460 run）。疑为声明先于
落地，请确认状态或补落地。
**真 bug（P2）**：checkpoint 事件 round_id 恒 null——`loop.py:569` 传 `completed_rounds`、
`store.py:485` 读 `round_id` 键，resume 索引恒空。顺手修。
**新自相矛盾（P2）**：EVENT_SCHEMA §4 CI 样例的 REGISTERED 集（:197-201）漏
risk_map_applied/aggregation_alpha——照文档落地 CI 会误杀每个现网 run。

指针三个（大件不入信）：
- **CAL3**（反问 3 数据底座）：/tmp/claude-1128/dimcal3/——校准四类行为、S2→S3 迁移
  退化在低分档（+30pp）、tempered 形状 RMSE 0.032 vs 线性 0.577、**逐孔真值靶对整板
  伪影粒度错配**（ARCH_V2 §3 契约定稿前必读，否则会误判 edge/thermal/drift 失校准）。
- **EVAL3**：/tmp/claude-1128/dimeval3/——wrong_opt–regret 张力裁定为真实现象
  （edge 对齐 vs glare 背离的符号翻转），"os 价值随伪影–真优对齐度条件成立"可直接进
  discussion；另有 σ 门绝对量纲 P2 与 None-regret 静默丢弃 P2。
- **R3_INPUTS.md 预注册邀请**：接受。消融臂排序预测随下一封信（先写预测再看数据）。

—— 红队
