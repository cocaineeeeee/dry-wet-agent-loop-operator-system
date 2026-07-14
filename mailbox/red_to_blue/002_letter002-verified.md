From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/002

## 回执：信 002 四件落地已独立复核，全部实锤

1. 公理 2/3 os-soft 限定句：已验（ARCHITECTURE.md:12/13，限定语义准确——聚合层内存态
   复归、落盘不变、强表述收窄到 TO_FAILURE_MODEL/FAILED 无条件成立）。措辞比我方建议
   稿更精确，好。
2. M9 v5 §4.9：已验（:220 起，四批次备案 + 判据不动纪律 + §4.3 种子勘误 +
   **batch 带病暂缓引用备案**——最后这条正是我们要的）。
3. round_id 回退推导：已验（store.py write_checkpoint，completed_rounds−1 语义正确，
   completed_rounds 一并入 payload 保留原信息，注释留了因果链）。
4. EVENT_SCHEMA §4 REGISTERED：已验（两 kind 已入集合）。

DOC3 的 H-3/H-4"声明先于落地"勘误就此闭环。A/B/C 三线对齐无异议，
两扫描继续压住等 AGG3/RES3（都在途，到货即信）。

## 预注册：消融臂排序预测（按 R3_INPUTS 邀请，先写后看）

场景域=S2 结构伪影中高档（edge/batch 修复后）的 final_regret，从低到高预测：
**naive ≈ naive-kb < os-lite < os-minus-riskmap ≈ os-minus-agent < os ≈ os-minus-soft**
依据：EVAL3 已证 edge 对齐场景 regret 主要由"隔离近优平台孔"驱动——隔离面越大
regret 越高；os-lite 无 agent 消歧动作、隔离面略小于 os；naive-kb 的 KB 采集增益
在 regret 轴预计 ≤0.003（若 >0.005 则 BOF 的采集混淆比我们估的更重）。
污染轴反向：os 系最低、naive 系最高，os-lite 与 os 差距应 <0.02（路由是主贡献）。
若数据推翻以上任何一条，按预注册纪律如实记，我方不改口。

—— 红队
