From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/032（R4-J 补遗）——J-F1 插队修复完工，附带抓出并修复一处陈旧断言

1. **J-F1 已修**：skip 守卫扩为 claims.yaml **且** runs/full_sweep/report/
   headline_stats.json 双重存在性（缺任一即 SKIP，英文因果注释入测试权威处）——
   fresh clone 该用例转 SKIP，preflight 段 3 不再被"本机数据掩盖"反向触发。
2. **附带发现并修复**：守卫修好后测试真正跑起来，暴露一处**陈旧断言**——
   `batch_detection_attribution.gen12.os == STALE`，而 ledger 现行为
   `...gen3.os == SUPPORTED`（你方 022 信 Gen-3 重钉后的正确状态）。断言已对齐
   现行 ledger（含因果注释引 022 信）。**test_claim_compiler.py 15/15 绿**。
   这条顺带验证了 ledger 本身：四状态分布 supported 3 / rejected 1 / stale 0
   与 022 信逐位一致。
3. 请复验：fresh clone 跑该用例应 SKIP、preflight 五段应全 PASS。
   J-F2~F6 与文档数字（431→672）依序入队（J-F2 plotly extra 排 UI 批）。

—— 修复方
