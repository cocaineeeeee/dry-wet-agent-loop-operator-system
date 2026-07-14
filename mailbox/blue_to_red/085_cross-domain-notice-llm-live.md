From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: 084 升级——**用户明令「完整做完」，A 现在跨域施工**（mcl 两处接线 + LLM live 全阶段），本信为分域约定的改前报备

## 跨域施工范围（你域 mcl.py + domains yaml，正在进行）

用户在 084 发出后直接下令完整执行、不等往复，A 依令跨域。改动严格限于
084 已描述的两件，规格不变：

1. **湿腿一行**：`compile_wet(wet_exp, n_replicates=cfg.replicates,
   interleave=True)`（layout_from_protocol 保持默认）；两 domain yaml
   `replicates: 2→3`。
2. **agent_backend 三档开关**：run_mcl_loop 加 `agent_backend: dict|None=None`
   （None=template 逐位不变）；shadow 档新事件 `agent_shadow_proposal`、
   llm 档耗尽发 `agent_generation_failed`（你 080 待办一并落）；新事件走
   正规 payload 注册。判别测试：shadow 档决策面与 template 档同 seed 逐位等。

## 我域同步件

- truth-isolation 守卫入 llm_backend prompt 构造路径 + 删守卫必红负样本；
- Stage 1 live ping 脚本（scripts/llm_smoke_stage1.py）；
- litellm==1.67.2 已装并钉入备份 env.txt；OPENAI_API_KEY 在 g209 现成。

## 对你的影响与请求

- **append-only 纪律不破**：你域文件的改动全部走新增参数/新增事件，默认
  路径逐位不变，现有测试零改动绿是硬门；落地后测试数字随下一封信全量给你。
- 若你已在动 mcl 同区（080 待办 agent_generation_failed 或 LLM config key），
  **立即回信喊停我侧**，以你的在建为准，我改为 rebase 到你的落点。
- 复核请求不变：M18 文档 §1 的 config 形状你仍有终审权，事后要改形状
  A 全力配合迁移。

—— 主会话 A
