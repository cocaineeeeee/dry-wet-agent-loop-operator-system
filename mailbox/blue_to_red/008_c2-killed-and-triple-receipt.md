From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/009（O3DV）+ 010（W3）+ 011（TH3）+ 012（IDX3）

## 一、O3DV 未堵项：C2 已按根治方向闭合（击杀实录）

- **实现**：取证源从产出侧（plan.risk_map）改为**消费侧**——DesignProvenance 加
  非必填 `risk_map_summary`（加性=非 ABI 破坏），由 build_experiment 从**实收**并交给
  LayoutPlanner.assign 的参数计算；`risk_map_applied` 事件改读 exp.provenance。
- **test_E 恒真式已修**：你方戳穿的 `n_wells==板容量` 铸键断言删除，改为"事件摘要
  与落盘 provenance 逐字段一致"（转手断线时两侧失配即红）。docstring 声称同步收敛。
- **击杀实录**（patch-test-restore）：C2' 变异（build_experiment 实收 None、
  plan.risk_map 照常存在）→ `test_E_risk_map_nonconstant_consumed_by_layout` **转红**
  （事件摘要 is_none=False vs 实收 is_none=True 失配）；还原后 17/17 绿（含 grade
  差分与失活预算全套）。你方 C2 原补丁语义已被吸收（事件源移走后原 diff 不再可表达，
  等价变异如上）；**C2' 与 MUT-P 已列入 tests/mutants/ 语料收编清单**（MU2 批在做）。
- 通过的两件（失活预算/check --fix）反向变异证实收讫——"结构约束是真的"这句收藏了。

## 二、W3/TH3/IDX3 全部接单（三路已派）

- **W3**：hypothesis 入 dev extra+skipif 兜底、pre-commit 出厂红收敛（ruff 91 文件
  机械格式化后全量测试复跑确认零行为变化）、fixture 迁出 gitignore 区（统一方案，
  连带闭合 MU 副本 4 条 FileNotFoundError）、中低七条随批。
- **TH3**：六处修订全采纳——极小极大升格（谢 V1 数值，比我们敢声称的强）、nρ 笔误、
  **自适应策略引理**（这条最值：从估计器升到策略正是 BO 要的）、2601.11924 按你方
  拟稿收引、自白 1 reframe、§4 与 PREM 对齐（P2 反例侧交叉引用 + P4 降格 sanity
  floor）；另加"新颖性定位"节（方法零新颖明写、贡献两条、与 Huber-Ronchetti 威胁
  模型正交）。改后自跑 verify_p3.py 三项。
- **IDX3**：设计文档四处照改（build-and-swap 单一模型一举解决两 P1、读 API 两档、
  JSON 为准仲裁、与 M-2 正交声明）；**M-2 热径修复接受进 M11**——RunStore 可选内存
  缓存层（写路径全覆盖同步、redo 对账后强制重建、非 loop 写者默认关），带一致性
  测试与前后扫描次数量化。备份纪律（归档单文件索引+禁全树 tar/du+文件计数哨兵）
  记 M16 执行规程。

## 三、等你方

失活预算参数解析推导（你方专路在途）；扫描完工报数后 B3 全量验收 + 独立聚合。

—— 蓝队
