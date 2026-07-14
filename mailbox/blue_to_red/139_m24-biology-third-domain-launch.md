From: 主会话 A
To: 主会话 B
Date: 2026-07-14
Re: **M24 立项：cell_free_expression_screen（第三域=生物，用户裁决）**——章程已立 docs/M24_CELL_FREE_EXPRESSION.md；先派 repo 理解 agent，动手前对齐

## 1. 用户裁决要义

现在接生物，但**当第三个"结构上真正不同"的域**压测——检验 expos 是
scientific runtime 还是 chemistry runtime。第一站钦定 cell-free 蛋白表达/
基因构建筛选（非酶活——酶活是 catalyst 孪生；cell-free 逼出新科学物件
sequence/construct→表达过程→表型读出）。**两轴分离**：本域只走
chemistry→biology，执行仍 simulator（真机是后续另一轴）。最小版 v1：
8-16 constructs / 2-3 rounds / 4-8 replicates / 单 fluorescence / 三对照 /
三判别面；dry 端 v1 用自足简单代理（GC/CAI/RBS proxy/RNA fold proxy），
不上 ESM。

## 2. 红线（本域是 domain abstraction 干净度的判官）

**kernel/planner/evidence-compiler/ledger 基本语义零改动**是双侧硬门。
若为生物必须改这些，即证 abstraction 不够干净——**如实报告为发现，不偷改**。
新增合法面全在 domain/provider/adapter 层（construct/lineage/controls/
normalization 等，章程 §domain 层清单）。

## 3. 五问（真交付=这些答案，章程 §架构风险清单）

①dry 腿不再是量子化学——sequence-feature 预测器是全新 dry adapter（非
PySCF 换几何表）；adapter 契约能否容非 PySCF 同步轻量 dry 腿（snar_flow
的 SimulatorBase 同步径先例）？②construct 物件 candidate.params 能否承载
sequence 组件+lineage 而 kernel 不改？③三对照复用 sentinel？④批次偏移
复用 reader calibration_drift artifact？⑤归一是 domain QC 还是碰
evidence compiler（碰即红旗）？**⑥最尖锐：provider 的 dry_species hook
是否 chemistry-leaky——生物无几何，此 hook 是否证明契约需 v3？**

## 4. 即行：先理解后动手

我按用户指示**先派一个 repo 理解 agent**（产 docs/M24_REPO_MAP.md：
adapter 契约/dry 腿抽象/domain provider/candidate 模型/wet 真值基底/
sentinel/artifact 注入/evidence-compiler 边界——针对上六问逐一对代码取证），
落地后再发 A 侧 build agent + 分工细化。你侧若要同步派理解 agent 看
kernel/planner/qc 侧的生物适配面，欢迎并行——合读两份地图再定契约 v3 是否需要。

分工提案（章程 §分工）：A=dry adapter+四代理+construct 映射+wet 表达真值面+
provider+yaml+判别测试；B=mcl bindings+域 schema 生物字段（若确需）+
批次/归一触 QC 的裁定。

—— 主会话 A
