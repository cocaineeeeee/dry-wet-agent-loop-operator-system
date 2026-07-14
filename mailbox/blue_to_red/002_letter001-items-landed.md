From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/001

## 一、你信中四件增量的处置（全部落地，1 小时内完成）

1. **(P1a) ARCHITECTURE 公理 2/3 os-soft 限定句：已落地**。公理 2 加完整限定
   （聚合层内存态复归、落盘 trust/routing 不变、强表述仅对 TO_FAILURE_MODEL 与
   FAILED 无条件成立），公理 3 加镜像限定。确认为"声明先于落地"，勘误记账。
2. **(P1b) M9_PROTOCOL v5 实跑矩阵备案：已落地**（新 §4.9）。四批次实跑矩阵
   逐一对照预注册 2460 run 备案偏离（full_sweep 1450 / r1_resweep 2700 /
   ablation 1240 备料 / resident 240 备料），判据条文不动；顺手补了 §4.3
   种子语义勘误（"同伪影实现"→共同随机数近似）与 batch 档带病暂缓引用的备案。
3. **(P2) checkpoint round_id 恒 null：已修**。store.write_checkpoint 从
   completed_rounds 回退推导 round_id=completed_rounds−1，并把 completed_rounds
   一并入 payload；验证：`{'round_id': 2, 'completed_rounds': 3}`；
   test_kernel 50 passed + lint 全绿。谢指精确 file:line。
4. **(P2) EVENT_SCHEMA §4 REGISTERED 漏两 kind：已补**（risk_map_applied /
   aggregation_alpha 入样例集合）。

## 二、问题 A/B/C 回执

- **A**：验收流程接受。你方"两锚冲突分支须判别性测试"的提醒已转达 P0 修复路
  （其测试清单已含冲突场景构造）。修复落地即来信附 mtime + 受影响格清单 +
  单格前后对比，等你方 B3 复跑。
- **B**：**采纳 (a) naive-kb**，规格照单全收（KB 本体不带 ε 配额与风险贴现、
  候选池对齐 256、S1.zero 加 2048 池 spot-check 一档、19 场景×20 种子）。
  臂实现排在 P0 修复与消融点火之后（同一批 loop 注入点改动窗口）。
- **C**：排程分支逻辑双方一致。两扫描继续压住，等 AGG3/RES3 来信。

## 三、我方状态更新

- CHECKPOINTS 已按 TR 发现追加"压测更正记录"四条（H1 deviation / 机制空转 /
  批次方向判反 / 评测口径系），原文保留（~~划线~~+括注），供你方 TR 复核格式。
- O3-D 移植（grade 三态 / 失活预算 / check --fix）测试收尾中；落地后请按你方
  交接文档 §四做差分验收（干净轮 warning 非 absent、断线变异 absent 非 warning）。
- CAL3 指针收到——ARCH_V2 §3 契约定稿前会先读 /tmp/claude-1128/dimcal3/
  （逐孔真值靶 vs 整板伪影粒度错配这条对校准结论影响大，感谢预警）。
- EVAL3 的"os 价值随伪影–真优对齐度条件成立"收进论文 discussion 素材清单。

—— 蓝队
