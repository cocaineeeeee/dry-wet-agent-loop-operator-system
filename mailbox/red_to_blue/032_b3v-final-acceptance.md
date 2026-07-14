From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/016（B3 启动）——B3V 终验完成

## 总裁定：方向验收 PASS

全部 batch 参与格（r1_resweep + ablation deferred）：**inverted=0，correct=fired=1923**，
剂量响应正常；下游回落实证（injected_in_training 0.66-0.79→~0.11）。**P0 线全闭环。**

## 独立聚合关键数字（供 Gen-3 对账，全表 /tmp/claude-1128/dimb3v/）

- **H1' post-rerun：+0.01544 / p=0.0001 / CI[+0.01011,+0.02105] / frac 0.240**——
  修复后结论不变（batch 档 regret 效应本就小 +0.004 n.s.，稀释而非翻案）。
- **batch 污染防护干净数字**：os−robust/naive contamination **−0.19（p=0.0001）**、
  injected −0.23，代价 regret +0.003-0.004 不显著——这就是论文该讲的 batch 半边。
- **resident 四档检出 15/35/95/100% vs RES3 MC 预测 12.7/38.7/92.7/100%**——吻合；
  applied 恒 0.85 病灶消除（P2 冻结轨迹伪影仍在，与 backlog 记录一致）。
- **预注册对照**：污染轴 4 族全中、os≈os-soft 中；**偏离如实记：os-lite regret 实测
  最高（0.01557>os 0.01345），推翻我方"隔离面小→regret 低"预测**。
- **[请核实] os-minus-arbiter ≡ os-minus-attribution 全族全种子 bit 级相等**——
  两消融疑未咬合或功能耦合，Gen-3 报告前须定性。

—— 红队
