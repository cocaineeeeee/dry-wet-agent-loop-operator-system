# expos —— 生物主线的自适应「干–湿–agent」研究 OS

> English: [README.md](README.md)

> 一个**主研究方向为生物**（2026-07-14 战略转向）的自适应「干–湿–agent」科学闭环研究
> OS。**生物如今驱动整条 loop**：第一个生物闭环——cell-free 蛋白表达，**sequence →
> phenotype → claim → knowledge → redesign**——已 **双签（M24-B，simulation 级）**。
> 底层引擎刻意保持**域中立**：一个不懂具体科学域的 loop（**propose → dry compute →
> promotion gate → wet measurement → evidence compile → claim → knowledge → next
> proposal**），agent 在其中做自适应决策。这个生物盲内核是本项目的设计核心——化学两域
> 已先证明它：换一个域跑通、而 **kernel / planner / evidence-compiler / ledger /
> knowledge-compiler 字节不动**，正是这一点让生物如今能骑上同一条 loop。

**电梯陈述：**
- **一个闭环，不是一条管线**：内核只有两个持久科学对象（ExperimentObject /
  ObservationObject）+ 追加式事件日志 + 轮次状态机；design / dry / 晋升门 / wet /
  QC / 证据聚合 / claim ledger / knowledge compiler / agent 全是**可替换模块**。
- **域中立是硬门，不是口号**：kernel 只理解七个概念——candidate / observation /
  trust / evidence(claim) / knowledge / decision（+ ExperimentObject 载体）；一切
  化学 / 生物语义**只允许住在 domain / provider / adapter / QC 层**。这一条已由
  "同一 kernel 字节不动换域跑通"实证（见下方状态台账），是本项目最核心的卖点。
- **观测默认是"待裁决证据"，不是数据**：任何测量值进入系统时 `trust = PENDING`，
  须经 QC 裁决后才被路由；实验证据经统计聚合产出 claim（supported / rejected /
  qualified / insufficient），写回 ledger，重编译成 knowledge，再改变下一轮提案——
  **数据自推导地改写知识指纹链**，而非外部注入。

---

## 生物 Program（当前主研究方向）

生物是系统从"化学 demo"跨到面对**真正新科学物件**的地方：**molecule / reaction →
sequence / construct → expression / phenotype**。这正是 2026-07-14 裁决把生物定为主
方向的原因；化学两域已完成其使命（证明 runtime 可换域）。权威路线图见
[`docs/ROADMAP_BIOLOGY_PRIMARY.md`](docs/ROADMAP_BIOLOGY_PRIMARY.md) 与
[`docs/BIOLOGY_PROGRAM_2026.md`](docs/BIOLOGY_PROGRAM_2026.md)。

### 第一个生物闭环——`cell_free_expression_screen`（M24-B，双签，simulation 级）

一条真正的 dry–wet–agent 闭环，跑 cell-free 蛋白表达：
**sequence → phenotype → claim → knowledge → redesign**。

- **同一 claim head 三态分离**（由纯 phenotype = 荧光驱动）：`expression_high` →
  **supported**（e-product 102→1033，+0.234）；`expression_flipped` → **rejected**
  （e=42，−0.004）；`flat` → **insufficient**（e=0，p=1.0）。
- **知识指纹迁移** d04b4d05 → 7f8ce457，由 wet 荧光驱动——数据自推导出新知识，而非
  外部注入。
- **生物盲 dry 腿**——33 条 dry 观测来自序列 proxy / sim，**0 条 Z-matrix / geometry
  / PySCF**：内核始终不知道自己在做生物。
- **真实生物元件**——Anderson promoter 梯（J23100…）、RBS、GFP CDS，以及可审计的变异
  算子（这些是*设计知识 / 校准*，不是本 run 的观测）。
- **双签 = 两条认证路径均认证**：raw readout + percent-of-control（controls 路径经
  scale-aware `w_min` 修，percent scale 下 effective_w_min 83.33，e=102→1034，CI 宽
  ≪ w_min）。
- **一条 caveat 一字保留**：判准 ④（被改的知识 → 下一轮 construct）是**机制证明**
  （翻转知识方向会完全重排提案），但在单条低信号 loop 内**不会自发触发**——真实的
  explore/exploit 结构限制，如实标注，不是 bug。

### breadth-first：五器官 Biology-Primary OS（M25–M29，fuller v0.1）

并行建成后，本轮已从骨架深化为生物 OS 的 **fuller v0.1 vertical slice**——**135 测试
全绿（含化学回归）**，kernel 保持生物盲。各器官均 simulation 级；**仅 M26 整环接线**
（其 dry+wet 环走 `run_mcl_loop`），其余四个跑通域本地 e2e、整环接线为待补 seam
（权威见 `docs/BIOLOGY_PROGRAM_2026.md` §1.5、各 seam 文档 `docs/bio_seams/`）。

| 器官 | 内容 | 成熟度 |
|---|---|---|
| **M25 · Design** | 5 个可审计变异算子（2 个 translation-invariant）+ 生成池 + PROV lineage + diversity acquisition；判别案例（dry 排序被 wet phenotype 推翻，effect −0.322）走真实 claim + knowledge 账本 | fuller v0.1——域本地 e2e + 24 测试 |
| **M26 · Program** | typed 基因线路图，circuit 家族 2→5（dose-response / FFL / repressilator oscillator）+ 5-tier verify gate + 时序动态 faces + oscillation-frequency phase | fuller v0.1——**整环 mcl e2e landed** · 20 测试 + 9 landed mcl e2e |
| **M27 · Perturb** | 5-backend virtual-cell tournament + discriminative baseline-gate，以真实已发表 Perturb-seq benchmark 为 grounding（3 数据集无方法胜过 mean baseline，scGPT 过零）并作为测试强制 | fuller v0.1——域本地 e2e + 26 测试 |
| **M28 · Understand** | 四 discovery agents（Hypothesis / Analysis / Contradiction / Replication）驱动真实 claim 账本；agent 只产 evidence，kernel gate 认证（结构性护城河） | fuller v0.1——域本地 e2e + 8 测试 |
| **M29 · Execute** | typed protocol → device_ir → 假 liquid-handler / plate-reader，走 M23 sensed-state COMMITTED 门；五面事务 | fuller v0.1——域本地 e2e + 19 测试 |

**诚实边界**：全部生物为 **simulation 级**——可信仿真 wet 读 + 真*序列* dry proxy（GC
/ CAI / RBS / RNA-folding ΔG，诚实标注为 biased proxy），**无真湿实验、无真机**。M27 的
benchmark grounding 是真实*已发表*结果用作校准，非本 run 自身 wet 观测；M29 为
protocol-to-simulated-physical。M24-B 的"双签"仅指 *raw + controls 认证*；五器官为
fuller v0.1 slice——不是成品、未双签、除 M26 外尚未整环接线。公开序列数据/元件用作设计
知识 / 校准，绝不当本 run 观测。

---

## 诚实状态台账

逐方向完整状态（生物细节见上；台账保留含化学与真机支线的完整图景）：

| 方向 | 内容 | 状态 |
|---|---|---|
| **生物（当前主研究方向）· 执行面** | cell-free 蛋白表达 / 基因构件筛选：Domain Contract v3（`compute_targets → ComputeTarget`，`input_kind` 支持 `molecular_geometry` / `sequence_construct`）；真实序列 dry proxy + 三真值面（expression_high / expression_flipped / flat，design 而非 measured） | ✅ 执行面就位（M24-A） |
| **生物 · 自适应闭环** | 第一个真正的生物闭环（`cell_free_expression_screen`）：phenotype → evidence → claim → knowledge；三态分离 + 指纹迁移 + 生物盲 dry 腿（细节见上） | ✅ **双签（M24-B）：raw + controls 双路径均认证**（simulation 级） |
| **生物 · breadth-first 五器官** | M25–M29 Biology-Primary OS 的 fuller v0.1 vertical slice（Design / Program / Perturb / Understand / Execute），135 测试全绿（含化学回归）；M26 整环接线（mcl e2e landed），其余四个域本地 e2e | 🔨 fuller v0.1（simulation 级；仅 M26 整环接线，均未双签） |
| **化学（已验证的基础 / 跳板）** | solvent / catalyst screening 两域，走完整 dry–wet–agent 闭环；同一 kernel/loop 字节不动完成换域存在性证明——正是这一点证明了 runtime 能换到生物 | ✅ 已跑通、双签（M16–M22） |
| **真机（平行工程支线）** | 真实物理动作的可恢复 / 可回读 / 可提交 / 不可重放事务语义（Real-Wet Readiness Contract） | ✅ 就绪 against fake physical backend / ❌ real hardware pending；real wet-lab validation ❌ |

> **诚实边界（务必留意）**：生物闭环现已在 **两条认证路径上均决定性跑通**（M24-B，
> **双签**）：同一条 claim head 在纯 phenotype（荧光）驱动下分离出三态，wet 荧光把
> 知识指纹迁移，且 **raw readout 与 percent-of-control 两条路径均认证**——controls
> 路径经 scale-aware `w_min` 修落地（percent scale 下 effective_w_min 83.33，claim
> SUPPORTED，e=102→1034，CI 宽 ≪ w_min）。一条 caveat **一字保留**（双签后依然成立）：
> 判准 ④——被改的知识改变下一轮 construct——是**机制证明**（翻转知识方向会完全重排
> 提案），但在单条低信号 loop 内**不会自发触发**（真实的 explore/exploit 结构限制，
> 不是 bug）。以上全部为 **simulation 级**——生物域跑在 in-silico 序列 proxy 与仿真读板
> 上（无真湿实验）。化学闭环、换域证明、真机就绪契约（对假后端）仍是**已发生的真实
> 成就**。

**架构硬门（本项目设计核心）**：无论主方向是化学还是生物，
`kernel / planner / evidence-compiler / ledger / knowledge-compiler` 必须保持
**域中立（生物盲）**。若接生物必须改这些，即证明 domain abstraction 还不够干净——
那是"发现，如实报告"，不是偷偷改。换域的全部代价被约束在 domain / provider /
adapter / QC 层。

---

## 两条 loop 与核心思想

expos 有两条 loop 驱动器：

1. **Dry–Wet–Agent 闭环（`expos/mcl.py::run_mcl_loop`，当前科学主体）**：双腿管线——
   dry 计算腿（化学=PySCF；生物=序列特征 proxy）→ Dry→Wet 晋升门 → wet 测量腿
   （plate-reader 仿真器）→ QC/trust 裁决 → 证据聚合与 claim 裁定 → ledger 更新 →
   knowledge 重编译 → 下一轮提案。solvent_screen / catalyst_screen 走这条，生物域
   也走这条。
2. **单腿材料闭环（`expos/loop.py::run_loop`，最初的基础）**：一个同步执行器
   （crystal / coating 仿真器），带**结构化伪影注入**与三级 QC / 信任路由——这是
   项目最早证明"测量不可信 vs 参数不可行"分类路由的地方，仍然真实、仍在用。

```
┌──── Agent Orchestrator（建议权，无裁决权）────┐
│ 目标翻译 · 先验/理由 · QC 解释 · 动作提案      │
│ 产出 DecisionRecord；只读视图 + 提案队列       │
└───────────────┬───────────────────────────────┘
                ▼ propose / explain（永不直写观测、证据与知识）
┌──────────────────────── Kernel（域中立）────────────────────────┐
│ objects   两 schema + DecisionRecord（七概念：candidate/obs/     │
│           trust/evidence(claim)/knowledge/decision）             │
│ store     追加式事件日志 + 对象存储 + 运行检查点                 │
│ lifecycle 轮次状态机 + trust 裁决/路由                           │
│ claims    ClaimRecord / ClaimDelta / Ledger（证据账本）          │
│ knowledge compile_knowledge（知识 = 从账本编译的产物）          │
└──┬────────┬─────────┬──────────┬───────────┬──────────┬─────────┘
 design/  adapters/  planner/   qc/        (dry 腿)   (wet 腿)
 空间采样  dry+wet    晋升门+     三级检查    PySCF /    plate-reader
 布局预算  providers  证据裁定    归因+统计   序列 proxy 仿真器
轮次状态机：DESIGNED → EXECUTED → QC_DONE → ROUTED → CLOSED
观测生命周期：PENDING ─QC→ TRUSTED → 证据聚合 │ SUSPECT/FAILED → 失败模型(+next_action)
```

**两条站得住的差异化主张**（见 `docs/DEEP_REVIEW.md §1`）：
1. **方法学空白**：无任何公开基准在闭环优化对比中注入*结构化*系统偏差（空间场/漂移/
   批次）——现有工作止步于 iid 噪声。本项目的「模拟器 + 六注入器 + naive vs OS 对比」正落此空白。
2. **provenance 驱动的失败归因**：把「测量不可信」与「参数不可行」分类路由，直接回应
   A-Lab 式失败链「合成失败与表征失败混为一谈、缺独立复核」之缺陷（能力已实证；"一等
   内核服务"级封装为 V2 提案，见 `docs/ARCHITECTURE_V2_PROPOSAL.md`）。

---

## 快速上手

```bash
# 安装（Python ≥3.11；或不装直接从仓库根目录跑，conftest.py 已使 import expos 可用）
pip install -e .            # UI/LLM 附加：pip install -e ".[ui]" / ".[llm]"
```

### 单腿材料闭环（crystal / coating）

```bash
# 跑闭环——五臂全通（对照组 → 完整 OS），换臂只换 --mode
python scripts/run_loop.py --domain crystal --mode os --rounds 6 --seed 7 --out runs/demo
#   --mode ∈ {naive, robust, rcgp, os, os-soft, compare}；断点续跑追加 --resume
#     naive    全信基线（稻草人对照）
#     robust   信任盲 + 副本中位数聚合（路由层之外的稳健对照）
#     rcgp     模型层稳健（RobustResponseModel：Plateau-IMQ 后验软剪裁离群）
#     os       三级 QC + 信任路由（完整 OS）
#     os-soft  os + 隔离观测软降权（软信任对照，除聚合外与 os 全同）
#     compare  转发 expos.eval.compare，三臂编排出主图
```

### Dry–Wet–Agent 闭环（solvent / catalyst；生物域执行面同径）

MCL 双腿闭环目前作为 Python API 暴露（`expos.mcl.run_mcl_loop`）：

```python
from expos.mcl import run_mcl_loop

# 化学：solvent screening，两轮，默认模板 agent（LLM 为可选插件位）
summary = run_mcl_loop(
    "domains/solvent_screen.yaml", rounds=2, seed=7, out_dir="runs/solvent_demo",
)
# catalyst_screen.yaml 同径——同一 kernel/loop 字节不动换域（M20 换域存在性证明）。
```

一轮的数据流：`compile_knowledge → agent 提案 → dry 腿 → 晋升门 →
wet 腿 → QC/trust 裁决 → certification 证据裁定 → apply_claim_deltas → 下一轮`。
证据自动产生 claim，claim 改写 knowledge 指纹，knowledge 改变下一轮候选——这条
"数据自推导改写知识"的因果链是化学域已实证的核心，如今在第一个生物闭环里也成立
（M24-B 已双签——raw + controls 双路径均认证；三态分离 + 知识指纹迁移；判准 ④
机制证明，simulation 级）。

### CLI v2 与评测

```bash
# runs/ 只读查询器 + override 投递端（装包后 expos 入口 ≡ python3 -m expos.cli）
expos status   runs/demo                          # 一屏运行态
expos verdicts runs/demo --trust suspect          # 裁决清单表（可按信任级过滤）
expos inspect  runs/demo obs <obs_id>             # 对象与事件查询（what ∈ events/obs/exp）
expos override runs/demo --obs <id> --trust trusted --reason "…"  # 人工改判（留审计事件，不触碰 store）
expos domains  validate domains/solvent_screen.yaml  # 域配置前置校验
expos ui       --runs-root runs                   # 拉起只读 Streamlit 面板（需 .[ui]）

# 三幕 demo 一键；剧本见 docs/DEMO_SCRIPT.md
python scripts/make_demo.py --out runs/demo

# M9 三臂对比评测（幂等格子——重跑不重算已完成 campaign）
python -m expos.eval.compare --domain domains/crystal.yaml --scenario S0.demo \
    --seeds 1,2,3 --rounds 8 --out-root runs/m9 --arms naive,robust,os

# 门 12 决策链验收（三层验收 + 决策链差分）
python scripts/verify_run_chain.py runs/<name>

# 跑测试
pytest -q
```

运行产物目录（`runs/<name>/`，已 gitignore）：

```
runs/demo/
├── config.json          # 域配置快照 + 模式 + 种子（可复现）
├── events.jsonl         # 追加式事件日志：状态迁移/裁决/改判/claim 裁定/决策
├── checkpoint.json      # 运行检查点（当前轮次/预算/账本快照）→ 断点续跑
├── experiments/         # exp_r<k>.json
├── observations/        # obs_*.json
├── truth/               # 仿真真值 sidecar（OS 不可读，仅评分脚本用）
├── models/              # 响应模型训练集指纹
└── report/              # 对比图 + summary.json（M9 起）
```

**试点数字（S0.demo · crystal，单腿材料闭环）**：第 3 轮边缘蒸发把某平庸边缘孔读数抬成
全场最高——扫描中 **naive 假最优命中率 1.00 vs os 0.20**；**os** 三级 QC 判 SUSPECT
复测证伪。全量扫描（**1450 格＝标定集 A 450 + 评估集 B 1000**，五臂）主数字为极显著的
可信性指标：假最优拒斥（配对置换**精确 p≈3.1e-5**）、训练集污染率 os **0.004** vs
naive **0.146**（配对置换**精确 p≈1.9e-6**），两数字重算溯源见
`runs/full_sweep/report/headline_stats.json`。**regret 老实标注为不显著/场景依赖**
（os vs naive 主口径 p=0.0668，且在多数结构性场景劣于 robust 基线）——os 的优势在污染
防护与假最优拒斥，详见论文骨架 `docs/PAPER_OUTLINE.md` 的解耦讨论。

---

## 仓库结构

```
dry_wet_agent_os/
├── README.md  README_EN.md  CHECKPOINTS.md  CHANGELOG.md  pyproject.toml  conftest.py
├── docs/
│   ├── ARCHITECTURE.md            权威蓝图（公理/域/schema/各层规格）
│   ├── ROADMAP_BIOLOGY_PRIMARY.md 生物成为主研究方向的权威路线图（2026-07-14 裁决）
│   ├── M24_CELL_FREE_EXPRESSION.md / M24_CONTRACT_V3.md / M24_REPO_MAP.md  生物域章程与接缝地图
│   ├── BUILD_PLAN.md              里程碑定义与验收标准
│   ├── DEEP_REVIEW.md             有效性审视（两条主张 + 三大威胁）
│   └── REFERENCE_MAP.md / PAPER_OUTLINE.md / MCP_SURFACE.md …  调研库/论文骨架/审计面
├── expos/
│   ├── kernel/{objects,store,lifecycle,claims,knowledge,overrides}.py  # 域中立内核：两对象+事件日志+trust 路由+证据账本+知识编译
│   ├── design/{space,sampler,layout,budget}.py
│   ├── planner/{promotion,certification,arbiter,stages,policy}.py       # 晋升门 + 证据裁定 + 失败感知规划（纯函数红线）
│   ├── qc/{checks,attribution,failure_model,certification_stats,replicate_collapse,stats,policy}.py
│   ├── adapters/dry/{adapter,compute,catalysts,solvents,constructs,sequence_adapter,sequences,ingest,worker}.py  # dry 腿：PySCF（化学）+ 序列特征 proxy（生物）
│   ├── adapters/wet/{screen,sim_reader,bio_readout,action_ledger,recovery,differential_gate,orchestration,…}.py  # wet 腿：plate-reader 仿真器 + 真机就绪事务面
│   ├── adapters/providers/{solvent_screen,catalyst_screen,cell_free_expression_screen}.py  # 域 provider（含生物）
│   ├── adapters/{base,sim_base,sim_crystal,sim_coating,domain_provider,bench_manual,artifacts,content_store}.py
│   ├── models/{response_gp,robust_gp}.py   # 响应 GP（trusted）+ RCGP 稳健 GP
│   ├── cli.py                              # CLI v2
│   ├── mcl.py                              # Dry–Wet–Agent 双腿闭环（run_mcl_loop）
│   └── domain.py  loop.py                  # 域装配 + 单腿材料闭环
├── expos_mcp/                              # FastMCP 只读审计面（expos-audit skill）
├── domains/{crystal,coating,solvent_screen,solvent_screen_flipped,catalyst_screen}.yaml  # 换域 = 换配置
├── scripts/{run_loop,make_demo,verify_run_chain,gen_sweep,expos_report,…}.py
├── tests/                                 # kernel/design/adapters/qc/planner/mcl/e2e …
└── runs/                                  # 运行产物（gitignore）
```

（`CHECKPOINTS.md` = 构建台账，逐里程碑记状态/验证/偏离；`CHANGELOG.md` 记版本变更。）

---

## 里程碑状态

| # | 里程碑 | 状态 |
|---|---|---|
| M0–M10 | 单腿材料 OS：kernel / design / adapters / QC 三级 / 归因+失败模型 / 失败感知规划 / agent 层 / naive-robust-os 三臂评测 / CLI v2 + UI | ✅ done |
| M16 | Executable Minimum Dry–Wet–Agent Control Loop（solvent_screen，simulated-wet） | ✅ done（双签） |
| M17+M18 | Evidence-to-Claim Compiler + 知识反馈闭环 → 定名 **Adaptive Dry–Wet–Agent Scientific Loop**；LLM 三阶段过（默认仍模板） | ✅ done（双签） |
| M20 | 换域存在性证明（catalyst_screen，同 kernel 字节不动跑通全环，门 12 COMPLETE） | ✅ done（双签） |
| M21–M22 | 域契约 v2 + provider 五 hook + 溯源补全 + 性质测试文化 | ✅ done（双签） |
| M23 | Real-Wet Readiness Contract（真机就绪事务面，对假物理后端；真硬件 pending） | ✅ done（双签） |
| **M24-A** | **生物执行面：Domain Contract v3（compute_targets/ComputeTarget）+ 序列 dry proxy + 三真值面** | **✅ 就位** |
| **M24-B** | **自适应生物闭环（cell-free 表达：phenotype → evidence → claim → knowledge）**——三态分离 + 指纹迁移；raw + controls 双路径均认证（controls 经 scale-aware w_min 修，effective_w_min 83.33，e=102→1034）；判准 ④ 机制证明（闭环内未自发触发）；simulation 级 | **✅ 双签（raw + controls 双路径均认证）** |
| M25–M29 | **生物 Program breadth-first，深化到 fuller v0.1**：五器官从骨架深化为 Biology-Primary OS 的 **fuller v0.1 vertical slice**——**135 测试全绿（含化学回归）**，kernel 生物盲。M25 Design（5 个可审计变异算子 + PROV lineage + diversity acquisition；判别案例 dry 被 wet 推翻 effect −0.322；24 测试）；M26 Program（circuit 家族 2→5 含 repressilator oscillator + oscillation-frequency phase；**integration owner 已 landed M26 seams 1–5 → 整环 dry+wet 走 `run_mcl_loop`**；20 测试 + 9 landed mcl e2e）；M27 Perturb（5-backend tournament + baseline-gate 以真实已发表 Perturb-seq benchmark 为 grounding 并作测试强制；26 测试）；M28 Understand（四 discovery agents 驱动真实 claim 账本，kernel gate 认证；8 测试）；M29 Execute（typed protocol → device_ir → 假 liquid-handler/plate-reader 走 M23 COMMITTED 门；19 测试）。全部 simulation/retrospective/fake-backend 级、诚实标注；**仅 M26 整环接线**，其余四个域本地 e2e、整环接线为待补 seam（权威见 `docs/BIOLOGY_PROGRAM_2026.md` §1.5、`docs/bio_seams/`） | 🔨 fuller v0.1（simulation 级；M26 整环，均未双签） |

> 权威里程碑台账见 `CHECKPOINTS.md`（含验证命令与偏离记录）；生物主线路线图见
> `docs/ROADMAP_BIOLOGY_PRIMARY.md`。

---

## 设计红线

- **域中立硬门**：`kernel / planner / evidence-compiler / ledger / knowledge-compiler`
  语义域中立（生物盲）；一切化学/生物语义住在 domain/provider/adapter/QC 层。若接新域
  必须改这些内核文件，作为 abstraction 发现如实上报，不得偷改。
- **真值隔离**：仿真真值只由 `adapters/sim_*` / reader 服务端写入 `truth/` sidecar；
  `qc/models/planner/agent` 一律禁读，`loop.py` 只做不透明透传。这是「系统没被伪影
  骗到」可定量证明的前提。
- **Agent 无裁决权**：Agent 只拿只读视图 + 提案队列，一切产出为 DecisionRecord，须经
  planner/kernel 校验才生效；无观测/证据/知识写句柄（守门测试机器验证）。
- **证据不可变 + 写严读容**：证据是追加式哈希链，schema 演进走验证语义而非数据迁移
  （ADDITIVE_SINCE 注册表）；任何进度都必须可恢复、可审计。
- **无静默降级**：约束缺失变量、预算超支、越界参数、缺单位等一律**响亮失败**（干净领域
  异常），绝不静默判真或降级。

---

## 安全与执行方式声明

当前执行器以**带结构化伪影注入的仿真器为主**（可控真值 → 可定量对比 naive vs OS）；
`BenchAdapter` 提供协议同构的真实台面路径（人类可读 worklist + CSV/图像回灌），真机
就绪事务面（M23）针对**假物理后端**验证——**真实硬件与真实 wet-lab 验证仍 pending**。
化学域使用常见食品级/教学级安全盐类（明矾/硝酸钾）水溶液蒸发结晶等；生物域当前限于
**in-silico 序列 proxy 与仿真读板**，无真实湿实验、无活体材料操作。

---

## 文档与贡献

- **文档站**（MkDocs Material，`mkdocs.yml`）：`python3 -m mkdocs serve` 本地预览；
  权威蓝图 `docs/ARCHITECTURE.md`、生物主线路线图 `docs/ROADMAP_BIOLOGY_PRIMARY.md`、
  论文骨架 `docs/PAPER_OUTLINE.md`、三幕剧本 `docs/DEMO_SCRIPT.md`、审计面
  `docs/MCP_SURFACE.md`。
- **只读审计**：`expos_mcp/` 提供 FastMCP 审计面（七只读工具，含门 12 验收 verify_gate12
  / diff_runs）；配套 `expos-audit` skill。
- **贡献指南**：见 [CONTRIBUTING.md](CONTRIBUTING.md)——含策略注入点开发约定、红线清单
  与对抗测试要求；工程规范见 `docs/ENGINEERING.md`。
