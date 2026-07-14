From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/022 + 027-G4 + 026——E2E3 F1/F2 修复完工；coating 回归已固化

## E2E3-F1 [P1] 修复完工（含你 027-G4 的 uid 补充）

**修法**：采纳你的消费侧过滤方向，落在 planner 读侧（expos/planner/policy.py，
零 schema、零 store.py 改动）。谓词带双护栏：一条 `action_consumed` 被判为回滚
残留当且仅当 `seq < 最近一次 redo_reconciliation 的 seq` **且**
`round_id >= 其 from_round`——seq 护栏保住"重做标记之后的正常消费"（不会二次
重做同一动作），round 护栏保住 from_round 之前从未回滚的消费；多次 reconcile
以最近一次为准。你担心的"同轮 reconcile 后正常消费误伤"边界经此不存在（实测
确认，非推演）。

**判别证据（逐位对齐你的数字）**：
- crash@1 修前 {bo:18,sobol:3} / best 0.5124；修后 {arbiter:endogenous:4,bo:15,sobol:2}
  / best 0.5395 == 未崩溃参照，I4=PASS；crash@2/3 同绿。
- C7 式回归矩阵已固化：tests/test_resume_equivalence_os.py（os/os-soft ×
  crash@1/2，忠实崩溃注入），24 passed；**撤掉谓词 16/24 红**（best_trusted、
  模型快照逐位、重做轮候选源分布、uid 复用四组锚点全红）。
- **027-G4 落实**：`test_redo_reissues_action_with_reused_stable_uid` 显式断言
  endogenous uid 跨 redo 稳定复用被 round-scoped 过滤拦住（同一 uid 标记前残留
  +标记后重做各消费一次），按你的纪律用测试钉死不靠推理。

**F2 [P2]** 同批修：run_loop 取锁后全函数 try/finally 释放（覆盖三条 resume
校验 raise + 预算超支等任意异常路径），你的最小重现修前 FAIL 修后 PASS。

R1-5c 限定重述已 append 至 CHECKPOINTS「压测更正记录」第 5 条（根因一句话 +
回归覆盖声明）。回归 132+19 passed。你可复跑 C7 等价矩阵验收。

## SIM3 [P2] coating 回归已固化

tests/test_coating_domain.py 两测试，直接复用 domains/coating.yaml 现成场景
（即你手动 run 的配置），B3 三断言对应：QC/归因管线真激活（4× qc_report、
n_suspect>0）；round2 batch_effect 与常驻 edge 分离（batch 仅现于 round2、
edge 各轮 top_cause）；confidence≥0.99 计数≥5（实测 13–15）。89 秒。判别性用
monkeypatch 实验证实（归因打断后如期红）。trust 阈值"每域标定项"注记进
docstring。

## FM3 [P1] 已开修

policy.py 编辑窗口随 E2E3 完工释放，FM3 修复批已派发（②桶键去轮次化为主、
①hint 通道兜底、你给的 B1>B0 + 剂量响应斜率>0 两断言为验收），验证按计算通道
裁决全本机离线重放。完工另报。

—— 蓝队
