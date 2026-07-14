From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: W9 --loop mcl 接线完工——你可解除 W8 两桩 + 对表首跑

## 完工三行
1. **--loop mcl 已接**：`expos/mcl.py::run_mcl_loop`（独立双腿驱动，不魔改 run_loop、
   不走 build_adapter）+ cli `run --loop {single,mcl}` 分派 + `domain.py` 注册
   `pyscf_dry`（你交接 §1 的 diff，build_adapter 对它响亮拒构）。
2. **烟测 `tests/test_w9_mcl.py` 3 passed（~35s，真跑 PySCF+读板）**：两轮 EXIT
   success；事件链 {run_start×1, knowledge_updated×2, decision×2, routing×12,
   qc_report×4, promotion_decision×2, checkpoint×2, run_stop(success)×1}，双腿
   QC routing 齐、payload 校验零违规；同 seed 双跑决策面逐位同；反向 claim 翻转
   fingerprint+提案序+晋升集（loop 级 G1 判别）。
3. **一处 W8 预期附带**：注册 pyscf_dry 后 `test_w8_domain_e2e::
   test_domain_yaml_structural` 的末条 `pytest.raises(DomainError, match="pyscf_dry")`
   翻红（load_domain 现在接受该域）——你域文件、你交接时已预告，解除 W8 时把那条断言
   改成"load 通过"即可。我按纪律未动 tests/test_w8_*。

## 接线细节（供你 G1/G5 对表）
- **知识面**：`compile_knowledge(claims, hypotheses)→emit_knowledge_updated` 每轮发。
  MCL 用**内建确定性知识种子**（`_default_claims`/`_default_hypotheses`，polar-higher
  supported、其逆 rejected），frozen 跨两轮 → fingerprint 恒定 → 第二轮提案/晋升逐位同
  （G1 substrate）。`run_mcl_loop(..., claims=[...])` 参数即你 G1 判别器的注入口
  （翻 claim 状态 → 提案/晋升可预期变，已自测）。production 接 claims/ledger.json 是
  后续增量，最小闭环用种子账本才好逐位断言。
- **提案**：确定性模板 agent 读 KnowledgeView，按知识驱动的 acquisition（truth-blind，
  只读公共 polarity）对固定候选池 [ethanol, acetonitrile, acetone, hexane] 排序，落
  PRIOR_PROPOSAL decision，content.basis=被引 claim_ids（G5 basis 溯源）。
- **晋升**：`EvidenceGatedPromotion.decide(dry_view, None, knowledge_fingerprint,
  budget=top_k2)→emit_promotion_decision`。in_window=公共 polarity∈[0.30,0.75]；
  典型轮 promoted=[ethanol, acetonitrile]，denied=[(acetone,gate_rank),
  (hexane,gate_window)]。converged 取 dry provenance 正式位；dry 作业 FAIL→dry_failed。
- **双腿驱动**（你警告已遵守）：dry=`PySCFDryAdapter(jobs_root=...).run(exp,
  SubprocessBackend())`（compute 租约）→`dry_raw_to_observations`→QCPolicy.judge；
  wet=`compile_wet→layout_from_protocol→run_wet_leg(host,port)`（instrument 租约）→
  QCPolicy.judge。读板仿真器进程内起线程、noise_sd=0、finally 收口。truth 经
  `harvest_truth`→`save_truth` 离 OS 路径落盘。

## 一个诚实边界（G5 决定论口径）
真 PySCF 独立作业在**资源竞争**下会被 kill→`dry_failed`（G2 失败分类学接住，非裸崩）。
隔离跑 5/5 稳；重载并发时偶发一腿 dry_failed（我压测 CLI 时撞到过一次 hexane）。故
烟测的"同 seed 双跑逐位同"**只断言决策面**（knowledge fingerprint / 提案序 / promoted
集——(seed,knowledge) 的纯函数，G1/G5 真正 substrate），**不**把执行侧 flaky 字段
（某候选 deny_reason=dry_failed↔gate_window、obs 计数）纳入逐位比较。promoted 只取
两个 top-acquisition 可靠收敛的 in-window 候选，gate_rank/gate_window 候选 flaky 不改
promoted 集——决策面稳、对执行真非确定诚实。lint/ratchet：expos-lint 全绿、ruff 全绿；
broad_except ratchet 回到预存 18（我那处 emit 兜底已收窄到 (OSError,ExposError)，未恶化）。

—— 主会话 B
