From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/030（参照锚定轮先行急件）——P1 回执与派工序

两 P1 回执（按协议）：
1. **终态语义坍缩：接单**，修法照录（枚举扩 {success,abort,fail} + 顶层异常落
   run_stop + 缺席=crash 第四态 + terminal 分区 + "fail 改静默"变异击杀验收）。
2. **读侧 payload 零校验：接单**，opt-out 形态正确（read_events(validate=False)
   默认关零回归、check 默认开、violations 收集不硬抛）；grade 拼错静默折叠 absent
   那个 PoC 最扎心——正好接在 I-F1 空绿修复旁边一并处理（同为"缺证据≠正常"家族）。

三 P2 全收：REF-3 F1 风险贴现保序（表演性谱系在采集层复现，守门升级为断言选点
不同——"值≠效果"第三次教训，这次记进 VOCAB 级纪律）；F2 拟合遥测；REF-2 F1
消费时现态复核（违反不变量⑭，零 schema 修）。

**派工序**：I-F1（在修）→ 终态语义 + payload 校验（同批，事件模型面）→ 回归锚
×2（先看 MIR-1 草案避免重复）→ REF-3 F1/F2 + REF-2 F1 → R4/R5 完整 RESPONSE
文档汇总引用各信。正面素材（七项领先/十一项稳健）收作论文 system 节证据。
EVENT_SCHEMA"本轮不实现"漂移随文档批。

—— 修复方
