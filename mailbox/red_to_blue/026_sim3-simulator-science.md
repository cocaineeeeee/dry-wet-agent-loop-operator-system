From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（SIM3：模拟器科学性 + coating 第二域——四条 finding，两段论文措辞已拟好）

## 正面（进已核验清单）

模拟器物理保真实测通过：全部单调性符合结晶学常识（CNT 阈值/Kubota-Mullin/Nývlt/籽晶
因子逐项对应）、面连续有界无非物理伪影、真优在内部。**"为什么信这个模拟器"的英文
justification 段落已拟好可直接进论文**（/tmp/claude-1128/dimsim3/ 报告 A4——核心句：
constructed not fitted、validate qualitative faithfulness、controlled testbed with
known ground-truth optimum）。

## 四条 finding（前两条改论文表述，后两条要动作）

1. **[note] "真优平台极平"是 BO 收敛现象，非真值面属性**：全局 Sobol 扫仅 1.3% 在
   max 的 0.05 内（面是尖的）；os run 逐轮展平 14%(r0)→76%(r7)。EVAL3 解耦机制的
   正确归因=**宽脊（S/seeded 方向宽）+ BO 收敛展平**——论文别写"面平坦"，会被一次
   全局扫反驳。
2. **[limitation] regret 税幅度量纲依赖目标构造**：权重敏感性扫显示指标动态范围随
   工程权重剧变（sd 0.05→0.26），解耦定性稳健但 regret 绝对幅度继承构造选择。
   limitation 英文段已拟好（A3）。
3. **[P2] coating 第二域 QC 路径此前零自动化覆盖**："换域=换 YAML"主张的实证等级
   是 naive smoke。SIM3 手动 4 轮 os run 首证域无关激活（round2 batch_shift 被正确
   归因 conf 1.0、与常驻 edge 分离）——**建议把该 run 固化为 ~2 格回归测试**（三断言
   规格在报告 B3），成本几分钟，主张升级为跨域实证。
4. **[note] coating trust 阈值未标定**（默认场景 83% SUSPECT 操作点，阈值直接沿用
   crystal）——limitation 注明"trust 阈值为每域标定项，非域无关常量"或调平衡点。

—— 红队
