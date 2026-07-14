# RUNS_INDEX_DESIGN —— 大 run 目录索引层设计 v1

> 状态：**设计稿（post-M10 backlog）**。定向研究④【signac-flow + Tiled → 大 run 目录索引与浏览】交付。
> 权威蓝图以 docs/ARCHITECTURE.md 为准。落地档位 `[POST]`：M0–M10 已关账，本设计与 M12（duckdb 报告平面，REFERENCE_MAP §21.2④）同批 post-M10 推进。
> 一句话：`runs/` 已达 full_sweep 1450 + r1_resweep 2700 (+ ablation ~1200) ≈ **5000+ 格、~110 万小 JSON**，NFS 上目录遍历（`du`/`find`）已成瓶颈。补一层**可全量重建的缓存索引**，JSON 仍是唯一真相。

---

## 1. 动机：实测（本机 NFS，现有 4150 格；scratchpad/bench_runs_index.py）

| 操作 | 耗时 | 判读 |
|---|---|---|
| `du -s runs/r1_resweep` | **258s** | 复现"du 超时"——遍历百万文件不可用 |
| `find … -name score.json`（2700） | 23.6s | 逐目录 stat，UI 扫目录之痛 |
| `ls runs/r1_resweep/runs` | 0.10s | 仅列目录名，够快 |
| duckdb `read_json_auto` 直查 2700 | 1.85s→1.09s | cold→warm，取 10 列 |
| duckdb 直查 BOTH 4150 | 3.52s→2.19s | 一条 SQL |
| duckdb GROUP BY arm 聚合 | 0.71s | |

**结论**：族6 结论在 4150 格仍成立——**duckdb 直查免建仓，ad-hoc 全量分析 2–3.5s 够用**。真正的痛不是"读一遍"，而是 `du`/`find` 级**目录遍历（23–258s）**，以及**交互型消费者**（UI compare 反复刷、CLI `status --all` 常跑）每次都开数千 NFS 文件句柄。→ 需一层常驻索引：<10ms 应答、filter/sort/paginate 不碰文件、彻底绕开目录遍历。**index 与 duckdb 分工并存，非二选一。**

## 2. 两个真相源背书（源码级核实）

- **signac**（`references/signac/signac/project.py`）：每 job `signac_statepoint.json`=真相；中央 `.signac/statepoint_cache.json.gz` 纯加速。`update_cache()`：扫 → 与旧 cache 的 id 集合 **diff → 仅变化才重写**；写 `fn+"~"` 再 `os.replace()` **原子替换**；缺文件 ENOENT 静默、可全量重建。
- **Tiled**（`references/tiled/tiled/catalog/orm.py`）：sqlite catalog `nodes(key, metadata JSON, structure_family, time_created, time_updated)` + `assets(data_uri, mimetype)`——**DB 存元数据+文件指针，字节按需读**；`time_updated` 作陈旧判据；查询/分页/全文全走 sqlite。

我们的 `cell_id = scenario__arm__seed`（确定性命名）= 穷人版 statepoint，缺的正是 signac 的**中央缓存**与 Tiled 的**查询面**。

## 3. Schema：`runs/<sweep>/runs_index.sqlite`（每 sweep 一库，或全局一库带 sweep 列）

```sql
CREATE TABLE cells (
  cell_id        TEXT PRIMARY KEY,   -- scenario__arm__seed（确定性身份，= statepoint）
  sweep          TEXT NOT NULL,      -- full_sweep / r1_resweep / ablation_*
  -- 展开的 statepoint 列（来自 cells.tsv + checkpoint.json）
  scenario_id    TEXT, arm TEXT, seed INTEGER, seed_set TEXT, domain TEXT,
  -- 生命周期
  status         TEXT,               -- pending/running/done/failed（派生自 checkpoint+score 存在性）
  completed_rounds INTEGER, rounds_total INTEGER,
  -- 关键指标列（来自 report/score.json 顶层；曲线/rounds[] 不进索引，用时读文件）
  final_regret   REAL, wrong_optimum_hit_any INTEGER,
  training_contamination REAL, training_injected REAL, f_star REAL,
  -- 文件指针 + 陈旧判据（Tiled 式）
  run_dir        TEXT NOT NULL,      -- 绝对路径，consumer 从此再开细粒度文件
  score_mtime_ns INTEGER, score_size INTEGER,      -- score.json 的 (mtime_ns,size)
  ckpt_mtime_ns  INTEGER, ckpt_size INTEGER,       -- checkpoint.json 的 (mtime_ns,size)
  indexed_at     TEXT
);
CREATE INDEX ix_cells_arm     ON cells(sweep, arm);
CREATE INDEX ix_cells_scen    ON cells(sweep, scenario_id, seed_set);
CREATE INDEX ix_cells_status  ON cells(sweep, status);
```

选 sqlite 而非 parquet：**UI/CLI 只读随机查 + filter/sort/paginate/GROUP BY 全在库内**（sqlite 强项），单文件便于 `os.replace()` 原子发布（§3.5）。需要列式全量分析时仍用 duckdb 直查 JSON（§1），二者不冲突。（注：本设计发布物是**全量 rebuild 的新库**而非对旧库原地 upsert，见 §3.5——sqlite 的原地改能力在此不使用，选它是为读侧随机查与单文件发布。）

## 3.5 并发模型：**build-and-swap 单一模型**（IDX3 P1，落地前定死）

> IDX3 审查两条 P1 合并为一个修法：①"单写者"是断言不是机制（compare.py 与未来 indexer 天然并发写同一 `runs_index.sqlite`，全仓无锁兑现此句——内核 `writer.lock` 作用域不含它）；②"WAL on NFS" 与 "os.replace 原子发布" 两策略并存且矛盾（SQLite WAL 依赖跨进程 mmap，NFS 上是已知坏组合）。

**定死为 build-and-swap，无原地写模型**：

1. 每个写者在**本地盘私有临时目录**（`$TMPDIR/idx_<pid>_<uuid>/runs_index.sqlite`）从零建一个**全新** sqlite 库——不 attach、不打开 NFS 上的现有库、不做原地 upsert。
2. 建完 `os.replace()` **原子发布**到 NFS 目标路径。同目标多写者并发时，最后一个 replace 胜出（各自的私有 tmp 互不干扰，绝不交错半库）。
3. **读者只读、永不写**：以 `mode=ro`（`file:...?mode=ro` URI）打开，永不触发 WAL/SHM 边车。

由此：
- **"单写者"从断言升级为机制**：每写者独占其私有 tmp，物理上并发写同一文件不可能——无需锁兑现，`compare.py` 与 `indexer` 可安全并发（各自 build 各自 swap，胜出者即最新全量快照）。
- **WAL 条目直接删除**（§6 同步删）：build 阶段在本地盘可用默认 rollback journal（或 `PRAGMA journal_mode=MEMORY`，反正建完即 replace）；发布后的 NFS 库只被 `mode=ro` 打开，NFS 上永不出现 `-wal`/`-shm` 边车。彻底绕开 "WAL on NFS" 坏组合。
- 代价：每次是全量 rebuild 而非增量 upsert。§1 实测 duckdb 全量直查 4150 格 3.5s，故 build 一个全量 sqlite 同量级（秒级），完全可接受；§4 的"指纹 diff 跳过未变格"仍用于**免读 score/checkpoint**（少 IO），只是产物落到私有 tmp 再 swap，而非原地改 NFS 库。

## 4. 全量 rebuild 纪律（signac 式指纹跳读，build-and-swap 产物）

- **触发指纹 = `(score.json, checkpoint.json)` 的 `(mtime_ns, size)`**（复用 ui/_common.py 既有 `_cache_token` 纪律：纳秒 mtime + size，避开秒级 mtime 同秒漏更，STRESS_TEST_R1 P2-E）。
- `rebuild(sweep)`：在私有 tmp 建新库；`ls`（0.10s，不 `du`/`find -type f`）取 cell 目录名 → 对每格比对**上一份已发布库**（`mode=ro` 打开读旧指纹）：**未变则从旧库拷行（免读 score/checkpoint）、变了才读文件**、磁盘新增则读入、磁盘已删则不拷入。建完 `os.replace()` 发布。
- **幂等**：同一磁盘状态重跑 rebuild 产出逐位等价的快照；崩溃安全靠 build-and-swap 本身（tmp 建到一半崩溃 → 目标库不受影响，旧快照仍完整；无半更状态、无需 sqlite 事务兜底 NFS）。
- **只扫目录名不 `du`**：每 cell 只 `stat` 两个文件（score+checkpoint），2700 格 ≈ 一次 find 的量级（~20s cold），远优于 `du`。指纹命中的格连 `stat` 都省到只读旧库行。

## 5. 谁写、谁读、与真相源关系

- **写（build-and-swap，§3.5）**：聚合器 `compare.py` 收尾时**副产物** rebuild+swap 一份 index（它本就逐格读 score.json）；无聚合任务时由独立 `expos.eval.indexer`（薄 CLI，可选依赖 sqlite——stdlib 自带）补建。二者同一 `rebuild(sweep)` 函数，各自建私有 tmp、各自 `os.replace()` 发布——**并发安全靠模型而非锁**（不再依赖"单写者"断言）。
- **读（两档 API，IDX3 P2）**：指纹列（`score_mtime_ns/score_size/ckpt_mtime_ns/ckpt_size`）存了就**必须有校验档**，否则陈旧行静默返回（读到已被覆盖的旧指标）。故读 API 显式分两档：
  - **快查档 `query(...)`（纯 SELECT，默认）**：filter/sort/paginate/GROUP BY 全走 sqlite，<10ms 应答，**不碰文件**。适配交互消费者反复刷（UI compare、`status --all`）——接受"可能读到 rebuild 之后新落盘、尚未再 swap 的陈旧行"，因为交互浏览容忍秒级滞后。
  - **精确档 `query(..., verify=True)`**：对**命中的行**逐行 `re-stat` 其 `run_dir/{score,checkpoint}.json`，与行内存的 `(mtime_ns,size)` 比对；**不符 = 陈旧行**，回落读该格 JSON 现算并（可选）触发该格 rebuild。只对命中行 re-stat（非全表），故仍远优于 `find`。用于"数字要对"的场景（导出、判定 done/需重算）。
- **读者只读**（§3.5）：`mode=ro` 打开，永不写、永不触发 WAL 边车。
- **与真相源关系**：**index = 缓存，JSON = 真相**（signac/tiled 同款）。index 可 `rm` 后全量重建；任何数字以 JSON 为准；快查档陈旧时精确档 re-stat 回落。索引**从不写回** score/checkpoint，不进内核热路径。

## 6. NFS 注意事项

- **禁 `du`、禁 `find -type f` 全遍历**；只 `ls` 目录名 + 定点 `stat` 两文件（精确档另对命中行 re-stat）。
- **build 在本地盘私有 tmp，发布用 `os.replace()`**（§3.5）：NFS 上只存最终单文件库，只被 `mode=ro` 打开——**NFS 上永不出现 `-wal`/`-shm` 边车**（SQLite WAL 依赖跨进程 mmap，NFS 已知坏组合，本设计从模型上规避）。build 阶段本地盘可用默认 rollback journal 或 `journal_mode=MEMORY`。
- 备份/归档：优先归档 `runs_index.sqlite`（单文件）+ 按需的 JSON，避免 `tar` 逐个 stat 百万小文件。

## 7. 冲突仲裁与划界

- **冲突以 JSON 为准（IDX3 P3 仲裁规则）**：index 与 score/checkpoint JSON 不一致时（指纹不符、并发 swap 竞态读到旧快照、rebuild 滞后），**一律以 JSON 为唯一真相**——精确档 re-stat 检出后回落读 JSON 现算，快查档标注"可能陈旧、以 JSON 为准"。index 任何数字都不得作为对外结论的终审来源；导出/判定走精确档或直读 JSON。
- **本索引不解决 M-2 run 内重扫（正交问题）**：M-2 是**单个 run 目录内**每轮多次 `store.list_observations()` 全量重扫 observations/ 的热径放大（planner 扇出+qc+loop），修法是 loop 内存态观测缓存增量维护（见 `expos/kernel/store.py` `cache_observations`、`expos/loop.py`）——**与本索引层（跨 run 的 `runs/` 目录浏览/聚合）完全正交**：一个治 run *内*的观测重扫、一个治 run *间*的目录遍历，互不覆盖、不互为前置。本设计显式不承担 M-2。
- **非目标（划界）**：不建 parquet 数据仓库（§1 实测证明过度工程）；不进内核依赖（仅 eval/UI 可选）；不索引 `rounds[]`/observations/experiments 明细（用时按 `run_dir` 读文件或 duckdb 直查）；不碰 truth/ sidecar（公理6 隔离）。
