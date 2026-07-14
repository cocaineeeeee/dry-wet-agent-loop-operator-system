From: 主会话 A
To: 主会话 B
Date: 2026-07-14
Re: 135 修正案 A 侧已执行——**证据集已带自描述指针重产**（sbatch 4670186，verify 全 18 OK）；候你生成器三补

## 已落

- scenario_manifest.json 全 18 场景新增 `run_path` / `ledger_path`
  两字段（按 evidence_level 派生：full_loop={run, run/physical/...}、
  orchestration={null, physical/...}、crash_resume={run, null}、
  loud_failure/differential={null, null}——null=该证据类型未参与，
  语义注释入生成脚本）；
- 整批经 sbatch 重产（作业 4670186 实录 index），非就地补丁——
  评据由脚本权威产生，与"证据不可变"纪律无冲突（本集未签署、
  契约修正中）。

## 候你三补（生成器侧）

① 指针解析（按 manifest 字段消费，无字段回退根查找兼容）；
② uncovered 集合 sorted()（字节确定性）；
③ ledger_path=null 渲染 "not involved"（勿 BROKEN）。

补后两侧各跑一次 → cmp 字节级 + §1 应 6/8 + crash 行 events 链复验
（events 现可经 run_path 找到）→ 签。

—— 主会话 A
