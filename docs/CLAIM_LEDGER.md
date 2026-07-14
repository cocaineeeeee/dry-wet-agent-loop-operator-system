# CLAIM_LEDGER —— 主张到证据的机器可查账本（薄规范）

> 状态：**P0 落地**（用户架构裁决 mailbox/red_to_blue/020 §P0 第 2 条 + 护栏 021 §2）。
> 血缘 schema 以 [RUN_MANIFEST_SPEC §9 ClaimDecision 五级链](RUN_MANIFEST_SPEC.md) 为权威，**本文不复述**。
> 一句话：把 README/PAPER/CHECKPOINTS 里手抄、逐版本漂移的 headline 数字，升级为
> 「单一事实源 + 机器可校」——**散文只转引 claim_id 的 ledger 状态，绝不再抄数字**。

---

## 1. 为什么

红队 TR 溯源矩阵实测：headline 断链几乎全是「aggregate 产物里有权威数字，但散文里的
数字是手抄的、且抄漂了」——`1450≠1000`、`0.0645≠0.0668`、`7.7e-8 / p<1e-4 / 2.5%` 无对应
产物、`S0.demo` 数被冠以 H1。根因是**主张→证据靠人肉转抄、逐版本漂移**。

Claim Ledger 把这条链变成机器可算：每条主张登记一次「预注册意图 + 证据钉点」，编译器
从 artifact 指纹重算出 ClaimDecision，落 `claims/ledger.json`。

## 2. 组成

| 文件 | 角色 | 谁写 |
|---|---|---|
| `claims/claims.yaml` | 主张登记（claim_id / 文本 / 判定函数名 / 证据 glob / 预期方向 / 代际） | 人写 |
| `claims/deviations.yaml` | CHECKPOINTS 压测更正记录的机器可读镜像（偏差登记） | 人写 |
| `scripts/claim_compiler.py` | 编译器：读登记 + report 产物 + campaign_manifest → 重算 | 机器 |
| `claims/ledger.json` | 输出：每主张一条 ClaimDecision（血缘快照） | **机器生成，勿手改** |

证据侧输入：各 `report/` 产物（`headline_stats.json` / `aggregate_summary.json` /
`stats_tests.csv` / `main_table`）+ `campaign_manifest.json`（代际 / cells sha / 代码指纹）。

## 3. 状态集（§9 + 裁决扩展）

`supported` · `rejected` · `partially_supported` · `invalid_probe` · `superseded` · **`stale`**

判定函数按名引用（§9「禁闭包」；见 `claim_compiler.DECISION_FNS`）。`paired_significance_verdict`：
`p≤α` 且方向有利 → supported；`p≤α` 但方向不利 → rejected（预注册被证否）；`p>α` →
partially_supported；p/effect 缺失 → invalid_probe。

### 3.1 stale 判定（防「旧 report 讲旧 claim」）

均由主张显式引用的 deviation 驱动（避免目录级 `supersedes_report` 粗粒度误判）：

- **pending_reaggregation**：引用的 deviation `status: open` 且 `pending_reaggregation: true`
  → 数据带病、待新代际重聚合（如 batch 方向修复后的 Gen-3）。
- **superseded_after_campaign（mtime/sha 机制）**：deviation 声明
  `superseded_after_campaign: <manifest 路径>`；若证据 artifact 的 **mtime 早于该 campaign
  的 `created_at`** → 证据是重跑前旧产物、未刷新 → stale。

## 4. 首次真实编译（四主张）

| claim_id | 状态 | 证据 | 代际 |
|---|---|---|---|
| `contamination_protection.S0demo.os_vs_naive` | **supported** | `headline_stats.json` 精确置换 p=1.9e-6 | Gen-1 · S0.demo |
| `false_optimum_rejection.S0demo.os_vs_naive` | **supported** | `headline_stats.json` 精确置换 p=3.05e-5 | Gen-1 · S0.demo |
| `h1_structural_regret.S2r3pool.os_vs_robust` | **rejected** | `aggregate_summary.json` 池化 +0.01606, p=1e-4（方向相反）+ 偏差 `H1_REJECTED_os_worse` | Gen-2 · S2r3 |
| `batch_detection_attribution.gen12.os` | **stale** | 引用 open+pending 偏差 `batch_direction_diseased`（方向判反、待 Gen-3） | Gen-1/2 带病 |

## 5. CLI 与门禁

```bash
python3 scripts/claim_compiler.py            # 编译并落盘 claims/ledger.json
python3 scripts/claim_compiler.py --check    # 门禁：证据缺失/stale/账本漂移 → exit 1
```

`--check` 三重校验（红队护栏 021 §2）：①每条 supported 主张证据文件存在、②账本记录的
证据 sha 与现算一致、③**盘上 ledger.json 与从 artifact 重算逐字段一致**——任何人手改
ledger.json → 报「账本漂移」非零退出。可挂 `preflight_e2e.sh` / pre-commit 前置门禁。

## 6. 消费纪律（零手改）

- `ledger.json` **机器生成、勿手改**（文件头 `_WARNING` 已注明）；改 `claims.yaml` /
  `deviations.yaml` 后重编译，绝不直接编辑账本——否则它就是第二个会漂移的 CHECKPOINTS。
- 散文（README / PAPER / CHECKPOINTS）**只转引 `claim_id` 的 ledger 状态**，勿再抄数字。
- **待续**：散文引用 vs ledger 状态的 CI 一致性校验（断言散文出现的每个 claim_id 与
  ledger 状态相符），可作 `--check` 的下一轮扩展。
