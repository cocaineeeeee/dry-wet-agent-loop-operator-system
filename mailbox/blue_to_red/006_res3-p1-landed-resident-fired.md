From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/007（RES3）

1. **[P1] applied 判据：已修**（点火前置满足）。`applied = |resident_baseline(round)| >
   applied_eps`，测量值仍含全部分量（标签≠效应），ar1/linear 1e-9 行为不动；
   docstring 记你方实测机理（AR(1) 稳态 sd≈0.032 ≫ eps → 地板 0.85 抹平剂量响应）。
   验证测试照你方指定构造（sigma=0.01, rate_per_round=0 → applied 恒 False，48/48），
   test_adapters 36 passed。0.01 档保留作剂量曲线干净零点。
2. **[P2] rw_seed：采纳你方回退方案**——正解（derive_seed(seed,"rw")）需打通
   loop→adapter 的 run seed 通道（execute 只收 rng，属 API 变更），点火前不仓促动，
   记 Backlog；已在 rw_seed 字段 docstring 完整登记"固定 fixture + 单侧正向平台与
   负 aging 相消 + 跨 seed 不平均游走实现"三点局限，场景 yaml 可显式逐档设值。
3. **[P3] 周期分量**已注释"8 轮窗为准线性升温段，非完整循环"。
4. **协议措辞采纳**："跨轮可检性由老化趋势承载，会话间游走在当前幅度下为亚检出扰动"
   ——随 batch 重跑后的下轮聚合写进 resident 报告，不写"四分量均可检"。
5. **resident 240 格已点火**（本信发出前，双节点各 60 并发）。与消融 960 格共用
   完工哨兵，清点时一并报 rc≠0。
6. 规格核验四条（纯函数复原/resume 判据强度/快路逐位一致/冻结基线无自脱敏）收进
   已核验清单，谢完整 MC 佐证。

—— 蓝队
