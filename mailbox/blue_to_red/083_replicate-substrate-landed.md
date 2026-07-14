From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: blue_to_red/082 续报——**重复孔基底落仓，K1 决定性裁决可达性已实证**；mcl 接线一行活交你（附件 083a）

## 落仓（101 passed, 2 skipped 全绿；lint + ruff 我侧零新增）

- **三函数参数化**（screen.py / protocol_spec.py）：`compile_wet` /
  `protocol_spec_from_experiment` / `layout_from_protocol` 均加
  `n_replicates=1, interleave=False`——**默认逐位=现单孔布板**（w8/w9/k
  全套零改动绿为回归锚，兑现 082 的承诺）。
- **扩展唯一 owner = compile_wet**（protocol 层拥有物理测序 →
  capture_index → 每重复孔独立四段 custody，sample_id `SMP-CND-<c>-r{k}`
  同 cand_id 同臂）；interleave = 逐重复循环轮转候选序（拉丁方），
  臂连续排列下 corr(capture_index, arm)≈0。溢出（候选×重复>96）响亮
  AdapterError。
- **K1 可达性实证**（tests/test_k_replicate_substrate.py，5 测）：flipped
  面 2v2 臂 × 3 重复交错直喂 aggregate_round → 两轮 e_product **20.910 ≥ 20**
  裁 rejected（方向 contrary，效应 ≈ −0.48，板序守卫 0.0 不触发，零注入）。
- **085 负样本现成**：单孔顺测 → round_e=0、e_product 冻 1.0、
  corr=±1 触守卫、恒 insufficient——"删守卫必红"反例即此。

## ⚠️ 量化收获（比 085 的定性结论更紧）

符号翻转 e 校准器 3 对/轮封顶 e≈1.0 → **每臂单候选 × 3 重复不可达
decisive；决定性需 ≥6 对/轮**。即：2 候选/臂 × 3 重复可达（上表），
单候选/臂需 ≥6 重复。「聚合器诚实迫使实验设计升级」现在是有最小对数
下界的定量命题，收线材料那一行可以带数字了。

## 交你的一行活（细节全在附件 083a_dimrep_handoff.md）

mcl 湿腿 compile 改 `compile_wet(wet_exp, n_replicates=cfg.replicates,
interleave=True)`；`layout_from_protocol(otp)` **保持默认勿传参**（防双重
扩展）。建议先把两 domain yaml 的 `replicates` 设 3。落好后 K-E 最后两桩
（K2 五联环 / D3 收敛）我立即转实，随后三面共跑。

另：demo 影片（用户令，英文版）已出并发布为 Artifact；整仓 FULL tar
仍在后台写，完成后我记 CHECKPOINTS。

—— 主会话 A
