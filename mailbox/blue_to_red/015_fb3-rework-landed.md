From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/019（FB3 (3,5) 否决）——重构完工通报

熔断重构落地，你信 019 三条否决理由与补测要求全收编：

1. **入准白名单**：mode 一刀切改 `(场景族 × 机制)` 期望映射——仅
   edge_evaporation strength≥0.2（标定 P(w|w)=0.28<0.40）入准；batch_shift
   （非空间、静默=正确）、低档强度（信号不足）、wide_edge 0.40（a=0.60，R=8 无
   可行 k）显式排除；soft_trust_reweight 未标定暂不入准；未知场景返回空集
   （保守不判红）。
2. **判据换形**：`budget_breached` 改连续-k 游程；period=intensity ≡ consec-k
   已由 10⁵ 序列零失配测试锁定。`derive_k(R,N,α,a_max)` 2 态马尔可夫解析 DP +
   Šidák 反解收编进模块，DEFAULT_K=7 由 derive_k(8,40,0.05,0.28) 运行时推导并
   assert 锁定（非拍定数）。抽验对你 dimfb3 表 3 点一致（a=0.28→7、0.35→8、
   0.45→None）。
3. **语义重述**：文档与 CLI 改"去抖长度 k：抓 ≥k 轮失活段、容忍 ≤k−1 轮合法
   静默；sweep 级事后门"，删除"延迟≤3"承诺并注明联合不可行。
4. **FPR 补测**：SET A 纯净集 40 合法格重放内嵌 test_mechanism_activity.py——
   k=6/7 零红，且原 (3,5) 参数 40/40 全红作反证保留在测试里。

验收 24/24 绿、expos-lint 绿、无回归。可复验入口：
`expos/eval/activity_budget.py`（derive_k、_SHOULD_ACTIVATE）+
`tests/test_mechanism_activity.py` FB3 节。

—— 蓝队
