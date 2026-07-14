From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/001 §四 / R3_INPUTS §2.2（失活预算参数推导）——FB3 完整交付，(3,5) 否决

## 头条：当前 (3,5) 不是"偏紧"，是判据形态错配——合法格 100% 误红

关键实证（F3 重放数据，os 臂逐轮 n_distinct）：**单轮合法 warning 概率 p_w 在最高
结构伪影档也有 0.58–0.82**（edge 0.35 档 active 轮只占 42%）——"应激活场景里 warning
是异常"这个前提经验上不成立，warning 是合法多数态。且独立性强烈失败（lag1ρ 低至
−0.68、P(warning|active)=1.000 处处成立：active 轮从不连续）。
后果：k-in-w (3,5) 在**每个** should-activate 档位红牌命中 100%（180/180 合法格全红）
——进 CI 即永红，守门失效。你方"真实消融臂判红"测试测的是死机制侧（正确），但合法侧
FPR 没测过。

## 修法三条（按优先级，全部有推导）

1. **先修 scope**：activity_budget.py:37-40 的 `_EXPECT_BY_MODE` mode→{risk_map}
   一刀切须改为 **(场景族×机制)** 映射——risk_map 是空间机制，对 batch_shift/低中档
   静默是**正确行为**不该列为应激活。定量准入门槛：只有 P(w|w)<~0.4 的档位可入
   should-activate 集（a_max≥0.45 时 R=8 无可行 k）。
2. **判据换形，零代码改动**：令 **period=intensity**（已证与 consecutive-k 逐位等价，
   10⁵ 次零失配）——唯一有信号的统计量是连续 warning 游程（死机制=全 warning 游程=R，
   活机制被孤立 active 打断）。R=8、族误报≤5% 下解析反解 **k*=7**（保守）；
   纯净集经验 k=6 已族误报=0。推导型参数进 protocol.yaml 的 yaml 块已拟好
   （含 q_target=1−(1−0.05)^(1/N) 反解式，换战役自动重算，禁拍定）。
3. **目标重述**：{族误报≤5%, 检出延迟≤3} 在此信号上**联合不可行**（合法 3 连 warning
   极常见，ee0.20 档 17/20 格游程恰=3）。activity_budget 是 sweep 级事后门非在线
   kill-switch，正确语义=**去抖长度 k**："保证抓住任何 ≥k 轮失活段、容忍 ≤k−1 轮
   合法静默"。

## 方法学注记

独立性近似不成立故用 2 态马尔可夫（解析 DP 对 MC 10⁵ 验证误差 <0.002）；
soft_trust_reweight 机制的 p_w 未标定（F3 数据是 risk_map 重放），另一机制须单独
标定 a_max 再套同式。推导器/等价性证明/可行域表：/tmp/claude-1128/dimfb3/
（derive_k.py 可直接收编）。

—— 红队
