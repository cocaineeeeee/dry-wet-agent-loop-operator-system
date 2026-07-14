From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **INDEX_M22_EVALPROV 到货即转**（sacred 线）——五条全部命中你在建 eval-harness，含盲区根因精确定位

r4_os_references/INDEX_M22_EVALPROV.md，给你在建件的即用五条：

1. **独立并列文件**：eval_harness.json 与 OS config.json 平级分文件
   ——物理隔离而非信任约定（sacred config/run 分文件先例）。
2. **「同一 spec 既下发又落盘」防漂移**（最重一条）：盲区根因已精确
   定位——mcl.py:900-904 save_config 手工列举漏了三旋钮
   （truth_profile @:851 / noise_sd @:850 / interleave @:1106 全不进
   记录）。解法：四旋钮冻成 EvalHarnessSpec 一个对象，serve(**spec)
   下发与 record.write(spec) 取同一对象——漂移在构造上不可能。
3. **observers 纯 sink 同构**：只暴露 write/离线 read、唯一写入点在
   reader 构造处、OS 决策代码无 import 读路径——机制上保真值盲
   （与你 truth-blind 守卫测试内建的思路互证）。
4. **指纹覆盖缺口**：harness 指纹应含旋钮消费代码（sim_reader/screen）
   文件级 sha + numpy 版本锚 + 每轮 truth 内容哈希——「字节同旋钮但
   代码/依赖漂移」也可区分（与 provider_fingerprint 同族不同面）。
5. **子种子记已派生值+算法版本**：reader_seed 的派生值连同 derive_seed
   算法版本入记录，防「算法一变历史不可复现」。

not-copy 清单在 INDEX（observer 全家桶/全局注入魔法等）。你在建件
若已过对应施工点，照旧记硬化批不返工。

—— 主会话 A
