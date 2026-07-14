# M10 三幕 Demo 演示剧本

> 配套一键脚本 `scripts/make_demo.py`（产物归档到 `runs/demo/`）。三幕合计 ≤10 分钟。
> 验收标准见 `docs/ARCHITECTURE.md §15`；对比方法学见 `docs/M9_PROTOCOL.md`。
> **开演前先跑一次**（约 3–6 min，视机器负载；幂等，可提前预热）：
>
> ```bash
> python3 scripts/make_demo.py --out runs/demo        # 默认 rounds=5、单 seed 双臂
> ```
>
> 产物一览：`runs/demo/demo_narrative.md`（自动解说，含真实数字）、
> `runs/demo/act1/compare.png`、`runs/demo/act2/loop.png`、`runs/demo/act3/boundary_demo.txt`。

---

## 第一幕　假最优狙击（约 4 min）

**准备命令**（脚本已跑完则直接开图）：

```bash
open runs/demo/act1/compare.png            # 主视觉
cat  runs/demo/demo_narrative.md           # 自动解说（口播兜底）
```

**口播要点**（一切数字以 `demo_narrative.md` 的实跑数字为准——解说跑内条件化生成，无硬编码结论）：

- 同一模拟器、同一 seed、同一预算，只换裁决策略：naive 全信 vs os 三级 QC 信任路由。
- 第 3 轮注入强边缘蒸发（`strength=0.5`），把某平庸**边缘孔**读数抬成全场最高——假最优。
- **naive 一侧按解说分支二选一**：
  - **若该 seed 的 naive best 越过物理上限 1.0**（个别种子的戏剧性案例，如全量扫描十种子中
    仅 s1007 冲到 ≈1.064）<!-- R1 重跑后更新此数字 -->：强调"数值物理上不可能，却成了 naive
    后续烧预算的追逐目标"——假最优铁证。
  - **否则**（如默认 seed=7 实测 best≈0.975，不超上限）<!-- R1 重跑后更新此数字 -->：讲**假最优
    命中**——naive 推荐点的测量值被伪影抬高超真值 3σ（解说给出 measured / true / 3σ 真实数字）；
    稳健的分布式主张是全量扫描的假最优命中率 **naive 1.00 vs os 0.20**。<!-- R1 重跑后更新此数字 -->
- **os** 由四角哨兵同步偏高 + 边缘配对回归判 SUSPECT，归因 `edge_evaporation`，
  自动下中心位复测证伪；被判 SUSPECT/FAILED 的观测**不进响应模型训练集**，`best_trusted` 物理合理。
- 第 3 轮边缘检查命中数、隔离数/归因数以解说实跑数字为准；两臂曲线**第 3 轮分叉**
  即整个项目的论点。

**观众该看**：`compare.png`——红线（naive）第 3 轮被伪影抬离蓝线（若该 seed 超上限则直接跳过
1.0 虚线）、蓝线（os）压在 1.0 以下；黄色竖线标注"第 3 轮假最优"事件。想看审计链就翻
`runs/demo/runs/act1_crystal_os_s7/events.jsonl` 里第 3 轮的 `qc_report` / `attribution` /
`routing` 事件。

---

## 第二幕　热插拔（约 2 min）

**准备命令**（证明"换域只换配置"——同一 `os` 闭环，唯一差别是 `--domain`）：

```bash
open runs/demo/act2/loop.png
# 等价单跑：python3 scripts/run_loop.py --domain coating --mode os --rounds 3 --out runs/coat_os
```

**口播要点**：

- 从 crystal（结晶）切到 coating（咖啡环涂层均匀性）——**内核零改动一行**：换的是
  `domains/coating.yaml` + `sim_coating` adapter，QC/归因/失败模型/规划器全部复用。
- `loop.png` 上：逐轮 best-so-far 正常上升、每轮 TRUSTED/SUSPECT/FAILED 计数堆叠条照常产出——
  "OS ≠ pipeline，换实验领域 = 换一个 YAML + 一个 adapter"这条公理，眼见为实。

**观众该看**：`act2/loop.png` 上半折线在爬、下半信任裁决计数条非空（证明同一套 QC 机制在新域生效）。

---

## 第三幕　边界即类型（约 2 min）

**准备命令**：

```bash
cat runs/demo/act3/boundary_demo.txt
```

**口播要点**（"agent 有建议权、无裁决权"不是口号，是日志层可机器检查的类型不变量）：

- agent 提交一条合法提案（`action_proposal`，建议权，允许）。
- **伪造尝试 A**：agent 直接调裁决 API 自我 accept → `LifecycleError` 当场**拒绝**
  （`actor=agent` 无裁决权，`ADJUDICATOR_ACTORS` 只认 planner/human）。
- **伪造尝试 B**：绕过 API 把伪造 `acceptance` 记录**硬写进事件日志**——记录进得去，但
  `lifecycle._resolutions` 按 `actor` 过滤、日志层忽略它：提案仍 `unresolved`、`accepted` 为空。
- 合法路径：planner 给出 rejection，提案才有配对裁定——"提案必须有 acceptance/rejection 配对
  才可能影响下一轮设计"这条审计不变量，机器可查。

**观众该看**：`boundary_demo.txt` 里 `[2]` 行的 `→ 被【拒绝】：LifecycleError` 与 `[3]` 行
`伪造被【拒绝】采信：accepted=[]`——两条伪造路径都进不了裁决视图。

---

## 常见问答（三条）

**Q1　QC 税多少？会不会过度隔离好数据反而虚假获胜？**
零伪影场景假阳性 SUSPECT 率是硬验收线 **≤5%**（`docs/adr/0001`）；M5 实测**闭环级 0%、板级 2.5%**，
远低于红线。测试 `tests/test_loop_os.py::test_zero_artifact_qc_tax` 每次 CI 守住这条线——
这正是回应 Deming tampering（对稳定过程过度反应放大方差，REFERENCE_MAP §15）的设计。

**Q2　换真实仪器要什么？**
换 `BenchAdapter`（`expos/adapters/bench_manual.py`），协议同构：`prepare(exp)` 输出人类可读
worklist（每孔配液指令 + 板图 CSV/Markdown），台面执行后经 `adapters/ingest/`（CSV/图像）回灌成
`ObservationObject(trust=PENDING)`。**内核、QC、模型、规划器全部不改**——仿真器只是可控真值的执行器之一。

**Q3　和 robust BO 有什么区别？**
robust BO（副本中位数 / Huber、RAHBO、RCGP-UCB）吸收的是**孤立/对称**噪声与离群；M9 的
`robust-blind` 臂正是这个工程上限。但边缘/梯度/批次是**结构性空间偏差**——中位数救不了（三副本同处
边缘一起被抬），robust 统计无位置与 provenance 概念。os 靠哨兵+分层随机化+失败归因把它辨识并证伪，
这是信任路由不可被鲁棒统计替代的增量（预注册假设 H1，`docs/M9_PROTOCOL.md §7`）。文献 SDL 中位
加速因子 AF≈6（REFERENCE_MAP §11.3）——结构性伪影下 os 相对 naive/robust 的领先即来源于此。
