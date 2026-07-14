# RUN_MANIFEST_SPEC —— 运行清单规范 v1

> 状态：**规范草案**（第九轮平台研究族5/6/7 交付：PostgreSQL 存储纪律 · Git 可审计性 · Nix 可复现性）。
> 权威蓝图以 docs/ARCHITECTURE.md 为准；本规范只规定 `runs/<name>/manifest.json` 的字段与语义。
> 三源对照一句话：**Nix**——输出由输入闭包的哈希决定（"same inputs ⇒ same output path"）；**Git**——单根哈希经 Merkle DAG 传递地钉住全部内容；**PG**——log-before-data 的先行写纪律 + 版本不覆盖。
>
> 落地档位标注：`[NOW]` 现已落盘（字段可从现有 config/checkpoint/对象直接派生）· `[M9]` M9 补齐（随 §M9_PROTOCOL 扫描协议落地）· `[POST]` post-M10（插件生态/ABI 注册表成熟后）。
> manifest 是 §13.13 已声明的 **commit marker**：先写全本轮产物、再原子 bump manifest；UI/`expos check` 只以它作心跳与真相锁。它**新增、不改写** config.json / checkpoint.json（见 §6）。

---

## 1. 身份（identity）

```jsonc
{
  "manifest_version": 1,                         // [NOW] 见 §5：即 checkpoint_version，ABI 对齐锚点
  "run_id": "crystal__os__s7",                   // [NOW] 确定性命名，见下
  "scenario_id": "S2.edge_evaporation.0.20",     // [M9] 场景族.注入器.幅度档（M9_PROTOCOL §4）
  "arm": "os",                                   // [NOW] naive|robust-blind|os
  "created_at": "2026-07-10T10:04:06Z",          // [NOW] utc_now()，写一次不重写
  "domain": "crystal",                           // [NOW] 复制自 config.json（冗余便于离线索引）
}
```

- **确定性命名**（Nix 输出路径哲学的轻量投影）：`run_id = <scenario>__<arm>__<seed>`（缺 scenario 时退化为 `<domain>__<arm>__s<seed>`）。同三元组恒同名 → Slurm 扫描"确定性命名防重跑"（§18.1/族2）直接由此保证；`__` 双下划线为不可出现于分量的分隔符。
- `created_at` **写一次**语义（§4 三类写之一），与 events.jsonl 的 seq=0 事件 ts 对齐。

## 2. 输入闭包（input closure，Nix 式）

> 原则：run 的产物应由其**输入闭包的哈希**完全决定。Nix "requires that all inputs be explicitly collected"——我们同样**显式罗列**、不做扫描推断。闭包三层：域规范 + 代码版本 + 随机源，M9 起加依赖指纹，post-M10 加插件血缘。

```jsonc
"input_closure": {
  "domain_yaml_sha256": "…",        // [NOW] 对 config.json.domain_config 规范化(JCS/sort_keys)序列化取 sha256
  "expos_version": "0.1.0",         // [NOW] pyproject 版本
  "expos_git_sha": null,            // [NOW 若可得] 当前工作树非 git 仓时为 null；入仓后回填（Git 式代码血缘）
  "seeds": {                        // [NOW] 确定性种子三元（loop.derive_seed 的根）
    "np": 7,                        //   base_seed：模型/采样主流
    "layout": "derive_seed(seed,'layout',round)",   // 版位分配子流
    "artifact": "derive_seed(seed,'artifact',scenario_id)"  // 伪影注入子流（M9_PROTOCOL §4）
  },
  "python_env_sha256": null,        // [M9] `pip freeze` 规范化后 sha256（environment fingerprint）
  "plugins_lock": null              // [POST] plugins.lock：每插件 (name, git_sha) 钉死——git-SHA 即血缘（§18.1 族4）
}
```

- **domain_yaml_sha256**：对 `domain_config` 快照做键排序 JSON 规范化再哈希——域 yaml 任一字段变更即换指纹，等价 Nix "changes to inputs alter the hash"。
- **seeds 三元**：一个 base seed 经 `derive_seed(seed, part, …)` 稳定派生全部子流（loop.py 已实现），manifest 只记录 base 与派生配方，不记录展开值（可重算=纯函数）。
- **plugins_lock（POST）**：仿 flake.lock——把每个"可变 ref"钉到不可变 git-SHA；registry 即普通 git 仓、ref=SHA 落事件日志，"哪个插件版本产了这条观测"天生可查。远端加载**必须签名/allowlist**（§18.1 红线）。
- **python_env_sha256（M9）**：只进 manifest 的**哈希**、不进内核逻辑（避免依赖漂移影响裁决）；用于事后可复现性审计与 taint（见 §4 未锁环境）。

### 2.1 环境指纹（environment fingerprint，Nix flake.lock 式）

> Nix flakes：`inputs` 全部**锁定到不可变节点**（flake.lock），derivation 的输出路径由输入闭包内容寻址决定。expos 不引入 nix，但把"环境闭包"显式罗列进 manifest，使"same inputs ⇒ same output"可**事后核验**（不做构建隔离，仅做审计指纹）。字段全部只读、派生自运行时探针，**不参与裁决**。

```jsonc
"environment": {                    // [M9] 全部只进 manifest、不进内核逻辑
  "python_version": "3.11.9",       //   platform.python_version()
  "platform": "linux-x86_64",       //   sys.platform + machine（跨机复现的粗粒度水印）
  "key_libs": {                     //   裁决/建模相关库的精确版本（漂移即换指纹）
    "numpy": "1.26.4", "scipy": "1.13.0",
    "scikit-learn": "1.4.2", "pydantic": "2.7.1"
  },
  "python_env_sha256": "…"          //   = §2.python_env_sha256：`pip freeze` 键排序规范化后 sha256（full closure）
}
```

- **两级指纹**：`key_libs`（人可读、裁决相关子集）是审计首看项；`python_env_sha256`（全闭包哈希）是 flake.lock 级完备指纹——二者不一致时以哈希为准并记 note。
- **锁定 vs 探针**：flake.lock 是"求解并钉死"，expos 现阶段只做"运行时探针快照"（记录实际用到的版本），不主动求解锁文件；`plugins_lock`（§2, POST）才是真正的钉死语义。
- **taint 联动**：`environment` 缺失或 `python_env_sha256=null` ⇒ 置 `taint.unlocked_env`（§4，未锁环境水印，不阻断）。

## 3. 产物指纹（artifact fingerprint，Git 式）

> Git：blob 由内容哈希寻址，tree 聚合子哈希，单根哈希传递地钉住全部内容（Merkle DAG）。run 产物同构：每文件一 sha256（blob），全 run 一 merkle 根（root tree）。

```jsonc
"artifacts": {                      // [M9]
  "files": {                        //   路径 → sha256（相对 run 根；含 experiments/ observations/ models/ events.jsonl）
    "events.jsonl": "…",
    "observations/obs_84d9400b50.json": "…"
  },
  "merkle_root": "…",               //   files 键排序后逐条 (path,sha256) 累积哈希 → 单根
  "round_count": 4                  //   commit marker：先写满 N 轮产物再 bump（UI 见 N 即 N 完整可读）
}
```

- **merkle_root 是"事件×产物双向对账"（§18.2 洞见1）的载体**：resume 的"完成"由**产物谓词**（文件在且 sha256 与 manifest 一致）× 事件断言联合裁定；不一致报 **CorruptedRun**（不静默续跑）。同一组谓词驱动 `expos check`。
- 单文件哈希已有先例：`RawDataRef.sha256`、`ObservationObject` 侧字段（objects.py）——本节把逐文件哈希上卷成一棵树。
- 内容寻址天然去重/防篡改（Git "the hash of corrupted content does not match its name"）。

## 4. taint 位域（§13.2）

```jsonc
"taint": {                          // [M9] 单调不可逆位域；置位后本 run 及派生产物永久带水印
  "community_plugin_used": false,   // [POST 触发]
  "unsigned_plugin": false,         // [POST 触发]
  "allowlist_override": false,      // [POST 触发]
  "agent_suggestion_auto_applied": false,
  "out_of_budget_forced": false,
  "manual_data_edit": false,        // override 通道人工改判即置位
  "unlocked_env": false             // [M9] python_env 未锁/缺失时置位
}
```

- 语义照 tainted-kernels：**只标注不阻断**（硬拒绝是 enforce 开关的事）；分诊铁律——**先看 taint、bug 先在 untainted 配置复现**（§13.6 CorruptedRun 分诊顺序第 1 步）。位域随事件单调置位，写入 manifest 时取并集，永不清零。

## 5. checkpoint_version 与 ABI 对齐规则（§13.4）

- `manifest_version` **即 checkpoint_version**：作为 ABI 注册表 `docs/abi/*` 的 `Since-version` 锚点（弃日历日期，git 史更准）。当前值 **1**。
- **stable 键集守门**：manifest 实写键集 ⊇ `docs/abi/stable/` 声明的键集（§13.4 CI 判据：tracepoint=kind+必填字段进 stable，evidence 内自由字段非 ABI）。§1/§2 的身份与闭包字段进 stable；§3 的 `files` 明细为**证据**（读者不得逐键硬依赖，只依赖 `merkle_root`）。
- 升降级照 ABI README：新字段默认 testing；testing→stable 需真实 Consumer + 覆盖测试；stable 只能经 obsolete 退场且历史 run 仍可读（PG catalog version 精神：**版本不符则响亮拒绝运行**，而非静默误读）。

## 6. 与 config.json / checkpoint.json 的迁移关系（不破坏现有 run）

- **纯新增**：manifest.json 是新文件，`config.json`（domain/mode/seed/domain_config）与 `checkpoint.json`（completed_rounds/budget/planner）字段与写语义**一字不改**；manifest 的身份/闭包字段全部**派生自**二者（§1/§2 标 [NOW] 者即此派生），无双源真相。
- **旧 run 兼容**（PG pg_upgrade 精神——"reuse the old user data files"）：缺 manifest.json 的历史 run 视为 `manifest_version=0`（legacy）；`expos check` 从 config.json 重建身份、**跳过**输入闭包/merkle 断言并**响亮记 note**（降级审计，非 FAIL——SKIP≠FAIL，§13.6）。
- **回填路径**：对已有 run 可事后重算 §2/§3（域快照与产物都在盘上、种子在 config），一次性生成 manifest 而不重跑 campaign——审计能力可追溯加装。
- **写序纪律**（PG WAL log-before-data 同构）：本项目**仅 checkpoint 路径**严格遵循"先事件后检查点"（store.py `write_checkpoint`：先 append_event 再原子写 → 崩溃时 checkpoint 落后于日志，保守重做，安全偏斜）。manifest 作 commit marker 位于该序**之后**——先落全轮产物与事件、再 bump manifest.round_count，崩溃时 manifest 落后于产物 ⇒ 保守重做该轮，与 PG "redo from last checkpoint" 一致。
- **WAL 完备性（已修复，PG "先写日志再改页"对照，走读 lifecycle.py 核实）**：`advance_status` / `route_observation` / `reclassify` 三条对象改判路径**已统一为 log-before-data**——**先 append_event（reclassify 另含 OVERRIDE DecisionRecord）、后写物化视图（save_experiment/save_observation）**，与 checkpoint 的 WAL 序一致。三条路径上崩溃于两步之间时，偏斜方向从"视图领先日志"翻转为**"日志领先视图"**（保守、可重放修复的安全方向）：事件已落但视图未改，重放据日志即可修复，不再出现"trust 落盘却无事件解释"的审计丢失。核实翻转不改 payload 内容——三处 payload 均只引用入参与改判**前**的旧状态（`from_trust`/`from_routing`/`old` 在物化前构造），写序无关。故 events.jsonl 现为**这三条路径上的严格 WAL**；`expos check` 的"事件×产物对账"（§3）遇"日志领先视图"的单步偏斜按**保守重做**处理，不误判 CorruptedRun。配套：`RunStore.append_event` 单行原子写、`read_events` 容忍 torn tail（仅物理末行半写，logging 告警）并**显式校验 seq 连续**（回退/跳跃响亮拒读，旧无 seq run 按行序补虚拟值兼容）——EVENT_SCHEMA §0.1 两条鲁棒性约束已落地。

## 7. 模型快照的 registry 语义（mlflow 式，post-M10）

> mlflow model registry：`registered_model`（逻辑名）下挂多个 `model_version`（version + current_stage∈{None,Staging,Production,Archived} + source/run_id 血缘）。expos 的 `models/snapshot_r<r>.json` 已是**内容寻址的版本**（snapshot() 返回 (X,y) 联合 lexsort 后的 sha256 训练集指纹 + n_train），但缺 stage 与显式 lineage 字段。本节规定 post-M10 的最小补齐（仅 manifest/事件侧新增，不改模型代码）。

```jsonc
"model_registry": {                       // [POST] 每轮一版本，逻辑名 = run_id
  "registered_model": "crystal__os__s7",  //   = run_id（一条 campaign 一个逻辑模型）
  "versions": [{
    "version": 3,                         //   = round_id（单调递增，mlflow version 语义）
    "fingerprint": "a1b2c3d4e5f60718",    //   [NOW 已有] model.snapshot()：(X,y) 训练集内容指纹
    "n_train": 12,                         //   [NOW 已有] 训练点数
    "stage": "response",                   //   [POST 缺] 阶段：response|failure|archived（对齐 mlflow stage；
                                           //     expos 语义 = 该快照喂哪个模型面，非部署环境）
    "arm": "os",                          //   [POST 缺] 产出臂（naive/robust/rcgp/os/os-soft）
    "lineage": {                          //   [POST 缺] 血缘：哪轮哪些观测训练出此版本
      "trained_on_obs": ["obs_84d9…", "obs_b250…"],  //   进训练集的 TRUSTED obs_id（reclassify 后以最终裁决为准）
      "excluded_suspect": ["obs_c394…"],  //   被 QC 结构性隔离出训练集的 obs_id（可审计"为何没学它"）
      "aggregation": "ReplicateVarianceAggregation",  //   聚合策略名（软信任臂的 alpha 降权在此标注）
      "based_on_event_seq": 41            //   产出此快照的 model_updated 事件 seq（钉回事件日志）
    }
  }]
}
```

- **version = round_id**：闭环每轮重训一版，天然单调，无需额外计数器（mlflow 自增 version 的轻量投影）。
- **stage 语义再定义**：mlflow 的 stage 指部署环境；expos 无部署，借用为"模型面"——`response`（响应模型正例）/ `failure`（失败模型正例）/ `archived`（被后续轮取代）。ADR 待定，避免与 mlflow 语义混淆。
- **lineage 已可从事件日志重算**：`trained_on_obs` = 该轮 `aggregation.prepare` 的入料 obs_id，`model_updated` 事件已含 snapshot 与 n_train——post-M10 只是把这份血缘**物化进 manifest** 便于离线查询，非新增真相源（与 §6 无双源真相一致）。
- **改判传播**：reclassify 把某 obs 从 TRUSTED 改判后，后续轮重训会自然把它移出 `trained_on_obs`；历史版本的 lineage **不回改**（append-only），"这一版当时学过它"是可审计事实。

---

## 8. CampaignManifest —— 战役级清单（OpenLineage 父子 run 式）

> RUN_MANIFEST v1（§1–§7）契约单个 `runs/<name>/manifest.json`。R1/R2 后出现 **campaign 级**实体（`runs/r1_resweep`：2700 格、双通道分片、修复前后配对）——OpenLineage 的 `ParentRunFacet`(RootRun{runId}+RootJob{namespace,name}) 恰是"父 job 派生子 run"的建模。此处规定 `runs/<campaign>/campaign_manifest.json`（`manifest_kind:"campaign"`），与单-run manifest **同级不嵌套**、各自内容寻址。首个实例已回填：`runs/r1_resweep/campaign_manifest.json`。

```jsonc
{
  "manifest_kind": "campaign", "campaign_manifest_version": 1,   // [NOW]
  "campaign_id": "r1_resweep", "created_at": "…Z",               // [NOW] 确定性命名，父 job 名
  "grid": {                                                       // [NOW] 格子清单即战役输入闭包
    "cells_manifest": "cells.tsv", "cells_sha256": "cef926f8…",  //   权威格子表 + 内容哈希（改一行即换指纹）
    "n_cells": 2700, "n_scenario_ids": 48, "seed_set": "B",
    "arm_distribution": {"naive":580,"os":960,"os-soft":960,"robust":200},
    "cell_naming_rule": "<scenario_id>__<arm>__s<seed>"           //   与 §1 确定性命名同源，可重算不展开
  },
  "code_fingerprint": {                                           // [NOW] 非 git 仓：钉 MANIFEST.sha256 而非 git-SHA
    "expos_version": "0.1.0", "expos_git_sha": null,
    "manifest_sha256_ref": "expos_backup_20260710/MANIFEST.sha256", //   121 文件 SHA256 注册表（REPRODUCE.md 校验闸）
    "scaffold_generator_sha256": "0024442e…"                      //   gen_resweep.py：物料确定性再生源
  },
  "execution_channels": [                                         // [NOW] 多通道皆幂等同名 → 无双写真相冲突
    {"channel":"sbatch","script_sha256":"c3f0e6e9…","segments":[…]}, //  DefPar %50，MaxArraySize 拆三段 OFFSET
    {"channel":"ssh_dual_node","script_sha256":"67df72f2…","shards":[  // PartitionDown 回退：ssh g208/g209
      {"node":"g208","shard_sha256":"8d2f82ba…","n_rows":1350}, …]}
  ],
  "aggregator": {"script_sha256":"ce50596b…","status":"pending","cross_sweep_pairing":"full_sweep 旧 os ↔ resweep 新 os by (scenario,seed)"}, // [NOW] 聚合器版本冻结
  "run_hierarchy": {                                              // [NOW] 父子关系（OL ParentRunFacet 投影）
    "parent_job": {"namespace":"expos/crystal","name":"r1_resweep"},
    "children": {"enumerated_by":"cells.tsv","count":2700,        //   子 run manifest = 各 cell 目录 config/checkpoint/events
      "parent_ref_rule":"子.ParentRunFacet.runId=UUIDv5(ns,campaign_id)"}
  }
}
```

- **不展开 2700 子 run**：与 §2 seeds 同哲学——记清单+确定性规则，可重算，不落展开值。
- **多执行通道等价性**：sbatch 与 ssh 双节点产**同名幂等** run 目录（`completed_rounds>=8` 跳过），通道差异不进产物指纹、只作审计溯源；把 §1 确定性命名从"防重跑"扩到"跨通道对账"。本仓非 git，`code_fingerprint` 钉备份包 `MANIFEST.sha256`（§2 `git_sha=null` 回填路径），语义等价 Git 单根哈希。

## 9. ClaimDecision 血缘 —— 主张到数据的五级追溯链

> PAPER_OUTLINE 的每条主张（可信性 p 值、H1 终判）必须机器可溯到原始格子，否则红队 G 路靠人肉复算。本节形式化 **主张→判定函数→stats_tests 行→cells 集合→代码指纹** 五级链，是 ARCH_V2 §4「协议即代码」的 manifest 侧落地：协议 `protocol.yaml` 定义判定，本 schema 记录**某次判定的血缘实例**。

```jsonc
"claim_decision": {                                    // [M9] 每条报告主张一条，落 report/claim_decisions.json
  "claim_id": "H1p.os_vs_robust.decision_risk",        // ① 主张 id：PAPER_OUTLINE/protocol.yaml hypotheses[].id
  "verdict": "PASS",                                   //   PASS|FAIL|UNRUN（unrun_is_fail：声明未跑=红）
  "decision_fn": "expos.eval.stats_tests:compare_arms_paired",  // ② 判定函数按名引用（ARCH_V2 §4 禁闭包）
  "test_spec": {"kind":"paired_permutation","alpha":0.05,"correction":"holm","ci":"bca_bootstrap"},
  "stats_rows": [                                       // ③ 证据行：钉回 stats_tests.csv 的具体行
    {"file":"report/stats_tests.csv","scenario":"S2.edge_evaporation.0.2",
     "comparison":"os_vs_robust","metric":"decision_risk","p_value":0.0029,"n_pairs":20}
  ],
  "cells": {                                            // ④ 参与格子集合（可审计"哪批格子支撑此数"）
    "campaign": "r1_resweep", "cells_sha256": "cef926f8…",
    "selector": "scenario_id∈{S2.edge>=0.2,S2.batch<=-0.18,S4.*} ∧ arm∈{os,robust} ∧ seed_set=B",
    "run_ids_ref": "cells.tsv 子集（selector 求值，可重算不展开）"
  },
  "code_fingerprint": {                                 // ⑤ 代码指纹：判定+数据两侧文件 sha 冻结
    "protocol_sha256": null,                            //   [M9] = schema_sha + fn_files_sha（ARCH_V2 §4/R2 裁定）
    "fn_files_sha256": {"expos/eval/stats_tests.py":"…"}, //   fn 按名引用锁不住函数体 → 文件 sha 一并冻结
    "manifest_sha256_ref": "expos_backup_20260710/MANIFEST.sha256"
  }
}
```

- **五级链闭合**：主张(①)→机器判定函数(②)→冻结的统计行(③)→内容寻址的格子集(④)→代码指纹(⑤)，任一级换指纹即报告主张失效——把 EXP012（文档假设句必带机器判定锚）落成血缘记录，杜绝 R1-4 沉默缺口与门面/机器判定漂移。
- **与 protocol.yaml 的关系**：`protocol.yaml`（ARCH_V2 §4）是**预注册模板**（hypotheses/claims/test 声明），本 `claim_decision` 是**一次执行的血缘快照**（填入 verdict/p/CI + protocol_sha256）——模板哈希进快照，"报告里每个过/不过都引用机器判定产物"由此成立。
- **UNRUN 显形**：selector 命中 0 格或 stats_rows 缺失 ⇒ `verdict:"UNRUN"`（红），非静默跳过（`unrun_is_fail`）。

## 10. ModelSnapshot registry 语义（承 §7，post-M10）

> §7 规定了 registry 骨架，但写于 fingerprint 仅含 (X,y) 之时；`response_gp.snapshot()` 现已把 **拟合出的核超参 theta + alpha 模式** 纳入指纹（R1-5(c) 修复：训练数据同、拟合态不同不再盲）。本节把 §7.model_registry 的 `fingerprint` 语义**收紧到 (X,y,theta,alpha_mode) 三元指纹**，并补 registry 血缘字段的 post-M10 落地口径（仅 manifest/事件侧新增，不改模型代码）。

```jsonc
"model_registry": {                        // [POST] 承 §7，逻辑名=run_id，每轮一 version
  "versions": [{
    "version": 3,                          // [NOW] = round_id（mlflow 自增 version 投影）
    "fingerprint": "a1b2c3d4e5f60718",     // [NOW 已升级] snapshot()=sha256((X,y) lexsort ‖ theta round1e-10 ‖ alpha_mode)
    "fingerprint_inputs": ["Xy_lexsort","kernel_theta@1e-10","alpha_mode"],  // [POST] 指纹构成显式罗列（审计可复算）
    "n_train": 12,                         // [NOW 已有]
    "stage": "response",                   // [POST] 模型面：response|failure|archived（借 mlflow current_stage 位，非部署环境）
    "alias": "latest_response",            // [POST] mlflow 3 aliases 语义（current_stage 已弃用）：稳定别名指向某 version
    "lineage": {                           // [POST] 血缘可从事件日志重算，此处物化便于离线查询
      "trained_on_obs": ["obs_84d9…"],     //   进训练集的 TRUSTED obs_id（reclassify 后以最终裁决为准）
      "excluded_suspect": ["obs_c394…"],   //   被 QC 结构性隔离出训练集的 obs_id
      "aggregation": "ReplicateVarianceAggregation",
      "based_on_event_seq": 41,            //   产出此快照的 model_updated 事件 seq（钉回 events.jsonl）
      "parent_campaign": "r1_resweep"      // [POST] 若隶属战役 → 反指 §8 campaign_manifest（跨层血缘闭合）
    }
  }]
}
```

- **指纹三元升级的 registry 含义**：mlflow `model_version` 靠 `source`+`run_id` 溯源，expos 靠**内容寻址指纹**——theta 入指纹后"同数据不同拟合态"产**不同 version**，resume 重建与一次跑完发散会响亮暴露（不静默共版本）；跨 BLAS 差异超 1e-10 容差即报发散（§7 既定：宁假阳不静默吞）。
- **stage vs alias（mlflow 3 对齐）**：mlflow 已弃 `current_stage` 转 `aliases`（可变别名指向不可变 version）；expos 同采——`stage` 记"模型面"（response/failure/archived），`alias` 记稳定引用（如 `latest_response`），历史 version 的 `lineage` append-only 不回改。
- **跨层血缘**：`lineage.parent_campaign` 使 model version → cell run → §8 campaign 三层可溯，与 §9 claim_decision 的 cells 集在同一 `cells_sha256` 上对账。

---

## 附录 A：血缘互操作三出口（post-M10，均只出不入）

> 前沿复查（2026-07，frontier_B_lineage）：单一 OpenLineage 出口已不完整——三出口互补：**A.1 OpenLineage=过程血缘**（run/round 时序事件流）、**A.2 Croissant 1.1=成品数据集质量出口**（trust/taint 被生态原生消费的最优通道）、**A.3 RO-Crate=run 目录整体打包为 FAIR 证据包**。三者均为旁路导出器，失败不影响 run 正确性。
>
> **诚实定位**：科学域尚无「事件日志一等公民」的先例（2026-07 复查——SDL 界普遍是半结构化会话日志，未把 append-only 流当权威真相源），本规范的 events.jsonl 真相源+确定性重建在科学语境属**新立论**；工程实践借企业级 event-sourcing 的 snapshot/replay/版本化。

### A.1 OpenLineage 出口（过程血缘）

> OpenLineage 对象模型：`RunEvent`(eventType∈START/RUNNING/COMPLETE/ABORT/FAIL) 携 `run`(runId:UUID + facets) + `job`(namespace+name + facets) + `inputs[]`/`outputs[]`(Dataset + facets)。facet 是**可扩展命名空间**（`_producer`/`_schemaURL` 必填，自定义 facet 挂 `expos_` 前缀即合法）。expos **不改内核**，导出器读 events.jsonl + 物化视图 + manifest，投影为 OL 事件流（只出不入）。改判天然映射为 OL 的 facet 版本化（store.py 已按此模式设计）。

| expos 概念 | OpenLineage 目标 | 关键 facet / 字段 |
|---|---|---|
| campaign / run（一个 run 目录） | **Job**（`namespace="expos/<domain>"`, `name=run_id`） | `SourceCodeJobFacet`←expos_git_sha；`JobTypeJobFacet`(integration=expos, jobType=CAMPAIGN) |
| 一轮 round | **Run**（`runId`=UUIDv5(run_id, round_id) 确定性） | START@round_designed / COMPLETE@checkpoint；`NominalTimeRunFacet`←事件 ts；`ParentRunFacet`→campaign 根 run |
| run 的输入闭包（§2/§2.1） | `ExecutionParametersRunFacet`（标准，2025 新增）+ `expos_inputClosure` 补自定义 | seeds/design 入参走标准 facet；domain_yaml_sha256/environment/plugins_lock 留自定义；round→round 重训依赖链走 `JobDependenciesRunFacet` |
| taint 位域（§4） | Run facet `expos_taint`（自定义） | 单调位域并集；对应 mlflow/OL 无原生位，用自定义命名空间 |
| ExperimentObject（设计） | **input Dataset**（`name="<run_id>/experiments/<exp_id>"`） | `SchemaDatasetFacet`←design_space；provenance→`expos_design` 自定义 facet |
| ObservationObject 集合 | **output Dataset**（`name="<run_id>/observations/round_<r>"`） | `DataQualityMetricsDatasetFacet`←QCReport；`DatasetVersionDatasetFacet`←obs sha256/trust |
| QCReport / adjudicate 裁决 | `DataQualityAssertionsDatasetFacet` | 每 check→assertion(passed/level)；suspicion/confidence 入 metrics |
| reclassify（改判/翻案） | **新 RunEvent** + Dataset 的 `DatasetVersionDatasetFacet` **版本 bump** | 新事件 `ParentRunFacet` 引旧 run；version 单调递增=OL facet 版本化（store.py 既定模式） |
| DecisionRecord（提案/接受/驳回/override） | Run facet `expos_decisions`（自定义） | kind/actor/refs/accepted——提案↔裁定配对（lifecycle `_resolutions`）投影为 facet 内引用链 |
| model snapshot（§7） | **output Dataset**（`name="<run_id>/models/snapshot_r<r>"`） | `DatasetVersionDatasetFacet`←fingerprint；`trained_on_obs` 子集血缘走 **Subset dataset facets**（2025 新增）；`expos_modelRegistry` facet←stage/arm |
| events.jsonl（审计日志） | RunEvent 流的**真相源** | 导出器逐事件投影；merkle_root（§3）作 `_producer` 版本锚，防导出与产物漂移 |

- **命名空间纪律**（OpenLineage Naming.md）：`namespace` 用 `expos/<domain>`，dataset name 用 run 相对路径——与 §1 确定性命名同源，跨工具可对账。
- **producer URI**：所有事件 `_producer` = `https://…/expos@<expos_git_sha>`（Git 式代码血缘钉入每条 OL 记录）。
- **只出不入**：OL 导出是 post-M10 的**互操作出口**，不作内核输入；导出失败绝不影响 run 正确性（与 taint/manifest 一样为旁路审计设施）。

### A.2 Croissant 1.1 出口（成品数据集质量，trust/taint 首选通道）

> Croissant 1.1（MLCommons，2026-02）：record 级 PROV-O provenance（`wasDerivedFrom` 源+处理步骤+agent 归属）+ 字段级 validation rules + RAI 词表，HF/Kaggle/OpenML/PyTorch/TF 原生消费——trust 元数据走此通道**优于**自定义 OL facet。导出对象是"发布态"的观测数据集（run 结束后的 observations 集合），非时序事件。

| expos 概念 | Croissant 1.1 目标 |
|---|---|
| obs.trust / suspicion / trust_confidence | record 级 PROV-O 质量声明 + RAI robustness 字段（生态可原生过滤 SUSPECT 记录） |
| reclassify 改判链 | `wasRevisionOf`（PROV-O 谓词，锚回 events.jsonl 的 reclassification 事件 seq） |
| taint 位域（§4） | dataset 级 RAI 声明 + DUO/ODRL 治理字段（水印随数据集发布流转） |
| 观测→实验→模型血缘 | record 级 `wasDerivedFrom` → exp_id / snapshot fingerprint（chain-of-custody） |

### A.3 RO-Crate 出口（Provenance Run Crate，run 目录=FAIR 证据包）

一句话方案：run 目录整体打包为 **Provenance Run Crate**——`ro-crate-metadata.json` 以 Schema.org/PROV 描述 manifest.json（输入闭包+指纹）为 crate 根实体、events.jsonl 为 append-only 审计工件、observations//decisions 为 outputs/agents 实体，merkle_root（§3）作 crate 完整性锚；静态成品打包与 A.1 的时序事件流互补（改判时间线是 events.jsonl 强项、crate 弱项，故 crate 只引用不复述）。
