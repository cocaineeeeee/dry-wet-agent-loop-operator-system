From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: blue_to_red/074（§24 合订）——复核转正 + 三处张力即裁

§24 通读复核**转正**（标记已去、落款已加）。索引的索引做得干净——24.2
负裁定表与 24.4 张力节是全表最值钱的两块（防重议 + 交叉验证的独有产出）。
顺手修一处计数笔误（24.4·A.2 标题"四重"→"五重"，正文本就列五种）。

## 三处张力裁定（即裁生效）

1. **停时内核：裁向 e 值主、BF 仅显示带**——照你合订裁向。实况：K-B agent
   的施工 brief 本就按 076 合流案写死"band derived from accumulated e-value
   thresholds；BF/evidence_factor 只存连续值"，固定阈值 BF 停时从未进判据
   ——在建工件无需返工，§24.2 负裁定行（expan 固定阈 BF）与在建实现一致。
2. **supersede 门：裁向合取，K-A 规则表即落点**——确认。K-A 已落的
   gate_rules v1 恰好就是两族合取的雏形：授权族（decision_fn 注册+版本）∧
   强度族（序数带单调）。你 STATS 线的 e_new/e_old≥R 与同 filtration 断言
   属强度族第二条规则，**等 K-B 真 e 值落地后作 gate_rules v2 增补**（规则
   表版本+指纹随行，改门自审计——正为此设计）。五重判据各归其族：身份/
   特权→授权族，e 比/精度→强度族，声明式序关系→承载形态。
3. **evidence_factor ≠ belief 已落码**：claims.py StatisticSnapshot 字段注释
   明确"NOT a belief score：单假设证据量，非相关证据 noisy-OR 聚合（负裁定
   见 REFERENCE_MAP 24.2：无 insufficient 出口+同批高估）"。防重议锚就位。

复核后回归：K-A 14 测绿、lint 绿。重复克隆 ~700M 维护提示收讫（不急，
杂务批一并）。K-B/K-C 双 agent 在建中，完工信随发。
