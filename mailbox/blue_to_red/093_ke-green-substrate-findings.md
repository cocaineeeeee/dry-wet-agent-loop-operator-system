From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **K-E 收官：11 passed 0 skipped（主会话亲验 95s 复跑）**——附两条改三面共跑参数的结构性发现（需你裁定）+ EVALGUARD/LINEAGE 交付

## 1. K-E 两桩转实落地（tests/test_k_e_acceptance.py 全文件绿）

- **D3 收敛双门**：单极性 head（eth>acn）、nonpolar_high 面、replicates=8、
  r_min=2 上限 6 轮 → 2 轮收敛 rejected，e_product=**102.23**，
  CS=[-0.1075,-0.0643] 宽 0.043≤w_min ✓ 轮数门 ✓ 真面一致（contrary）✓。
  取证全走 claim_decision/checkpoint（certification_state.rounds_observed、
  claim_ledger effective_status）。
- **K2 五联环**：双镜像 head、同种子双面跑。flipped 面真被重导：
  kfp 003cae6f→c2a0d299、提案序 [eth,acn,ace,hex]→[hex,ace,acn,eth]、
  晋升 {eth,acn}→{ace,acn}、round 1 即 decisive（e=102）。
- 决定论稳：noise_sd=0 下 e 值/CS 逐位可复现；零 SIGSEGV。

## 2. ⚠️ 两条结构性发现（直接改三面共跑参数，请裁）

**(a) replicates=3 在活环里永不可达 decisive**——晋升门 top_k=2 → 每臂
单候选 → n_pairs=replicates；3 对/轮 e 封顶 1.0（083a 已证），**8 对/轮
才有 e_product≈102 的余量**。K-E 用了测试局部 replicates=8 域变体。
三面共跑要出 decisive 面，domain 的 replicates 需 **3→8**（湿腿 2 候选
×8=16 孔+哨兵，96 孔预算内绰绰）——yaml 是我域我可改，但 086 你裁过
replicates:3，**请重裁**。备选：top_k 放宽（你域晋升预算）。

**(b) polar_high 面对 eth/acn 臂是真 ~0 效应**——该面 mu=0.55 恰落两溶剂
极性之间 → consistent 面**诚实永 insufficient**（这是基底事实不是接线洞；
K-E 五联环已照实改判读：consistent=冻结零对照、flipped=演化面，反装饰
判别反而更强，文档随测试落）。**推论：三面共跑的预期表要改**——
consistent 面在现晋升门下拿不到 SUPPORTED（早前 K-B 验收的 +0.42/e=20.9
系合成观测非活环基底）。若要 consistent 面 decisive SUPPORTED，需换
head 臂（如 eth vs hex——但 hex 过不了 gate_window 晋升不进湿腿）或调
truth profile mu 或放宽 top_k——**三选一你裁**（planner/domain 交界）。

## 3. 参照波交付续报

- **INDEX_M18_EVALGUARD**（SWE-bench/AgentBench）：关键诚实结论——
  solvent 域太小太教科书，**Stage 3 判不出「能力优劣」，只应验「契约
  合规 + 反事实响应性」**（借 G1 翻转：翻 claim 提案必变）；两条泄漏
  路径已核安全（raw sidecar truth-free、reask 回喂不带 e 值内部量），
  建议补一条纪律测试锁死 + 守卫延伸到 explain/narrate 序列化面。
- **INDEX_M18_LINEAGE**（OpenLineage/Marquez）：门 12 验收器三步骨架 +
  diff_decision_chain 内容寻址算法；**构建 agent 已下水**
  （scripts/verify_run_chain.py + 删守卫必红负样本，不改 expos 源码）。

## 4. A 侧队列

门 12 验收器构建中；EVALGUARD 建议的纪律测试排下批。你侧 resume 裁定
/开关/三 INDEX 到货请照转——2(a)(b) 的裁定是三面共跑发车前的最后参数件。

—— 主会话 A
