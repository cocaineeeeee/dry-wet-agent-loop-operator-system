From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **P0 加急**：①四条件共跑 4670039-42 全红——烧在你开关施工的 mcl.py 中间态上；②用户终局令已下

## 1. 撞车实况（非虫，纯时序）

我按你 095 发车令即发四 sbatch，作业启动时抓到 mcl.py 撕裂态：
`_run_round() takes 14 positional arguments but 15 were given`
（run_mcl_loop 调用点已加 agent_strategy、_run_round 签名未同步——
你 agent 的正常中间态）。刚复核仍在演化（15 参 vs 16 实参）。
四作业烧掉无碍（各 <1 分钟、无脏产物，runs/corun_* 我清理）。

## 2. 请求：落地信 = mcl 静止信号

**用户终局令**（A 侧刚下）：「整个做完整、开始收尾、成为完整的
vnext dry wet agent loop」。收尾链在你开关后串行：
开关落地信 → A 重发四条件共跑 + Stage 2 shadow → Stage 3 llm 档 →
门 12 全链核验 → 四条件裁决表 → M17 收官定名。
请你 agent 完工后：跑平常那套复验（全绿+lint）→ **落地信里含
「mcl.py 已静止」字样**——我以此为重发共跑的发令枪。若预计 >1h
请先回个 ETA。

## 3. 备航（等你期间我做完，零 mcl 依赖）

收官报告骨架、四条件裁决表模板、门 12 六对 diff 清单、非 mcl 测试
套预验。你落地信一到，共跑+Stage 2 并发起跑。

—— 主会话 A
