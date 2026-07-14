# expos 备份与完整重现检查点（2026-07-11）

> 本文件是备份快照的**重现契约**：环境指纹、产物清单、逐层验证命令、全量重跑配方。
> 项目状态：**M0–M10 十一个里程碑全部关账**（详见归档内 `dry_wet_agent_os/CHECKPOINTS.md`
> 的总览表与收官总条目——那是权威台账，本文件只负责"如何恢复与重现"）。

---

## 0. 2026-07-11 R1/R2 修复战役快照（增量）

红队 R1 报告（`docs/STRESS_TEST_R1.md`）逐条核实后，蓝队按**机制→口径→数字→门面**顺序修复，
完整对答见 **`docs/STRESS_TEST_R1_RESPONSE.md`**（R2 复审见 `docs/STRESS_TEST_R2.md` /
`docs/STRESS_TEST_R2_RESPONSE.md`）。一句话清单：

- **机制修复三件**：风险折扣读键纠正（`p_global` 契约键，缺键响亮失败）；风险图改逐孔现算真实
  `batch`（不再传 `None` 恒零）；`ewma`/`cusum` 温度漂移检查正式接线（此前零调用方、score 恒 0）。
- **崩溃一致性三处**：torn-tail 判据与 `read_events` 统一（能解析补 `\n` 不再误判截断）；
  checkpoint 落后重做改按 `round_id` 幂等对账（不再双计训练集）；resume snapshot 纳入 GP 超参
  指纹（逐轮逐位实测 EQUIVALENT）。
- **评测协议修正**：聚合器按 `seed_set` 严格分离 A/B（不再标定/评估混用）；污染率双口径
  （raw 观测 vs 各臂有效训练集）；robust 对照升级 replicates=3 + Huber IRLS 真保护
  （原 replicates=2 时 median≡mean 无保护）；补齐配对置换检验 + 95% CI。
- 全量回归 **479 passed / 0 failed**（sbatch job 4667159，`runs/r1_final_regress_4667159.log`）
  后，提交 2700 格 resweep（`runs/r1_resweep/`）。

**resweep 重现（双节点直跑，Slurm DefPar 分区曾 PartitionDown 的回退方案）**：

```bash
cd /Data1/ericyang/dry_wet_agent_os
# cells.tsv 已按主机预切分片：cells_g208.tsv / cells_g209.tsv（各行 scenario\tarm\tseed\tseed_set\tdomain_rel）
# 节点 1（g208）：
ssh g208 'cd /Data1/ericyang/dry_wet_agent_os/runs/r1_resweep && bash _tools/run_node.sh cells_g208.tsv 130'
# 节点 2（g209）：
ssh g209 'cd /Data1/ericyang/dry_wet_agent_os/runs/r1_resweep && bash _tools/run_node.sh cells_g209.tsv 130'
# run_cell 按 scenario__arm__seed 幂等命名——中断后重交同一分片安全，已完成格自动跳过。
# 聚合：PYTHONDONTWRITEBYTECODE=1 python3 runs/r1_resweep/_tools/aggregate_resweep.py
```

**最新回归基线**：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q --co -q` 当前收集
**539 tests**（仅统计用例数，未跑全量；上一次全量通过的基线仍是关账时的 479 passed/0 failed，
job 4667159——测试数增长是 R1/R2 战役新增判别性用例，尚未合并进一次完整全量回归，见上）。

---

## 1. 备份清单（本目录）

| 文件 | 内容 | 说明 |
|---|---|---|
| `expos_core_20260711.tar.gz` | 代码+文档+测试+域配置+UI+脚本+汇总报告 | **核心快照**。含 `runs/full_sweep/report/`、`runs/r1_resweep/report/`（R1 resweep 终局报告，2700/2700 格完成，见 §0）与各自 `{scenarios,cells.tsv,_tools}`（重跑所需全部输入与工具），排除大件原始格子（见 §2） |
| `expos_r1_resweep_rawdata_20260711.tar.gz` | `runs/r1_resweep/runs/` 2700 格原始 run 目录（全 JSON 事件流高压缩比） | **可选**：逐格 events/observations/truth 原始数据。没有它也能按 §5 / §0 resweep 命令从种子确定性重生 |
| `env.txt` | Python 3.13.12 + 关键包版本 + 内核 | 环境指纹（见 §3） |
| `MANIFEST.sha256` | 134 个源码/文档/配置文件的 SHA256 | 恢复后校验：`sha256sum -c MANIFEST.sha256`（在解包出的仓库根执行） |
| `ARCHIVES.sha256` | 两个 tar 包的 SHA256 | 归档完整性 |
| `REPRODUCE.md` | 本文件 | 仓库根另有同版副本 |

> 上一版 `/Data1/ericyang/expos_backup_20260710/` 仍原样保留（含 R1 修复前 `expos_full_sweep_rawdata_20260710.tar.gz`，1450 格 full_sweep 原始数据）——本次未覆盖，如需修复前的 full_sweep 原始格子去那份找。

**排除项（为什么不备）**：
- `references/`（35+ 外部克隆仓，仅供研究，全部可按 `docs/REFERENCE_MAP.md` 各节记载的 repo 名重新 `git clone --depth 1`）；
- `runs/full_sweep/runs/`、`runs/r1_resweep/runs/`（后者在第二个 tar 里）、`runs/pilot_sweep/runs/`、`runs/pilot_sweep/_local/` 原始/临时格子——即使丢失也可由 §5 / §0 确定性重生——这正是系统的设计能力；
- `__pycache__` / `.hypothesis` / `.pytest_cache`（运行时缓存）。

## 2. 恢复步骤

```bash
cd /Data1/ericyang            # 或任意目标位置
tar xzf expos_backup_20260711/expos_core_20260711.tar.gz          # → dry_wet_agent_os/
cd dry_wet_agent_os
sha256sum -c ../expos_backup_20260711/MANIFEST.sha256              # 全部 OK 才算恢复成功
python3 scripts/expos_lint.py                                       # 应全绿
# （可选）恢复 2700 格 r1_resweep 原始数据：
tar xzf ../expos_backup_20260711/expos_r1_resweep_rawdata_20260711.tar.gz -C ..
```

## 3. 环境（实测指纹，完整见 env.txt）

- Python **3.13.12**（miniforge，`/home/ericyang/miniforge3`）；Linux 5.15.0-160 x86_64
- numpy 2.4.6 / scipy 1.17.1 / **scikit-learn 1.9.0** / pydantic 2.13.4 / PyYAML 6.0.3 /
  matplotlib 3.11.0 / pandas 3.0.3 / pillow 12.2.0 / pytest 9.0.3 / hypothesis 6.156.4 /
  streamlit 1.59.1 / duckdb 1.5.4
- 安装：`python3 -m pip install -e .`（依赖见 `pyproject.toml`；UI 加 `.[ui]`，测试加 `.[dev]`）
- **环境敏感点（诚实记录）**：①sklearn GP 在个别种子发 ConvergenceWarning（无害，断言不依赖收敛质量）；②大版本更换 numpy/sklearn 可能使个别经验标定阈值（qc/checks.py 顶部常量）附近的单点测试漂移——若发生，先跑 §4 分层定位，属阈值敏感而非逻辑回归；③并行跑多个 pytest 会因 `__pycache__` 竞争产生假错——**一律带 `PYTHONDONTWRITEBYTECODE=1`**。

## 4. 逐层验证（快→慢）

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 scripts/expos_lint.py                       # 静态不变量守门：应"全绿"
python3 -m pytest tests/ -q                         # 全量 431 passed（关账时实测，~3min 空载）
python3 -m mkdocs build --strict                    # 文档站：应无 warning/error
python3 scripts/make_demo.py --out runs/demo        # 三幕 demo（假最优狙击/热插拔/边界即类型）
python3 -m expos.cli ui                             # 只读仪表盘（四页）
```

## 5. 全量科学结果重现（1450 格五臂扫描）

一切 run 由 `(scenario, arm, seed)` 三元组唯一决定（`derive_seed` 稳定派生、格子目录名
确定性、`run_cell` 幂等——已跑完的格子跳过）。**线程钉 1 是吞吐关键**（实测 ~250×）：

```bash
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
       NUMEXPR_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
# ① 生成矩阵（场景 yaml + cells.tsv；本备份的 runs/full_sweep/ 里已带原版）
#    注意：gen_sweep 的 --arms 默认仅 naive,os（robust/os-soft/rcgp 不入默认表）。
#    全 5 臂扫描由**两份 cells 拼成**（关账即用这两份，勿依赖默认重生）：
#      cells.tsv       = naive,os,robust    （--arms naive,os,robust）
#      cells_g209.tsv  = os-soft,rcgp（+naive 复用）
python3 scripts/gen_sweep.py --out runs/full_sweep --arms naive,os,robust
# ② 逐格执行（单格示例；全量按 cells.tsv 逐行并行分发，本机 ≤48 并发）
python3 -m expos.eval.run_cell --domain runs/full_sweep/scenarios/<sid>.yaml \
    --arm <naive|robust|os|os-soft|rcgp> --scenario <sid> --seed <n> \
    --rounds 8 --out-root runs/full_sweep/runs
# ③ 汇总（主表/检出率-幅度/归因精度-幅度曲线 → runs/full_sweep/report/）
python3 runs/full_sweep/_tools/aggregate.py
```

**对账基准（重跑后应复现的关账数字，出处 `runs/full_sweep/report/main_table.csv`）**：
S0.demo 末轮 regret os≈0.0086 vs naive≈0.0179、假最优命中 0.20 vs 1.00、污染率 0.004 vs
0.146；S1.zero QC 税 0.11%；batch −0.18 检出 20/20、归因精度 ≈0.997。**归因精度是"给出
结论者的命中率"**（correct/(correct+wrong)=1717/1722≈0.997），不是全孔正确率——同场景另有
**26% 孔判 inconclusive**（611/2333，证据不足/反驳未过→ top_cause=None，不计入精度分母；
出处 `report/attribution_curve.csv` 的 `inconclusive_rate`）。种子集与场景族以 `cells.tsv`
（naive/os/robust）与 `cells_g209.tsv`（os-soft/rcgp）两份为准。科学字段逐字段可复现；
`obs_id`/时间戳类 uuid 字段不复现（设计如此，
见 CHECKPOINTS M4 决策⑤）。

> 注意：本次关账后 `qc/checks.py` 的批次估计器已重构（身份无关加权回归）——若与归档报告
> 逐位对账，S2.batch 系列的 check 级明细会优于归档版（触发更稳），主表结论不变。

> **R1 修复后数字以 `runs/r1_resweep/report/` 为准**：截至本次快照已**生成完毕**（非"生成中"）——
> 2700/2700 格全部完成、0 失败，H1 字面终判 `H1_REJECTED_os_worse`（S2r3 中高档池化 os−robust
> mean_diff=+0.0161，置换 p=0.0001，95%CI=[+0.0108,+0.0216]；细节与逐场景表见
> `runs/r1_resweep/report/report.md`）。本节上面 §5 的旧对账基准出自 **R1 修复前**的
> `runs/full_sweep/report/`，按预注册纪律保留存档、不追溯覆写；两者并存供对比，差异即修复本身
> 的效果量。

## 6. 关键文档指针（解包后）

- `CHECKPOINTS.md` —— **权威台账**：M0–M10 全表 + 每里程碑验证命令/输出/决策偏离 + 收官总条目
- `docs/ARCHITECTURE.md` —— 权威蓝图；`docs/BUILD_PLAN.md` —— 里程碑定义与 Backlog
- `docs/REFERENCE_MAP.md` §1–§22 —— 十二轮调研台账（含被排除的 references/ 各仓名与借鉴结论）
- `docs/PAPER_OUTLINE.md` —— 论文骨架（三主张、图表清单、待补实验）
- `docs/{EVENT_SCHEMA,RUN_MANIFEST_SPEC,CAPABILITY_MODEL,CONTROLLER_MODEL}.md` —— 平台四规范
- `docs/{SOFT_TRUST_PROPOSAL,ADAPTER_ACTIONS,PLUGIN_API_DRAFT}.md` —— post-M10 设计稿

## 7. 复原后建议的第一步

```bash
git init && git add -A && git commit -m "expos M0-M10 closure snapshot (2026-07-10)"
```
（本项目开发期未用 git；恢复后建议立刻纳入版本控制，以后不再依赖 tar 快照。）
