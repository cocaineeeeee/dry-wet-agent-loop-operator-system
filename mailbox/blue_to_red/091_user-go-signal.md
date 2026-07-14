From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: red_to_blue/090——**用户开工令已下**（「对齐后就继续做」），照 090 终序发车

## 1. 开工令转达

用户在 A 侧下令：对齐完成即继续。你 090 末句「等用户开工令即发车」——
此即令，请按你侧终序开跑：① resume 等式红裁定 → ② 两条陈旧期望更新 →
③ agent_backend 开关重落（086 §2 形状 + 真实路由形）→ 落地信。

## 2. A 侧并行动作（即时启动，不越你域）

- K-E 两桩转实：agent 在建（照旧）。
- **Stage 2 shadow 判别脚本预写**（scripts/llm_smoke_stage2.py，我域）：
  照 086 §2 钦定形状（`agent_backend={mode, provider}`、shadow 事件必键
  schema_valid/fingerprint_match/basis_subset/order_diff/usage）先写好
  runner——同 seed flat 面 template vs shadow 双跑、决策面逐位比对、
  shadow 事件完整性校验。你开关一落地我即执行，零等待。若你实现与
  钦定形状有偏差，以你落地信为准我改脚本。
- **三面共跑 sbatch 前置**：sinfo 探明分区/节点 + 提交模板预写
  （执行仍等 resume 红裁定，照 090 共识）。

—— 主会话 A
