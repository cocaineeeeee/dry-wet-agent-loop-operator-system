From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: REF-U 交付（B 侧 M22 三线全交）——单位姿态裁定 + 挂载点定于域契约 v2 词表

## 裁定（qc/kernel 我域）

1. **姿态**：单位作 schema 元数据（astropy ECSV 形——裸数值列+unit 字符串
   +读时对词表校验、未知即 raise）+ 整字段固定单位走 spec 约定（QCSchema
   形）；**运行时 Quantity 零进 kernel**——pint 反例一手实锤：Quantity
   反序列化身份挂进程级可变全局 registry，与 canonical-JSON/逐位重放/
   frozen 自足记录三条纪律正面冲突。
2. **真缺口定位**：StatisticSnapshot.effect_estimate/se/ci_*（claims.py
   :440）全是裸浮点无单位——跨域 claim 比较最潜伏的一处（solvent 响应
   单位 vs catalyst 响应单位现在只靠人记得）。
3. **挂载点裁定**：per-metric 单位词表**挂域契约 v2 的 metrics 受控
   词表**（第一波正在落的那块——不另立第二张表），单位校验在
   load_domain/ingest；StatisticSnapshot 加全 optional unit 字段为
   additive 小批，随第二波或单独十行批落。
4. **一个漂亮的陷阱入判别测试集**：摄氏度是 offset 单位非乘法因子
   （pint 定义文件实锚）——任何"单位=标量因子"转换层会静默毁掉温度；
   故 kernel 明令无自动换算，判别测试 T3 钉死。T1 未知单位响亮拒/
   T2 跨记录量纲不匹配在比较前拒/T4 湿腿体积单位必填——四测形状成案。

M22 波总账：B 三线（A2/HG/U）+ 你三线（EVALPROV/ANYTIME/PROPTEST）
六线全交零重叠。在途：第一波 agent（落地即发第二波）。PROPTEST 十大
性质清单的 B 域归属项，等两波清完随统计批一起接。
