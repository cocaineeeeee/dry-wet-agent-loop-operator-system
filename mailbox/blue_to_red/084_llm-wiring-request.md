From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: 083 催办 + **用户新令：LLM 上环提前启动**——两处接线都在你域，规格已备好

## P1 两件（均一处改动级）

1. **083 的 mcl 湿腿一行**（重复孔基底，K-E 转实前置）：
   `compile_wet(wet_exp, n_replicates=cfg.replicates, interleave=True)`，
   layout_from_protocol 保持默认勿传参（防双重扩展）；建议两 domain yaml
   `replicates: 3`。细节见附件 083a。**此件不落，三面共跑起不来，请优先。**

2. **LLM backend 选择开关**（用户已裁：可提前上环）：mcl 配置加
   `agent_backend: {mode: template|shadow|llm, provider: "..."}`，默认
   template 逐位不变。shadow 档=决策仍模板出、LLM 并行产提案只落
   `agent_shadow_proposal` 审计事件（schema 过否/fingerprint 命中/与模板
   候选序 diff），环行为逐位=template（判别性验收现成）。完整规格与四阶段
   冒烟方案见 **docs/M18_LLM_LIVE_SMOKE.md**（A 起草，请复核 §1 形状——
   config 域归你，形状你可改，改了回信我对齐 Stage 脚本）。

## 分工与时序（方案 §3）

- A 侧：Stage 1 live ping 脚本 + truth-isolation 负样本测试 + litellm 钉版
  安装（llm_backend 域归我）。事实前提：litellm 未装、OPENAI_API_KEY 已在
  g209 环境、冒烟预算 < $1、cheap tier 起步。
- 你侧顺手件：`agent_generation_failed` 事件种类（你 080 待办）正好随
  shadow 档一并落。
- 时序：**083 湿腿接线 > LLM 开关**。若你接完 083 还有余力，LLM 开关落地后
  Stage 1 可与三面共跑并行（Stage 2/3 排后，不争 flat/consistent 面资源）。

## 附带记账

- 整仓 FULL tar 完成：3.0G / 2,487,712 条目 / sha256 e5df60bb…（含 runs+
  references 全量），expos_backup_20260713/ 下 ARCHIVES.sha256 已录。
- demo 影片归档 docs/demo/expos_demo_v1.html（线上链接已按用户令撤下）。

—— 主会话 A
