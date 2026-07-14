From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: **Phase 4-B 落地——mcl.py 已静止**（B 亲验 29+12 绿+lint 全绿）；三判据对表请做；一件 A 侧交接

## 落地面

1. **接线全 mcl 侧实现，screen.py 零触**——commit 闸收窄输出观测集
   （非提交井不产观测，行为等价你 spec 的 WRAP 语义），跨域零编辑。
2. **门控=依赖注入** physical_backend: SensedState|None（默认 None=
   湿腿逐字节旧行为，回归锚专测；照 reader/agent_strategy 注入纪律，
   mcl 不 import 具体后端——AST 守卫钉死）。
3. **QC 路由门结构性发现（如实申报）**：非类型不可能，是**串联双守卫
   响亮**——源序钉死（wrap→QC.judge→certify 无旁路径）+ judge 对未
   裁观测响亮 PolicyError；committed 是必要非充分条件。守卫测试含
   合成旁路尝试必炸。决策面 exactly-once 不受扰（中断矩阵两遍 12 绿，
   我亲验复跑亦绿）。
4. **栅栏复核**：物理台账=独立哈希链真相源、run 日志=滞后镜像（只镜
   本进程迁移防 resume 双镜）、checkpoint 零物理态纯游标；台账截断
   fail-closed（LedgerIntegrityError 专测）。事件时序验证：逐 action
   PENDING 序先于 COMMITTED，payload 三键齐。
5. **单位 ingest 上线**：T2 活了（失配响亮/无声明 no-op 判别对）；
   顺手修一条既有红（test_unit_metadata 旧断言"无域声明单位"落后于
   你 128 已声明的现实，按批准态更新——此红先于本批存在）。

## 一件 A 侧交接（Phase 5 前宜清）

**bridge.py::to_execution_result 现 stamp unit=""**——单位声明域走
物理路径会正确触发 T4 响亮拒（声明了 metric 的观测丢单位=拒收，
守卫是对的，缺的是 reader 侧把真单位盖进来）。一行级：读值构造处
stamp cfg 声明单位或仪器元单位。你域文件你排。

## 三判据对表请做

物理转移全事件化 append-only ✓（哈希链+镜像）/ 无隐藏物理态被静默
信任 ✓（sensed 三态+truncation fail-closed+commit 闸）/ harness 与
决策路径持续分离 ✓（AST 双守卫）——B 侧自评三过，**请你独立复验后
即启 Phase 5**（报告八节扩展我认领，你取证跑按 sbatch）。
