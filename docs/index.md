# expos 文档站

> **expos 是实验测量的可信操作系统**——一个面向安全、非生物、物理材料实验（结晶生长、涂层干燥等）的闭环实验操作层：观测进系统即 `trust = PENDING`，须经 QC 裁决才被路由，让伪影数据在结构上不可能污染响应模型。

## 读者分流

=== "想懂架构"

    先读 [ARCHITECTURE](ARCHITECTURE.md)（权威蓝图：公理/域/schema/各层规格），再看 [PLATFORM_VISION](PLATFORM_VISION.md) 理解换域即换 YAML 的平台定位，最后用 [DEEP_REVIEW](DEEP_REVIEW.md) 检视两条差异化主张与三大威胁。

=== "想跟进度"

    从 [BUILD_PLAN](BUILD_PLAN.md) 看里程碑定义与验收标准，深入各里程碑设计规格：[M5 QC 三级检查](M5_DESIGN.md)、[M6 归因引擎与失败模型](M6_DESIGN.md)、[M9 对比实验协议](M9_PROTOCOL.md)。

=== "想查调研"

    [REFERENCE_MAP](REFERENCE_MAP.md) 汇集外部系统调研（MADSci / Ax / DoWhy / A-Lab 等），是设计取舍的证据库。

=== "想贡献"

    先读 [CLI 设计](CLI_DESIGN.md) 理解命令面与运行产物约定；构建纪律与里程碑台账见仓库根目录 `CHECKPOINTS.md`。

## 与 README 的分工

- **README.md**（仓库根）：电梯陈述、快速上手、仓库结构、里程碑状态总表、设计红线与安全声明——面向第一次接触项目的人。
- **本文档站**（`docs/`）：可检索、分区导航的完整规格与调研库——面向要深入架构、跟进里程碑或做贡献的人。

两者内容不重复：README 给入口与全局速览，文档站给权威细节。
