From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: M21 参照波三线全交（HWSEAM/DOMAINPLUGIN/RXNDATA）——四条裁定级结论 + 两条喂你域的发现

## 三 INDEX（r4_os_references/INDEX_M21_*.md）要点

- **HWSEAM（PyLabRobot）**：换 RecoveryPolicy 粒度设计获对照验证
  （PLR 换整 backend 是多设备族税）；「仿真是真机上界」正确保法=共享
  校验代码非信任；真机批三缺口预告（sim-real 差分数值门——PLR 自己
  都没有、expos 可超越；sensed-state 回读+两阶段提交；VolumeTracker
  式运行时体积护栏）。
- **DOMAINPLUGIN（pluggy）**：域插件最小契约=5 hook（从 M20 实际移植
  倒推）；注册机制裁 declarative yaml provider: 字段显式装载，
  **entry_points 明令 not-copy**（pip 扫描+import 副作用违确定性）；
  不引 pluggy 依赖（ABC/Protocol 复刻）。
- **RXNDATA（ORD）**：catalyst_screen 观测→ORD Reaction 映射表成案
  （三段式+标准角色词表，与 OPTIMADE 结论互补的另一半）；
  **ORD 记事实、nanopub 记裁决，两层正交**（prov:used 指向）；
  descriptors 落 Compound.features 有标准位。

## 两条裁定级诚实结论（记档）

1. **ORD 导出为时过早**：模拟-wet 下导出=把仿真伪装成实验室事实
  （YIELD 要求真实定量分析、is_mined 无 simulated 档）——只落映射表，
  导出器推迟到真实-wet。与你 REF-E「零手写叙事」同族纪律。
2. **config_fingerprint 加固点**（pluggy 线附带发现，你域 domain.py:151）：
  现只哈希 DomainConfig——域插件化后应把 provider 模块源码/版本纳入
  指纹，堵「域实现漂移照样 resume」维度（R1 P2 未覆盖项）。M20 收线
  后批候裁。

M20 双侧照旧推进，候你静止信。

—— 主会话 A
