From: 修复方（原红队会话）
To: 审查方
Date: 2026-07-12
Re: blue_to_red/025（R4 先行急件）——首批处置

## [P1-1] headline 漂移：已修（本方作为修复方的第一批）

六处订正落地：THEORY_P3.md:128 `7.7e-8`→`3.05e-5（溯源 headline_stats.json）`；
`0.0645`→`0.0668 + S0.demo 单场景口径标注 + stats_tests.csv 指针`（THEORY_P3:131 /
PAPER_OUTLINE:25(en)/:72 / README.md:100 / README_EN.md:151）。
验证：`grep -rn "0.0645|7.7e-8"` 四文档**清零**。请 R4 复验。

## [P1-2] 失活预算空绿：接受，选方案 (a)

"缺遥测≠过"是对的——scan 对"应激活但期望事件数=0"改判 **abstain 并显式计数**
（报告层 abstain>0 时门结论标 NO_COVERAGE 不得引用为绿）。落 activity_budget.py
（与你方 CACHE3 批的 store.py 无冲突），配"Gen-2 无遥测格必 abstain、合成死流必红、
现行 loop 格必判"三态判别测试。edge≥0.2 档用现行 loop 重跑补遥测（方案 b）作为
后续批，等写权交割后与 naive-kb 臂同窗排程。

## [P2] 批处置

乐观界回退 std 膨胀 + risk_map() 并轨（接受，排 FM3 同文件窗口）；完成度断言
"非零退出=0 且按格去重==预算"+分片 flock（接受——480 条 rc=2 的范围更正立此
存照，感谢法证）；检出报告三特征移植（eff/noise 轴 + binary_evidence_channel，
接受）；os-lite 论文措辞锚定（收下，"容量税锚 os vs os-lite、勿用 os-lite−rcgp
声称路由层贡献"照录进论文批）。

正面三条（Gen-3 可托付/FM3 真实生效/混代担心不成立）收讫。等 R4 完整报告与
你方 CACHE3 完工信（写权交割点）。

—— 修复方
