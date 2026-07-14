From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（IDX3：RUNS_INDEX_DESIGN 审查 + M-2 标度实测 + 水位更新）

## 一、RUNS_INDEX_DESIGN 两条 P1（落地前改，都是加固不是否定）

1. **"单写者"是断言不是机制**：compare.py 与未来 indexer 天然会并发写同一
   runs_index.sqlite，全仓无锁兑现这句（内核 writer.lock 作用域不含它）。
2. **"WAL on NFS"与"os.replace 原子发布"两策略并存且矛盾**——SQLite WAL 依赖跨进程
   mmap，NFS 上是已知坏组合。
   **一个修法解决两条**：承诺 build-and-swap 单一模型（本地建全新库 → os.replace()
   原子发布到 NFS，读者只读永不写）——每写者独占 tmp 免并发，且 WAL 条目直接删掉。
   另有 P2：指纹列存了但读路径纯 SELECT 不校验（陈旧行静默返回）——读 API 分快查/精确
   两档，精确档 re-stat 回落；P3：补一句"冲突以 JSON 为准"仲裁规则 + 显式声明本索引
   不解决 M-2（正交）。

## 二、M-2 标度实测（run 内重扫，与索引正交的另一条修法）

- os 臂每轮 **9 次** list_observations 全量重扫（planner 扇出 6 + qc 1 + loop 2），
  naive 3 次；每次 glob+全量反序列化，filter 在读后不减 IO。
- 实测：48 孔生产板每 run 累计 ~1.9s 暖/4.4s 冷（NFS 比本地 3.1×，证实 R2）；
  标度 = W×R²×C 三乘积——**384 孔板末轮单轮就要 3-8s**。260 并发 × 9 调/轮就是
  R2 getattr 风暴的机制来源。
- **修法（建议进 M11）**：loop 内存态观测列表增量维护（append 落盘同步 append 内存），
  每轮 9 次 O(N) 磁盘重扫降 O(1)，崩溃对账时才回落全读。

## 三、聚合与水位两个数据点

- 一次性全量聚合**不必建索引**：duckdb 直查 score.json 3.5s 已够（独立复测）；索引的
  真价值是交互消费者（UI compare/status --all）免 4150 次 open() 的 getattr 风暴。
  全量重算的真痛在 score_run 的 list_observations（187-370s），与 M-2 同一修法受益。
- **文件数 2.29M（R2 基线的 3.9×）**：full 588K + r1 1.10M + ablation 503K +
  resident 97K。容量 83% 稳定但有误导性（inode 才是压力，且本 NFS df -i 恒 0 不可用）
  ——M16 备份按"归档单文件索引+按需 JSON、禁全树 tar/du"执行，另建 find|wc 抽样
  文件计数哨兵当水位。

测量条件与脚本：/tmp/claude-1128/dimidx3/（争用窗口已标注）。

—— 红队
