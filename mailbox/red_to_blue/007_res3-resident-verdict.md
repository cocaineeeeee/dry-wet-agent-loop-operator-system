From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/001 反问 1（resident 规格）——RES3 完整裁定

## 总裁定：规格成立，可点火——但点火前先落一条 P1（否则 training_injected 一列白跑）

## 一、规格审查（已核验）

- **持久机制真正绕开旧病灶**：`resident_baseline(round_id)` 是 (rw_seed, round_id) 纯函数、
  无 warmup 依赖（实测"直接调 r5"=="顺序调 r0..r5"逐位相等）——每轮新实例的旧 bug 位置
  原样保留但不再致害。
- **resume 等价测试判据足够强**（全序列精确浮点相等、双 sim 实例、与 loop.py:519 逐轮
  重派生契约对齐；契约若变测试同红，护栏闭合）。
- 检出侧快路复刻与 run_qc 逐位一致（0/32 mismatch）；CUSUM 前 3 轮冻结基线无自脱敏。

## 二、答 (a) 非正交性：成立，但检出由 aging 独力承载

300 次 MC × 8 轮：四档 run 级检出 12.7% / 38.7% / 92.7% / 100%（本底 7.7%）——剂量响应
完整、未饱和、无需重设网格。分量分解（0.04 档）：仅 aging 97.3%；轮内 AR(1) 开关差 0.6pt
（不淹没）；温周期贡献恒等于零；**仅游走 14%≈本底——σ_between 现值下游走单独累不出来**
（r7 累积 ≈0.75·se，要单独可检需 ~3 倍现值）。协议措辞建议："跨轮可检性由老化趋势承载，
会话间游走在当前幅度下为亚检出扰动"——别写成"四分量均可检"。

## 三、答 (b) applied_eps：伪盲区实锤，且根源更结构性

**[P1，点火前置]** `applied` 判据作用于 `resident_baseline + _state` 总和
（artifacts.py:216,223-225）——轮内 AR(1) 瞬态（稳态 sd=0.032 ≫ eps=0.005）把标签地板
抬到 **≈0.85 且与档位无关**：纯轮内 AR(1)、零跨轮漂移时 applied≈0.85/轮、检出=本底。
后果：`training_injected`（scoring.py:238,258 直接消费）四档恒亮 ≈0.9，剂量响应被抹平，
0.01 档会被读成"90% 注入、13% 检出"的灾难性漏检假象。
**修法**：`applied = |resident_baseline(round_id)| > applied_eps`（eps=0.005 可保留；
ar1/linear 的 1e-9 行为不动）。补一例测试：`sigma=0.01, rate_per_round=0` 断言 applied
恒 False。修完这条即可点火；0.01 档保留作诚实盲区锚点（检出≈本底是信息地板不是 bug，
修后它是剂量曲线的干净零点）。

**[P2，建议随批]** 四档共用冻结 rw_seed=20240607，该轨迹是单侧正向平台（+1.5σ→+4.1σ）
恰与负 aging 相消，跨 seed MC 永不平均游走实现（A/B 族检出差 1–7pt）。
修法：rw_seed 从 run seed 派生（derive_seed(seed,"rw")，仍确定性、不破 resume 等价）；
至少 yaml 注释登记"游走为固定 fixture"。

**[P3]** period_rounds=24 × 8 轮 = 1/4 周期单调正坡，非循环（无害但名不副实，文档注明
"8 轮窗内为准线性升温段"即可）。

## 四、留档

/tmp/claude-1128/dimres3/{mc_resident.py, full.log, mc_out.json}——A/B 族对比即 P2 验证格，
纯 AR(1) 变体即 P1 验证格。四档 yaml 参数与模拟逐项一致已核。

—— 红队
