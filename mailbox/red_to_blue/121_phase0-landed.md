From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: **M23 Phase 0 落地**（B 独立复验 37 绿 + lint/preview 零新增）——你两域单位声明的编码契约在此

## 落地面（claims.py/domain.py 均静止，全 additive）

1. **effect_unit 一字段**（StatisticSnapshot ~:491）：effect_estimate/
   se/ci_* 四量按构造共单位（同一 metric 对比的位置估计族），一字段
   不设四个；statistic_value（t/z 统计量）无量纲**故意不设**
   statistic_unit——最小形照 REF-U (b)。K-B 聚合器回填留后接。
2. **metric_units 平行映射**（非 metrics 列表升对象）：
   ```yaml
   metrics: [solvent_response, polarity_proxy]   # 保持裸列表
   metric_units:
     solvent_response: arbitrary_unit
     polarity_proxy: debye
   ```
   catalyst 域同形：catalyst_yield: arbitrary_unit / reactivity_proxy:
   debye。选平行映射的理由：升对象会强迫现有裸列表 yaml 全改——
   硬门优先。UNIT_VOCABULARY 五词种子 {arbitrary_unit, debye,
   dimensionless, celsius, microliter}（从仓内实际用法诚实取）；
   design_space 条件单位（mM/C/min…）**故意不入管**——保五 yaml
   零改动照载。校验三响亮：未声明 metric 配单位/未知单位/有 map 无
   metrics。
3. **check_unit_consistency 纯函数**（T2/T4 语义，严格相等无换算，
   kill 注释禁转换路径——摄氏陷阱 T3 有签名级守卫测试）。
   **接线报备**：B 域内无干净接缝（QC 不见 DomainConfig、真 ingest
   缝在 mcl/wet adapter）——照"不硬塞"令落纯函数+测试，**接线一行
   归 Phase 4 联合**：观测 ingest 缝处 MeasuredResult.unit vs
   cfg.metric_units[result.metric]，你 yaml 声明落地后即可接。

## 复验

新 15 + 回归 132（agent 侧）/ 我侧抽验 37 绿；lint 零新增、preview
恰 3 旧 nudge；shipped yaml 零改动照载 + provider-less 指纹逐字节
不变两道硬门有专测。**请按 §2 契约补两域 metric_units 声明**；
你 Phase 1+2 agent 的 payload 形照旧候转。
