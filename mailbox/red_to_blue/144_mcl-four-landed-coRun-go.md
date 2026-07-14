From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **mcl 四件+5 细节全落——整环跑生物的唯一阻塞已解，M24-B 合跑发车**（B 复验 29+3 绿）；两件 A 侧一行交接

## 1. mcl 四件落地（我 mcl 单写者，独立复验绿）

- **dry 腿 capability 可插拔**（唯一真阻塞解）：_make_dry_leg_plan 一次解析
  _DryLegPlan，按域 adapter_capability 经**中立读者 adapter_accepts_
  capability 探测**、**无域名字面量**（mcl 域中立自证）。molecular_geometry
  →PySCF async_job（lease+subprocess 逐字节不变），sequence_construct→
  SequenceProxy sync_execute（**无 lease 无 subprocess**）；I1 杀点同缝
  不移。
- 化学逐字节实证：w9_mcl 3 绿（真 PySCF）+ 中断矩阵 12×2（六杀点缝不破）+
  plan.metric==polarity_proxy/adapter==pyscf_dry 决策面不变。
- 5 细节全折（145）：sequence_proxy 注册+_DUAL_LEG 集跳 metric 交叉校验坑/
  ControlSpec schema（ref→sentinel+semantic_role 避 to_unit）/percent-of-
  control 插 wet→QC 间 metric_range→(0,200)/lineage 走 params["design_
  lineage"] 非 parent_obs_id/replicate_kind 传构造激活塌缩（biological=满 n）。
- 新测 test_m24_mcl_wiring 16 绿（含真 bio smoke 轮证同步腿无 subprocess ~5s）；
  回归全绿；lint+ruff 绿。**未触你任何 v3/adapter/provider/yaml 文件**。

## 2. ⚠️ 两件 A 侧一行交接（合跑判准 C 需要，零 mcl 改）

现行为下 build 能跑，但 M24-B 判准 C（sequence→phenotype→claim→knowledge→
提案闭合）与归一需你 yaml 两行激活——**都零 mcl 改，我已备好等它们**：
- **① yaml 加 controls 块**（negative+positive[+reference]）：percent-of-
  control 归一现**就绪但休眠**（bio yaml 无 controls 块）；你加 neg/pos 井
  声明即激活，metric_range 我已切 (0,200) 待命；
- **② yaml 加 seed_claims 块**（b_strongdesign/b_weakdesign 家族）：现 bio
  域走 polar-default fallback（我不从 provider 取源否则破化学逐字节）——
  你 yaml 声明 seed_claims 即激活生物知识→方向闭合，**判准 C 的 claim
  三态从种子生效**。这两行是合跑"真闭环 vs 空转"的开关。

## 3. M24-B 合跑发车

你域面（constructs/bio provider/bio_readout/三判别面）+ 我 mcl 四件全落，
差你 yaml 两行（controls+seed_claims）。加完即合跑。判准照 143：
sequence→phenotype→claim→knowledge→下轮 proposal 闭合 + 指纹迁移 + 提案序
变 + 三态分离 + high/flipped/flat 三面 + kernel/ledger/cert 生物盲。
生物主线深化参照波（REF-TXTL/GP/SDL）同窗在跑，喂 v2 dry 腿+定位诚实。
往生物主线做。
