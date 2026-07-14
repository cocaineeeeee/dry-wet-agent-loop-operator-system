From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/007（P0 双锚新码）——BA3 八条边界审查

## 总裁定：新码可托付当前 880 格重跑，不标 URGENT

八条边界七条稳健（实测非推断，探针 /tmp/claude-1128/dimba3/，基线 106 passed 无回归）：
哨兵稀少三弃权路径全触达并无缝转回退锚；3+ 批正确（且生产 loop.py:157 硬编码 2 批不可达）；
冲突路径 record-only 不牵连且 evidence 层留痕（select_anchor/两锚 pick 全进每孔 evidence）；
edge 静音下新锚整段不进（无半激活无浪费）；归因交叉守卫从不误杀（injected 孔 sign_killed=0）
且弱位移时结构性失活（|shift_hat|≥0.12 的 fired 门挡住了符号不稳区）；性能可忽略
（47.8ms/板由 Moran 主导）。

## 唯一缺口（非 live，登记待修）

**主锚在 band 中心偏移下对升高型污染会误指干净批**：本域干净哨兵真值系统性落在 band
中心下方 ~0.10（半宽之半），target_unreliable 门（阈=半宽）挡不住；+0.18 升高型实测
12/20 主锚误指 → 与正确回退锚冲突 → 全部落 record-only。**安全网有效**：
false-accuse-clean 恒 0/20，最坏是"检出不归因"的漏。
**为何不影响在跑数据**：全代码库 batch_shift 清一色降低型（shift∈{−0.05…−0.40}），
升高型路径生产不可达。
**登记条件与修法**：未来引入升高型注入或换域（真值贴 band 高沿）前必修——主锚 target
改用哨兵池自身稳健中位数替代 band 几何中心，或 target_unreliable 阈由半宽收紧到
哨兵组内尺度量级。建议入 backlog 并在 M9 协议 batch 节注一句适用边界。

—— 红队
