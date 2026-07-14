# R3 输入包 · 审查方（红队）下一轮入口

> 2026-07-11 晚。**本文是修复方（蓝队）为审查方下一轮压测组装的完整入口文档**：R1+R2 两轮
> 加固战役的变更面全景 + 主动邀请压测的新表面 + 数据面指针 + 诚实的未闭环清单 + 反问汇总 +
> 复核命令。目的是让红队**选靶**——不用再逐仓库摸底。基调不变：加固不是否定，"已核验"与
> finding 同权。措辞守则同 `docs/STRESS_TEST_VOCAB.md`（中性：健壮性 / 边界 / 误用场景）。
>
> **配套读物**（本文不复述其内容，只给指针）：
> - `docs/STRESS_TEST_R3.md` — 红队 R3 前哨（12 路整合；§1 三急件、§9 终审剩余项）。
> - `docs/STRESS_TEST_R2_RESPONSE.md` — 修复方对 R2 的逐条回应（**定稿 agent 在途**）；其 **§8「给红队的四个反问」** 见本文 §5，只引路径不复述。
> - `docs/ARCHITECTURE_V2_PROPOSAL.md` — 架构提案（机制活性/协议即代码）；**§7 五问**是红队已认领的审查点。
> - 权威台账 `CHECKPOINTS.md` · 蓝图 `docs/ARCHITECTURE.md` · 协议 `docs/M9_PROTOCOL.md`。
>
> 全量套件 **549 测试**（`pytest --collect-only`，2026-07-11）。所有 file:line 均按当前快照核实；
> 修复方持续改码，凡快照相关裁定按最新码复核不算翻案。

---

## 1. 本轮变更面全景（R3 的攻击地图，按包 · 每项一行带测试名）

R1+R2 修复后落地的代码面。红队可据此选靶——每行一个"效果证据入口"（测试名），复验请验效果不只看绿。

### kernel（`expos/kernel/`）
- **转移表**：`lifecycle.py:45 VALID_TRANSITIONS` + `advance_status:54`（越界 raise）；信任侧 `TRUST_TRANSITION_TABLE:161`/`check_trust_transition:182` — 测 `tests/test_stateful_kernel.py`（Hypothesis `KernelStateMachine:111`，advance/reclassify 规则驱动两表）。
- **幽灵裁定**（Q-8）：`lifecycle.py:302 validate_proposal` guard③`:322`（裁定从未入日志的提案即拒） — 测 `tests/test_kernel.py::test_ghost_adjudication_rejected:426`。
- **flock 写锁**：`store.py:98 _acquire_writer_lock`（`fcntl.flock LOCK_EX|LOCK_NB`），opt-in `RunStore(lock=True)` — 测 `tests/test_kernel_robustness.py::test_writer_lock_blocks_concurrent_writer:164`。
- **torn-tail 谓词**：`store.py:128 _parse_line`（共享行有效性谓词）+ `_heal_torn_tail:165`（下次 append 前截尾） — 测 `test_kernel_robustness.py::test_torn_tail_healed_before_next_append:55`、`test_crash_consistency.py::test_true_torn_tail_truncated_with_warning:54`。
- **redo 对账**：`store.py:358 reconcile_redo_rounds`（删 round≥from 的孤儿 obs/exp + 记 `redo_reconciliation` 事件），resume 时 `loop.py:409` 调用 — 测 `test_crash_consistency.py::test_redo_reconciliation_event_logged_once:125`、`::test_redo_round_n_train_matches_oneshot:118`。

### planner（`expos/planner/`）
- **机制修复三件**（全 `policy.py`）：(a) `_plate_risk_map:159` 用真棋盘格批标 `R{r}-B{(row+col)%2}` 替 `solution_batch=None`（原风险图恒常、避险惰性）；(b) `_global_artifact_rate:185` 读契约键 `p_global`（缺键 raise，原读错键 `global_rate` → 折扣恒 1 = 退化为无折扣 UCB）；(c) `_RISK_TIER=0.25:152` 粗分层，保 `LayoutPlanner` 块平衡不被连续风险值饿死 — 测 `tests/test_mechanism_activity.py::test_E_risk_map_nonconstant_consumed_by_layout:73`、`::test_D_failure_aware_reached_with_risk_discount_generator:126`。
- **毒丸校验**：`arbiter.py:106 validate_proposal_content`（消费于 `policy.py:290`），退化提案（如 `params` 非 dict）入受理集前即拒 — 测 `tests/test_planner_arbiter.py::test_adjudicate_gate_rejects_poison_pill:550`、`::test_collect_agent_poison_params_skips_not_raises:169`。
- **min_dwell**：`stages.py:67 StageRule.min_dwell`，FSM enforce `:183`，gp/failure_aware 默认 2 — 测 `tests/test_planner_stages.py::test_min_dwell_breaks_gp_failure_aware_oscillation:122`、`::test_validate_negative_min_dwell:70`。
- **封顶**：两层 — `arbiter.py:339 arbitrate` 预算封顶（择至 well 数 ≤ 预算，返回 chosen+overflow）+ `policy.py:438` 候选容量二次封顶（`slots=n_cands`，超额按优先序 overflow 留痕） — 测 `tests/test_planner_arbiter.py`（arbitration/overflow）。

### qc（`expos/qc/`）
- **批次估计器**：`checks.py:449` 身份无关过原点 WLS `shift_hat`；fire/score 改用**带符号** `shift_hat`（`:455`，`abs(shift_hat)≥BATCH_MAG_FIRE ∧ abs(z)≥BATCH_Z_FIRE ∧ ¬edge_fired`） — 测 `tests/test_qc_checks.py::test_batch_artifact_hits_one_batch:69`。⚠️ **选批** `top_b=max(batch_shifts,key=abs)` 仍在 `checks.py:461`——R3 §1.1 的"选哪批异常"方向问题**未随此改闭环**，见 §4 与 §3 重跑证据，请红队定向复核。
- **cusum**：`stats.py:262 cusum`（双侧列表 CUSUM），跨轮哨兵主判 `checks.py:519`（`DRIFT_CUSUM_K/H=0.5/6.0`，冻结 target=0/sd=1） — 测 `tests/test_qc_stats.py::test_cusum_alarms_after_shift:191`、`test_qc_checks.py::test_temporal_drift_cross_round_detects_aging_instrument:275`。
- **边界测试**：`tests/test_qc_policy.py::test_soft_trust_upper_boundary_discontinuous:436`（软信任上界不连续点）；fire/full-score 阈值解耦常数 `checks.py:78`。
- **W_AUX（幽灵通道剔除）**：历史"四通道 anchor 0.2 + aux 0.1"里 aux≡anchor 恒等，合并为三通道 `W_QC/W_MAIN/W_ANCHOR=0.4/0.3/0.3`（`attribution.py:78`）— 测 `tests/test_attribution.py::test_signature_weights_no_phantom_aux:311`（断言 `W_AUX` 不存在）。

### adapters（`expos/adapters/artifacts.py`）
- **resident 漂移**：`mode="resident"` 真实仪器四分量跨轮持久漂移 — `resident_baseline:182`（aging `:190` + 会话间随机游走 `:192` + 温周期 `:196`）+ 会话内 AR(1) `:213`；`applied_eps=0.005:177` 替 `1e-9`（消全亮 applied 标签污染）；确定性于 (seed,round) 保 resume 等价 — 测 `tests/test_adapters.py::test_instrument_drift_resident_components_and_baseline_determinism`、`::test_instrument_drift_resident_resume_equivalence_at_adapter`、`test_qc_checks.py::test_temporal_drift_resident_aging_detected_late_rounds:315`、`::test_temporal_drift_resident_pure_within_ar1_is_honest_blind_spot:337`。

### eval（`expos/eval/`、`runs/*/_tools/aggregate*.py`）
- **种子集 / artifact 种子孤儿标注**：报数只来自评估集 B；`run_cell._seed_triplet` 显式标 `artifact_orphan`（防再被引为独立伪影种子流）+ `exec_round0` 标执行流真源 — 测 `tests/test_eval.py::test_training_members_sidecar_reproducible:167`。
- **双口径**：检出 `{arm}_detection_rate`（注入器专属检查、含 round0）vs 旧 `_detection_rate_any`；归因 cause 级 vs 逐孔（见 `report.md` 口径段）。
- **置换检验**：`stats_tests.py`（`compare_arms_paired`）— 测 `tests/test_eval.py::test_permutation_symmetric_noise_not_significant:209`、`::test_permutation_clear_difference_significant:199`、`::test_permutation_all_zero_diffs_p_is_one:193`。
- **cause 级配对**：`{arm}_cause_hit_rate` + 种子级 bootstrap 95%CI — 测 `tests/test_compare.py::test_aggregate_cause_level_metrics:220`。
- **eff-noise 轴**：`detection_curve.csv` 并列 `eff_over_noise_mean/median`（|实现效应|/noise_sd，修 L-1 乘性注入×绝对阈值的量纲混淆），产物 `detection_curve_effnoise.png` — 测 `tests/test_compare.py::test_aggregate_realized_effect_over_noise`。
- **training_members**：`training_contamination` 新口径 + sidecar — 测 `test_eval.py::test_os_arm_training_contamination_matches_legacy:158`、`::test_soft_arm_training_contamination_exceeds_legacy:149`、`::test_os_contamination_le_naive:112`。

### loop（`expos/loop.py`）
- **五→活性事件**：五个策略注入点现发只读活性观测事件 `round_designed:462` / `risk_map_applied:471`（`_risk_map_summary:328`）/ `aggregation_alpha:505` / `model_updated:519`（ARCH_V2 §2 观测面，发射者只记事实不判档）— 测 `tests/test_mechanism_activity.py`。
- **override 消费**：`loop.py:449 consume_pending_overrides`（`kernel/overrides.py:165`）— 测 `tests/test_overrides.py::test_idempotent_reconsume_no_double_reclassify:233`、`::test_valid_override_applied_with_audit_trail:126`。
- **配置指纹**：`domain.py:142 config_fingerprint`，fresh 存 `loop.py:417`、resume 全配置漂移守卫 `:389-402`（raise 除非 `--allow-config-drift`，emit `config_drift`）— ⚠️ **drift 守卫路径无专测**（仅 `test_ui_smoke.py` 列 kind），列入 §2 新表面。
- **消融四臂**：`runs/ablation/cells.tsv`（1240 格）四臂 `os-lite` / `os-minus-riskmap` / `os-minus-arbiter` / `os-minus-attribution`（+os-soft 参照）— 接线测 `tests/test_mechanism_activity.py::test_ablation_riskmap_is_none:234`、`::test_ablation_arbiter_consumes_nothing:259`、`::test_ablation_attribution_detects_but_no_attribution:284`。
- **UI 空态链**：`ui/_common.py:327` 容空（空快照→空 dict→空态指引，不 KeyError）+ pg_board/pg_overview/pg_decisions 多处"暂无…属预期"caption — 测 `tests/test_ui_smoke.py::test_apptest_missing_dir_friendly:187`。
- **CLI `check --fix`**：⚠️ **未落地**（O3-D 交接的实现级纪律尚未成 CLI 子命令；`cli.py` 子命令仅 run/status/verdicts/inspect/override/domains/ui）。torn-tail 自愈只在 store 层（`_heal_torn_tail`），未上升为 `expos check --fix`。作为**提案表面**见 §2。

---

## 2. 待压测的新表面（我方主动邀请找茬）

以下多为**已落规格 / 提案规格**，请红队优先压。

1. **grade 三态判档规则**（提案，未落码）：以 `grade:{active,warning,absent}` 替 `fired:bool`（`STRESS_TEST_R3.md:335`、`ARCHITECTURE_V2_PROPOSAL.md:72`）。差分测试构造法见 O3-D 交接 `/Data1/ericyang/r3_os_references/M11_HANDOFF_O3D.md §四`（cosmic-ray baseline+差分、pinned mutant）。请裁：三态阈值/去抖如何既不误判合法惰性、又抓住表演性生效。
2. **失活预算熔断参数**（提案，未落码）：intensity=3 / period=5 熔断（连续 N 轮黄牌升红，`STRESS_TEST_R3.md:337`）——**该参数选择依据薄弱**（借自 VS Code CrashTracker "3 次/5 分钟"，非本系统推导）。**请压这个数怎么定才对**（sweep 长度、轮数、误报率的函数）。
3. **`expos check --fix` 防误截三重纪律**（提案）：redis-check-aof 式三段（水位截尾 / 干净截断自愈 vs 中段损坏必须响亮失败 / 默认响亮+显式 `--fix`），加 O3-D 两硬纪律：`--fix` 只愈尾须做成**结构约束**（水位后必须直达 EOF 才许截）、自愈**遇第一个坏行即停**（SQLite `walIndexRecover` 只认连续有效前缀）。落地前请红队先审这套纪律的完备性。
4. **CampaignManifest / ClaimDecision 血缘 schema**：CampaignManifest 已作 JSON 落盘（`runs/r1_resweep/campaign_manifest.json`，v1，含 grid/code_fingerprint/execution_channels/run_hierarchy）；ClaimDecision/`claims.json` 仍是 TR 路提案。请找 schema 缺字段 / 血缘断点。
5. **provenance 图投影完备性**：run_hierarchy 用 OpenLineage `ParentRunFacet` 投影（campaign→cell 父子，子 run `UUIDv5`；`campaign_manifest.json` + `RUN_MANIFEST_SPEC.md`）。**请找缺边**——哪些真实依赖（scenario yaml、scaffold 生成器、聚合器）未进投影。
6. **resident 漂移四分量参数化 + CUSUM 功效**：四分量 `rate_per_round/sigma_between/period_rounds+period_amp/phi+sigma`（`artifacts.py:171-177`）。承 R2_RESPONSE §8 反问 1：(a) 会话间游走能否被跨轮 CUSUM 累出，还是被温周期/AR(1) 淹掉（真非正交？）；(b) `applied_eps=0.005` 是否在低幅档又造"applied=True 但检不出"的伪盲区。**resident 240 格待发射**（见 §3），请预注册功效预测。

---

## 3. 数据面

- **resweep 2700 格终判**（机制修复后 · 评估集 B · 零失败）：`runs/r1_resweep/report/report.md`、`aggregate_summary.json`。
  - **字面判定 `H1_REJECTED_os_worse`**：S2r3 中高档池化 n=100，mean_diff(os−robust)=**+0.0161**、置换 p=**0.0001**、95%CI[+0.0108,+0.0216]、os 更优占比 0.22（mean_diff>0=os regret 更**高**=更差）。
  - **根因 = 硬隔离数据饥饿**：os 在签名匹配的中高档正确隔离受影响孔（如 S2r3.edge_evaporation.0.35 os `train_inj=0.396` vs naive 0.833、`n_suspect=0.711`），代理模型训练数据被抽走 → regret 付费。**机制通电≠有用**：修复前后 os 配对 all_shared n=200 mean_diff(new−old)=**+0.0001**、p=**0.9191**、frac_improved 0.14（机制通电对 regret 无净改善——H1' 的敘事约束）。
  - **附带**：批次方向证据在此可见——S2r3.batch_shift.-0.18 os `train_inj=0.977`（naive 0.489）、`train_contam=0.559`（naive 0.364），os 反把污染批**拉进**训练（选批指向干净批，佐证 §1 qc 的 `checks.py:461` 未闭）；S4 无 robust 格 → H1 的 S4 半边不可裁（`note_S4`）。
- **消融 1240 格**【**待发射**：`runs/ablation/cells.tsv` 已就位、`ablation.sbatch` 已备、0 run 完成、无 report】。四臂 os-lite(380)/os-minus-riskmap(160)/os-minus-arbiter(160)/os-minus-attribution(160)/os-soft(380)。**预注册判读邀请**：请红队**先预注册**消融臂预期排序再看数据——若 `os-minus-riskmap ≈ os`（风险图对 regret 无用），是"活性≠有用"的又一独立证据；反之则风险图确有 regret 贡献。
- **resident 240 格**【**待发射**：`runs/resident_sweep/cells.tsv`、`resident.sbatch` 已备、0 run 完成】。naive/os/os-soft 各 80（4 幅度×20 seed×3 臂）。配 §2.6 的 CUSUM 功效预测。

---

## 4. 未闭环清单（诚实）

1. **P-6 FWER 无声明**：~12 检查并行零族错误率校正（R2 P2★/P-6），根修应落 `qc/checks.py`（逐孔 QC 税 1.09% 达标不受影响，但协议须声明族错误率口径）。
2. **加权污染口径需 models 导权重**：`report.md` K-P3——"有效污染权重占比"（Σw·1[contam]/Σw）需 `robust_gp` 侧导出 per-obs influence 权重（fit 内局部量，未落盘），属需 models 侧配合的 spec（`M9_PROTOCOL.md §3.3(R2)`）。
3. **S4 robust 对照缺**：resweep S4 无 robust 格 → H1 的 S4-vs-robust 半边不可裁，只报 os-vs-naive。
4. **H4 真跨族**：现 S3.wide_edge 是宽边界（与窄边界签名同族之争，R2_RESPONSE §8 反问 2）；真跨族伪影（如纯 drift×dust 组合）未跑。
5. **cand_id 内容指纹**：`kernel/objects.py:149` `cand_id` 为随机 `new_id("cand")`，非内容指纹——同参候选跨 run 不可去重/对齐。
6. **supersedes 无行为**：`planner/arbiter.py:151` 明标"记账字段、顶替语义未实现"（J-7，Backlog M13）；`attribution.py:579` detour 只填 supersedes 不执行顶替 — 锁定测 `test_planner_arbiter.py::test_supersedes_is_bookkeeping_only`。
7. **EXP011 / mechanisms.py 注册表本体仍是提案**：机制活性注册表（协议即代码）只在 `ARCHITECTURE_V2_PROPOSAL.md`/`STRESS_TEST_R3.md` 提案层，无 `expos/mechanisms.py` 本体。
8. **（新增，见 §1 qc）批次选批方向**：`checks.py:461 top_b=max(...,key=abs)` 在棋盘格对称平局下恒选插入序首批（干净批），R3 §1.1 P0 未随 shift_hat 改闭；resweep 数据（§3 附带）仍见 os 隔离干净批。请红队定向复核是否需根修选批（读 signed `shift_hat` 符号 / 哨兵参与选批）。

---

## 5. 既有反问汇总（引路径，不复述）

- **ARCH_V2 §7 五问**（`docs/ARCHITECTURE_V2_PROPOSAL.md:301`）：假活性攻击 / 协议哈希边界 / 无 truth 的 grade 校准 / 保费上限 X 可证伪性 / 分层信任不可弥合差。红队已认领 1/3/4/5 为 R3 审查点；反问 2 已裁定（fn 文件 sha 入 `protocol_sha256` 闭包）。
- **R1_RESPONSE §5 五反问**（`docs/STRESS_TEST_R1_RESPONSE.md:102`）：机制活性不变量下沉内核 vs 测试层 / S3 宽边界算不算未见类型 / batch 全局效应 truth 标签口径 / 污染率二值 vs 加权口径 / 主张一是否降格为"污染鲁棒的 BO"。
- **R2_RESPONSE §8 四反问**（`docs/STRESS_TEST_R2_RESPONSE.md:165`，**定稿在途**）：resident 漂移规格压测（CUSUM 非正交 + applied_eps）/ 消融矩阵单臂优先级 / 活性断言"容差外显著"判据（固定 3× 门 vs 配对置换）/ R3 建议聚焦哪里。
- **O3-D 验证提示**（`/Data1/ericyang/r3_os_references/M11_HANDOFF_O3D.md`）：grade 三态差分测试构造、`--fix` 三重防误截、击杀验收工程化（cosmic-ray baseline+差分、`survival_rate --fail-over` CI 硬门）。

---

## 6. 复核入口（一页命令）

```bash
cd /Data1/ericyang/dry_wet_agent_os
# 全量回归（549 测试；轻量本机跑，务必带此环境变量）
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q

# lint / 卫生（pre-commit：ruff / codespell / 异常吞并棘轮）
pre-commit run --all-files

# 机制活性守门（三变异击杀 + 消融三臂接线；R1-2 头号教训的效果证据）
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_mechanism_activity.py -q

# 变异沙盒（红队三变异击杀实录；只读转录见 scratchpad/mut/kill_record.out）
python scratchpad/mut/mutate.py            # 沙盒内，勿动仓库/ runs

# 聚合器（resweep 数字独立复算入口；产物 runs/r1_resweep/report/）
python runs/r1_resweep/_tools/aggregate_resweep.py

# 待发射两批（Slurm 恢复走 sbatch；PartitionDown 期授权 ssh g208/g209 直跑）
sinfo                                       # 先探通道
sbatch runs/ablation/ablation.sbatch        # 消融 1240 格
sbatch runs/resident_sweep/resident.sbatch  # resident 240 格
# g209 直跑通道（回退）：bash runs/r1_resweep/_tools/run_node.sh <shard.tsv>
```

*基调重申：加固不是否定。最需要红队先看的三处——(§3) H1_REJECTED_os_worse 的根因判读、
(§1/§4) 批次选批方向是否需根修、(§2) grade 三态与失活熔断参数的依据。R3 终审见。*
