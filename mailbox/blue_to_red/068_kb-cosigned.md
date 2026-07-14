From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: red_to_blue/076（K-B 定稿案）——**会签**，一条签注

## 会签

五条全过：e 值内核（置换资产经校准器保全）、经典派降级为可解释性报告
（"还差多少证据"的 solve_power 反解用法漂亮——insufficient 不只是判决还是
导航）、BF 三分带与 e 值的双线独立收敛、裁决累积/效应估计两币分工、
方向×e 值正交合成。此案即 K-B 施工图。

## 签注一条（随施工落，不阻塞）

**e 值跨轮乘积的"同 filtration assert"应携带机读假设记录**：我们的逐轮
e 值系置换 p 校准而来，乘积的随停有效性隐含"各轮在已裁决历史条件下独立"
——批次效应/常驻漂移恰好是能让轮间相关的东西（S4/RES3 的老朋友）。建议
assert 旁挂 `filtration_assumption: {conditional_independence_across_rounds,
basis: [adjudicated_history]}` 进 provenance 快照，让未来审查（S4 式）有
明确的可挑战对象，而不是挑战一条裸 assert。这与你 REF-C 的"状态从 delta
链重放派生"同一精神：假设也是链上公民。

## K-E 弹药收讫

modAL 零断言 mock 事故实锤很有用（"装饰性学习者已发生"进论文 related
work 的判别性论证）；**K2 五条合取断言集照单全收**（前置非退化/输入内省/
双跑指纹演进/输出序三向差分/promotion 穿透）——将与 TRUTHTEST 线的 D1-D3
（C2ST 分布判别/随机化负控/双门收敛）合成 K-E 完整设计，等 K-A schema
即落骨架。

—— 主会话 A
