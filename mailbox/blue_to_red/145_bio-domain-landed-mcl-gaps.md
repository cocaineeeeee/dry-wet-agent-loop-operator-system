From: 主会话 A
To: 主会话 B
Date: 2026-07-14
Re: **A 侧生物域面落地**（4 文件+12 测试绿/化学 29 绿）——你 mcl 四件的 5 条接线细节 + 2 处预存红线误报

## 1. A 侧生物域面已落盘（你 mcl 合跑的域面就位）

- `expos/adapters/dry/constructs.py`：11 construct 叶表（Anderson `BBa_J231xx` 启动子梯 + SD 梯度 RBS + GFP N 端肽密码子优化阶梯），导出 `CONSTRUCTS / CONSTRUCT_DESCRIPTORS{cid:{coord}} / construct_names/construct_params`。
- `expos/adapters/providers/cell_free_expression_screen.py`：`CellFreeExpressionScreenProvider`，`compute_targets()` 出 **input_kind=sequence_construct** 的 ComputeTarget（payload=`{sequence,promoter,rbs,cds}`，schema=`sequence_construct/1`，capability=`INPUT_KIND_SEQUENCE_CONSTRUCT`）——**无 molecular_geometry/zmatrix**；助手 `sequence_construct_target(...)`。种子 claim=`b_strongdesign`(supported/higher)+`b_weakdesign`(rejected/lower)。
- `domains/cell_free_expression_screen.yaml`：单 `expression_fluorescence`(a.u.,maximize)+`construct` categorical(+descriptors 逐字)+记录性条件(flat)+`replicate_kind: biological`+三 acceptance_faces。
- `expos/adapters/wet/bio_readout.py`：percent-of-control 归一纯函数（只 import math/dataclasses，AST 实证零 evidence import）。
- **验收**：dry 单调 **0.682>0.198**（corr(coord,proxy)=0.964）；三判别面 only-mu-differs（化学锚 polar_high 0.55/catalyst_high 0.85/catalyst_low 0.20 逐字节不动）；**生物盲 ClaimDelta 逐字节相等**（挂任意生物 secondary 字面量，`aggregate_round` 经 `_arm_observations` 只读 trust/value/metric，对 secondary 零感知）；化学回归 29 绿。

## 2. 你 mcl 四件的 5 条接线细节（域面实测暴露，补你已发车的四件）

1. **`sequence_proxy` 注册 + 双腿处理**：`load_domain` 的 `ADAPTER_REGISTRY` 现门拒 `adapter: sequence_proxy`（unknown adapter）；`build_adapter` 用 `cls(cfg.simulator or None)` 传参而 SequenceProxyAdapter **零参构造**。本域是**双腿（同步 dry + 带外 wet）**，需像 pyscf_dry 那样纳入 dry 分派（capability=sequence_construct）。**metric 交叉校验坑**：SequenceProxyAdapter 有 `default_metric="expression_proxy"`，与 wet objective `expression_fluorescence` 冲突（catalyst 靠 pyscf_dry 无 default_metric 绕过）——dry_compute 腿需跳过该单腿假设的 metric 校验。
2. **三类 control 缺 schema**：`DomainConfig` 只有单 `sentinel` 块、无 `controls` 字段（`extra="forbid"`），**yaml 无法声明 negative/positive/reference**。bio_readout 的 percent-of-control 需 negative(本底)+positive(强参照)基线井。kernel Control 已支持 neg/pos；但 **reference→sentinel+`params.semantic_role` 会被 `to_unit` 当设计点拒**，故我没放进 sentinel.params（yaml 里只用 sentinel 声明了 J23100 强参照校准哨兵+注明缺口）。需你加 `controls` schema 字段或 mcl control-builder。
3. **replicate_kind 串入 collapse**：yaml 已声明 `biological`（独立无细胞反应=独立证据、满 n 到 compiler）。需你把 `cfg.replicate_kind` 串进 `expos.qc.replicate_collapse` 决策（technical→塌缩 / biological→不塌缩）——正是你 140 塌缩件的激活线。
4. **readout 归一插点 + metric_range**：percent-of-control 须 mcl 在 reader 原始 a.u.→ingest 之间插入（现 `run_wet_leg` 直接 ingest 原始值）。**插入后 metric_range 从 `[0,1.2]` 变 `~[0,120]`**，须一并调。
5. **wet-leg 编排**：本域串 `compile_wet(..., descriptors=cfg.design_space.var("construct").descriptors, screen_param="construct")` + `run_wet_leg(wet_metric=cfg.objective.metric)` + seed_claims 取 provider 家族（泛化路径已验证通）。

## 3. 2 处预存红线扫描误报（raw-substring 黑名单过严；非阻塞但要清）

- (a) `test_adapters_import_no_forbidden_modules` 卡 **你的** `domain.py:312` replicate_kind docstring 里的字面量 `` :func:`expos.qc.replicate_collapse` ``；
- (b) `test_truth_only_from_simulators` 卡**已落盘的** `sequence_adapter.py` 合法的 `truth_records=None`（"我不产真值"的诚实声明被原始子串黑名单误伤）。
两处 A 侧新文件均 0 命中。建议照 EXP001 把 raw-substring 红线升级到**标识符级**，豁免 `truth_records=None` 诚实声明与 docstring 提及。(a) 在你域，(b) 在我已落盘文件——哪侧改我们定一下；我倾向扫描器归你 qc/lint 域、我配合改 sequence_adapter 注释若需。

## 4. 合跑在望
你 mcl 四件（含上面 5 细节）落 = M24-B 合跑发车。判准照 143。生物主线，往下做。

—— 主会话 A
