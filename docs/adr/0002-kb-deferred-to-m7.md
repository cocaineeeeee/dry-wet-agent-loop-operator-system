# ADR 0002：批量采集 M4 用 UCB+去重、Kriging Believer 延至 M7

- **Status**: Accepted (2026-07-10)
- **Context**: §11.1 推荐 KB；M4 是 naive 对照基线，性能非目标；KB 正统实现需冻结超参条件化（§13.9 走读后才锁定 numpy 配方）。
- **Decision**: M4 用 UCB top-n + 最小距离去重（一次性排名）；KB（select_batch_kb：clone(kernel_)+optimizer=None、全量重分解、伪观测=后验均值）随 M7 TrustAwarePlanner 落地。
- **Consequences**: naive 基线保持简单可比；os 臂获得正统批量策略；两臂批量策略差异需在 M9 报告中声明（生成器字符串已入 provenance）。
