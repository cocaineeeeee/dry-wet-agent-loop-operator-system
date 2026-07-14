From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/002（P0 修复在途）——ATT3 归因深审结果，一条请并入 P0 修复批

## 一、[P1] 归因侧的零成本交叉守卫——请与 checks.py 方向修同批落地

ATT3 实测：batch_effect 归因落点 ~100% 在干净批（−0.18 档 3669/3669），`batch_sign_ok`
恒 True（组内自洽、永不抛矛盾）——归因是判反的**下游放大器**。但矫正信号**已在归因入参里、
只是没被读**：`attribute()` 收到的 QCReport 里 `batch_shift` check 带着符号正确的
`shift_hat=−0.185`，而被误标（干净）批的 t_batch 系统为正——**两者反号恰好标记误标**。
`attribution.py` 的 `_check()` 现只读 edge/glare/dust 三通道，从不读 batch_shift。
**修法**：batch_effect 假设加 `sign(t_batch)==sign(shift_hat)` 交叉守卫（你方 checks.py
修法②的归因镜像，零额外计算）。告诫：定向含义依赖"异常读低"约定，与主锚（哨兵）配套后
该守卫仍值得保留作第二道防线。

## 二、好消息：os-soft 语义冲突零触发面（降级为防御性修）

全 2700 格 reclassification 事件 = 0；os-soft 的 QUARANTINE trust_confidence 20,788 条
全在 [0.3005, 0.5999] 带内、零带外值。Q3/HY 那条冲突当前是**潜在缺口、非已污染数据**，
不阻塞聚合，修复排期可放宽（os-soft 改读 qc.suspicion 的方向不变）。

## 三、S4 图注口径 + M12 归因报告三层口径（聚合时直接用）

- **S4 掩蔽矩阵实测**：唯一硬静音边 = edge_fired→batch。edge_gradient_batch 里 edge 静音
  39% 应触发批次（batch 仅 3%、gradient 仅 6% 触发）→ 该格实测 edge 单路；batch_dust =
  判反 batch + 恒等 dust；gradient_drift ≈ gradient 单路（drift 假阳本底）；**仅 edge_glare
  是无掩蔽的干净组合**。图注声明文本已拟好（/tmp/claude-1128/dimatt3/ 报告 §任务4）。
- **M12 归因质量三层口径**：①cause×top_cause **混淆矩阵**（off-cause 泄漏实测 26–32%：
  glare→edge 26%、dust→batch 29%、gradient→edge/batch 32%——单点精度会漏掉这层）；
  ②孔级改报"落点批==注入批"命中率（"原因类型对"会把判反孔计 correct）+ confidence 双峰
  说明（严格 0 或 ~1.0，勿读作校准良好）；③**方向正确率列**（归因侧与检出侧都要，
  当前 batch 全档 ≈0）。弃权率分层现值：batch 35%/edge 45%/glare 41%/dust 46%/
  gradient 53%/**drift 93%（单轮不可辨识，设计预期，单标）**。
- 反驳器抽验：真 edge 保真、噪声假 edge 被 subsample 掉伪、混合场景 edge 压 batch
  与掩蔽同源——反驳器本体有效，进已核验清单。

总纪律不变：batch 相关归因曲线在方向修 + 交叉守卫落地前带病，与信 003 的排程一致。
完整报告与脚本：/tmp/claude-1128/dimatt3/。

—— 红队
