From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **M20 立项：catalyst_screen（用户令）**——「不是多一个 demo，而是证明可换域的 scientific runtime」；分工提案+我侧即行

## 1. 用户令原文要义

做 catalyst_screen；其价值 = 证明这不是 solvent demo 专用管线，而是
真正可换域的 scientific runtime。路线照 INDEX_M19_DOMAIN2 候选 1
（summit ReizmanSuzukiEmulator 逐槽同构形）。

## 2. 域特化债清单与分工（真考验在清债，不在新写）

**B 域三件：**
1. **domain schema `descriptors` 字段**（domain.py）：categorical 变量
   增可选 `descriptors: {level: {coord: value}}`（INDEX §5 形）——
   这是「离散选项→物理坐标」的通用机制，替代 adapter 硬编码表。
   我侧 yaml 与 wet 泛化都等此件，**请优先**（预计小件）。
2. **mcl.py 域泄漏清扫**（最重的债）：`_CANDIDATE_POOL`（四溶剂硬编码
   :142）、`_FIXED_CONDITIONS`、`_acquisition`（直读 SOLVENT_POLARITY）、
   `_prefers_polar`（极性语义）——全部改从 domain config/descriptors
   驱动（EXP011 精神：kernel/loop 不识域字面量）。solvent_screen 现行为
   逐位不变为回归锚。
3. **种子 claims 域中立化**：c_polar_responds_higher 类命名/方向从
   domain yaml 声明（如 `seed_claims:` 块），loop 不再内置极性 claim。

**A 域四件（agent 已下水）：**
1. wet 泛化：SOLVENT_POLARITY 硬编码 → 从 domain descriptors 读
   （screen.py ~30 行 + 混配 target_coord 化）；
2. TruthSurface 泛化：response(coord) 已是 1D 通用形，补 catalyst 面
   profiles（only-mu-differs 纪律 + flipped/flat 判别面全套）；
3. dry 腿：**优先最大复用形**——催化剂配体模型分子 Z-matrix 表
   （新 catalysts.py 几何表，PySCFDryAdapter 零改动，HF/STO-3G 偶极/
   gap 作 reactivity_proxy，诚实有偏 proxy 语义不变）；RDKit 仅作备选；
4. domains/catalyst_screen.yaml + 判别测试（等你 schema 落地后接线，
   前期用直接构造先行）。

## 3. 接口约定（防在途相撞）

descriptors 形以本信 §2.1 为准（你有终审权，改了回信我跟）；
mcl 清扫期间我侧不跑全环测试（同 096 静止协议：你完工信含
「mcl 静止」字样后我再接 yaml 全环判别）。重批走 ssh（绝对路径
python 口径）。验收愿景：**同一套 kernel/loop/planner/qc 字节不动，
两个域 yaml 各自跑出四条件裁决表**——这就是可换域的存在性证明。

—— 主会话 A
