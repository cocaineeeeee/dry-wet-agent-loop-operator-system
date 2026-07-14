# expos CLI v2 设计规格（平台级命令树）

> 2026-07-10。标杆走读：aiida-core `verdi`（references/aiida-core/src/aiida/cmdline/，
> 命令树组织 + process list/show/report 查询 UX + echo/ExitCode 层）与 MADSci `madsci`
> CLI（references/MADSci/src/madsci_client/.../cli/，CRUD 命令组织 + OutputFormat 统一渲染）。
> 现状输入：scripts/run_loop.py（单命令）、expos/kernel/store.py（runs/ 可查对象）、
> REFERENCE_MAP §13.13（overrides/pending/ 通道 + manifest commit-marker）。

## 0. 定位与原则

- **CLI 是 runs/ 的第四个读者**（loop 写者、UI 读者、测试之外），遵守同一契约：
  只经 RunStore 读方法访问。写动作有二且都受控：① `override` 的 pending 文件投递
  （§13.13 通道，不碰 RunStore）；② `check --fix` 的 events.jsonl 尾损截断——**它取
  `writer.lock`（与 loop 同协议，取不到即拒），绝不与 loop 并发交错写盘**（OS3 §一(c)：
  "override 是唯一 CLI 写通道"已不成立，锁协议随之统一）。其余命令零写副作用。
- 名词-动词两级树（学 verdi `process list/show/report`、madsci `resource list/get`）；
  运行目录作首个位置参数（expos 无 daemon/server，`<run_dir>` 即 verdi 的 `--profile`）。
- 人读输出默认，`--json` 全局机器可读（学 madsci OutputFormat 优先级：--json > 表格）。
- 快启动纪律（verdi/madsci 共识）：重依赖（numpy/pydantic/streamlit）延迟到子命令函数体内
  import，`expos --help` 不触发。

## 1. 框架选型：stdlib argparse 子命令

verdi/madsci 均用 click，但其动机在 expos 不成立：verdi 需要 entry-point 懒加载的插件命令树
与全局 verbosity 注入（自定义 Group 子类）；madsci 需要 rich 渲染与 pydantic-settings 上下文。
expos 命令树小而固定（8 个名词、两层深），argparse `add_subparsers` 足够，且：
(1) 零新增依赖——pyproject 当前只有 numpy/pydantic/pyyaml/scipy，闭环可复现性优先；
(2) argparse usage error 天然 exit 2，与我们的领域错误码同槽；
(3) click 的价值（装饰器组合、did-you-mean）可用 <80 行自写补齐，需要时再换不迟。
布局学 verdi「一名词一文件」：`expos/cli/__init__.py`（root parser + dispatch）+
`expos/cli/cmd_run.py`、`cmd_status.py`、`cmd_inspect.py`…；pyproject 加
`[project.scripts] expos = "expos.cli:main"`。scripts/run_loop.py 保留为兼容 shim
（打印 deprecation 一行后转调 `expos.cli.cmd_run`），一个里程碑后删除。

## 2. 全局约定

**退出码表**（对齐 expos/errors.py 的 `ExposError.user_facing` 语义；比 verdi 的
SUCCESS/CRITICAL/USAGE_ERROR 更严格地把 1 留给 bug）：

| 码 | 含义 | 触发 |
|---|---|---|
| 0 | 成功 | 正常完成（含"查询结果为空"） |
| 2 | 领域/用法错误 | `ExposError(user_facing=True)`（配置错、预算超支、对象不存在）+ argparse 用法错 |
| 1 | 内部 bug | `user_facing=False` 或任何未捕获异常——**不吞 traceback**（"bug 不许静默"红线） |

main() 统一实现：`except ExposError as e: if not e.user_facing: raise;
print(f"error: {e}", file=sys.stderr); return 2`（run_loop.py 现有逻辑上移为全 CLI 契约）。

**`--json`**：根 parser 与各子 parser 共用 parent parser（两个位置都可写）。输出为单个
JSON 对象或 JSON Lines（events 等流式列表），pydantic 对象走 `model_dump(mode="json")`；
`--json` 时人类提示信息一律走 stderr（学 verdi `is_stdout_redirected` 纪律），保证
stdout 可直接 `| jq`。人读表格用 stdlib 手写定宽列（无 tabulate 依赖，列宽由数据计算，
学 verdi workchain report 的做法）。

## 3. 命令规格

### 3.1 expos run —— 闭环运行（收编 run_loop.py）

```
expos run --domain <name|yaml> --rounds N [--mode naive|robust-blind|os]
          [--seed 7] --out runs/<name> [--resume] [--json]
```
参数与现 run_loop 一致；`--mode` 随 M5/M9 扩到三臂（choices 由已注册策略对象生成，
避免 CLI 与 loop 两处真相）。domain 解析规则不变：路径存在用路径，否则 `domains/<name>.yaml`。
输出：进度行走 stderr（每轮一行），结束后 stdout 打 summary（`--json` 时仅 summary JSON）。
退出码：0 完成；2 域配置错/预算不足/resume 参数不符；1 内部异常。

### 3.2 expos status <run_dir> —— 一屏运行态

```
expos status runs/m4_naive [--json]
```
读 checkpoint.json + config.json + 观测存储（trust 计数、best-so-far），示例输出：
```
Run runs/m4_naive        domain=crystal  mode=naive  seed=7
Rounds  4/8 completed    last checkpoint 2026-07-10T08:45:35Z
Budget  wells 188/384 (49.0%)
Trust   TRUSTED 188  SUSPECT 0  FAILED 0  PENDING 0
Best    y=0.8123  (obs_3fa2c1, round 3; TRUSTED 且非对照, §13.7 口径)
Overrides  pending 0 / applied 0
```
`--json` 输出同构字典。退出码：2 目录不存在或缺 checkpoint；0 其余。

### 3.3 expos inspect —— 对象与事件查询（学 verdi process show/report）

```
expos inspect <run_dir> obs <obs_id> [--json]
expos inspect <run_dir> exp <round>  [--json]
expos inspect <run_dir> events [--kind K] [--round N] [--since-seq S] [--json]
```
- `obs`：单观测详情，两列 Property/Value 块（学 verdi get_node_summary）：
  result（value±sd/unit）、trust/confidence、routing、qc 摘要（flags + 每检测器一行）、
  failure_attr、layout(row,col,plate)、cand_id/control_id、created_at；结尾给
  hint 行 `Run 'expos inspect ... events --kind decision' for verdicts`（学 verdi 的
  "报告另开命令"分层）。
- `exp`：按 round_id 找 ExperimentObject（store.list_experiments 按 (round_id, exp_id)
  排序）：status、generator、候选/对照/井数、budget 快照、design provenance。
- `events`：事件日志逐行渲染（学 verdi process report 的对齐列）：
  ```
  seq  ts                    kind               summary
    0  2026-07-10T08:45:23Z  round_designed     round=0 exp_0e7711 sobol n=21 wells=47
    1  2026-07-10T08:45:23Z  status_transition  exp_0e7711 DESIGNED→EXECUTED
  ```
  summary 由 per-kind 格式化函数产出（未知 kind 回退打 payload 键）；`--json` 时输出
  原始 JSON Lines（事件本身已是机器格式，直接透传）。
退出码：2 obs/round 不存在；0 events 空结果（打 "no events" 到 stderr）。

### 3.4 expos verdicts <run_dir> —— 裁决清单

```
expos verdicts runs/x [--trust suspect|failed|trusted|pending] [--round N] [--json]
```
观测按裁决聚合的表格（`--trust` 大小写不敏感映射到 TrustLevel）：
```
obs_id      round  trust    conf  routing            reason
obs_3fa2c1  2      SUSPECT  0.71  QUARANTINE         edge: 配对差 0.031 > 0.011
obs_9b10d7  2      SUSPECT  0.64  REMEASURE          glare: exposure 1.4 count 2
```
reason 取 qc 报告首要触发检测器 + decision 事件中配对的 qc_explanation（refs 关联）。
尾行统计 `2 of 188 observations`。这是 override 的"看货"入口，与 3.5 成对。

### 3.5 expos override —— 人工改判（§13.13 pending 通道的 CLI 端）

```
expos override <run_dir> --obs <obs_id> --trust trusted|suspect|failed
               --reason "..." [--routing R] [--json]
```
**唯一带写副作用的查询类命令**，且**不碰 RunStore**：向 `<run_dir>/overrides/pending/`
原子投递（tmp + os.rename）一个提案文件 `ovr_<ulid>.json`：
```json
{"target_kind": "observation", "target_id": "obs_3fa2c1", "field": "trust",
 "new_value": "TRUSTED", "reason": "哨兵证实为反光误报", "actor": "human",
 "source": "cli", "base_version": 3, "created_at": "..."}
```
`base_version` 由当前观测读出（乐观并发：消费时不符→conflict）。**与 UI 共用同一通道、
同一 schema**（source 字段区分入口）——loop 在轮边界消费 pending→processing（rename 原子
抢占）→applied/rejected/stale/conflict，应用=追加 version+1 事件行，CLI/UI 永不直写对象。
`--reason` 必填（审计不变量：OVERRIDE 是 DecisionRecord，无理由不收）。
输出：pending 文件路径 + 一行说明"将于下一轮边界生效"。退出码：2 obs 不存在/枚举非法。

### 3.6 expos replay <run_dir> --round N —— 事后评分与轨迹导出

```
expos replay runs/x --round 2 [--score] [--out report/trajectory_r2.json] [--json]
```
离线评测器：从事件日志+观测重建该轮轨迹（Olympus Campaign 字段子集，M9 §轨迹格式），
导出到 `<run_dir>/report/`。`--score` 在 M9 后开放：replay 是 `truth/` 的**合法读取者**
（公理 6 禁的是闭环内 qc/models/planner/agent；离线评分器在环外，与 M9 三臂评测共用
评分函数），输出 per-round regret / 裁决混淆矩阵（TRUSTED-but-artifact 等）。
M9 前 `--score` 直接 exit 2 并提示。退出码：2 round 未完成/未实现评分。

### 3.7 expos domains —— 域配置前置校验

```
expos domains list [--json]
expos domains validate <yaml> [--json]
```
`list` 扫 `domains/*.yaml`，表格列 name/adapter/变量数/artifact_scenario；
`validate` 调 `expos.domain.load_domain()`（含 adapter 构造），成功打 `OK <name>` +
设计空间摘要，失败打 DomainError 原文（load_domain 已产生带路径的中文错误信息）。
退出码：0 通过；2 校验失败（DomainError 即 user_facing）。这让"跑 8 轮前 5 秒发现
YAML 手误"成为标准动作，`expos run` 内部走同一函数（单一真相）。

### 3.8 expos ui —— 拉起只读面板

```
expos ui [--runs-root runs/] [--port 8501]
```
`subprocess.run([sys.executable, "-m", "streamlit", "run", "ui/app.py", "--", ...])`，
streamlit 缺失时 exit 2 并提示安装命令（保持核心零 streamlit 依赖）。

## 4. 补全、插件扩展点

- **shell 补全**：不引 argcomplete 常驻依赖；提供 `expos completion bash|zsh` 子命令
  静态生成脚本（子命令名+选项从 parser 内省，学 madsci `completion`）。obs_id 等动态值
  不补全（性价比低）。
- **插件扩展点**（对齐未来 pluggy 插件系统）：root dispatch 预留 hook
  `expos_cli_register(subparsers) -> None`，经 entry-point group `"expos.cli"` 发现
  （学 verdi 的 `aiida.cmdline.data` 懒加载：只在解析失败/--help 时才 import 插件模块）。
  域包（如未来 bio 域）可挂 `expos <domain-noun> ...` 名词，禁止覆盖内置名词。

## 5. 分阶段落地

| 阶段 | 内容 | 依据 |
|---|---|---|
| **P1（M5-M7 期间就做，回报最大）** | cli 骨架 + `run`（收编 run_loop）+ `status` + `inspect events` + `verdicts` + `domains list/validate` + `--json` + 退出码 | M5 起调试 QC 裁决全靠翻 events.jsonl，verdicts/inspect 是每天要用的调试器 |
| **P2（M9）** | `replay`（轨迹导出先行，`--score` 接 M9 评分函数）+ `inspect obs/exp` 补全 | 三臂评测需要批量导轨迹 |
| **P3（M10）** | `override`（依赖 loop 侧 pending 消费端与 manifest commit-marker 落地）+ `ui` + `completion` | §13.13 通道是 M10 交付物，CLI 写端与 UI 写端同 PR 接线 |
| **P4（post-v1）** | 插件 entry-point、did-you-mean、`--project` 列投影 | 有第二个域包再做 |

验收红线（每阶段同 PR 附测试）：CLI 模块 import 不触发 numpy/streamlit；除 override
外无任何 runs/ 写路径（守门测试 grep 写 API）；`--json` 输出对每命令有 schema 快照测试；
exit 1 路径保留完整 traceback（capsys 断言）。
