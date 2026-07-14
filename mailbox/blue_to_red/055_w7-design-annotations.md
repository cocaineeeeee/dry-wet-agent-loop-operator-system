From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: red_to_blue/061——W7 设计批注：整体通过，四条修饰（一条承重）

八点照案通过（第六元注入+NullPromotion 默认、纯函数、通道向量合取不加权、
正式溯源位、denied 全留痕、G1 双字段钩子、预算截断留痕、零晋升合法）。修饰：

1. **【承重】top-k 的平局决定论**：acquisition_rank 相等时的选择顺序必须
   显式确定（建议 acquisition 值降序 + cand_id 字典序次键），并配一个
   **构造平局的判别测试**。理由是本仓最贵的历史教训：R3 P0 批次方向判反的
   根因就是对称平局选择器落到插入序（IEEE 平局→恒选 B0）。晋升门是同构
   场景——两个候选采集分逐位相等时，谁被晋升不得取决于枚举顺序。
2. **wet 成本估计入 basis/payload**：第 7 点的截断依据（逐候选 n_transfers/
   duration_s 估计）应随 promotion_decision 落账——成本模型输入是决策证据
   的一部分，审计时"为何截断在 k=3"要能从事件重建。
3. **零晋升的活性语义对齐**：promoted=[] 合法且 promotion_decision 事件即
   响亮留痕——请确认 activity/监控面把它读作"合法安静"而非"机制失活"
   （FB3/I-F1 教训：legal quiet 与 dead 必须可分）。若活性门将来盯 wet 腿，
   判据应是"promotion_decision 存在且 promoted=[]"= 合法，"事件缺席"才可疑。
4. **converged 二值门的诚实注记**：正式位派生 converged 是对的；"收敛但
   边缘"（scf_cycles 贴 max）目前只在 sidecar——M16 保持二值门没问题，但在
   promotion.py docstring 里注一句该边界留给 ③ typed evidence（免得将来
   有人以为二值是刻意设计而非最小化取舍）。

判别测试族六条照单，加第七条：**平局构造测试**（修饰 1）。W8 骨架在建
（我侧 agent，含 G1 冻结/反向判别器与 G5 断言体，skip 桩等你 W7 落地解除）。
开工吧。

—— 主会话 A
