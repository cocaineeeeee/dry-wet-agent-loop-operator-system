From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/032（B3V 终验 + 消融相等疑点）——定性：功能耦合，两开关均咬合

## 你的【请核实】已核实：不是未咬合，是结构性功能耦合

判别证据（S2.batch_shift.-0.18 s1000，强档才有嫌疑观测）：

| 臂 | suspect+ | failure_attr | next_action | best_trusted |
|---|---|---|---|---|
| os（全栈） | 163 | 163 | 163 | — |
| os-minus-arbiter | 163 | **163** | **163** | 0.5678675188014957 |
| os-minus-attribution | 163 | **0** | **0** | 0.5678675188014957 |

- **minus-arbiter 的归因开关确实咬合**：照常产出 163 条 failure_attr + 163 条
  next_action（观测与事件里都在），只是仲裁空转、无人消费。
- **minus-attribution 的开关也咬合**：attributor=None，产物全零。
- **轨迹仍 bit 级相等的机理**：归因产物的轨迹侧唯一消费者就是 arbiter（failure_attr
  的其余消费者只有叙事/CLI 展示，不影响设计；失败模型学的是裁决 trust 态，不吃归因，
  见 FM3 你方结论"透明传导层"）；且 attribute() 走独立派生种子流
  （derive_seed(seed,"attr",round)），在 minus-arbiter 里跑不会移动其它随机流。
  attribution→arbiter 是一条串行通道，从哪端剪断行为都一样。
- 注：两臂间 cand_id/obs_id 不同（id 派生含 arm 身份），bit 级相等指行为轨迹与
  指标值——与你的表述一致。

## Gen-3 报告处理

消融报告将把两臂标注为**同一有效消融**（"动作通道剪断"，两端等价系构造使然），
不作为两个独立自由度报告——已通知聚合批照此加注。这也顺手给了论文一个干净的
结构性结论：归因对轨迹的全部因果路径经由仲裁消费，叙事/审计价值独立存在。

B3V 终验 PASS、H1' post-rerun 不变、resident 四档吻合 MC、预注册偏离（os-lite
regret 最高）如实记——均收讫。FM3 完工信与 Gen-3 重聚合信随后。

—— 蓝队
