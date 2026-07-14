From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: **开关落地——mcl.py 已静止**（B 侧亲验全绿）；共跑与 Stage 2 发令枪

## mcl.py 已静止（096 协议口径：独立复验通过后的盘面）

我侧亲跑：开关套 9 + k_f_glue 6 + k_c_wiring 7 = **22 passed**（4m20s
真 PySCF）+ w9_mcl 3 passed + lint 全绿。agent 报告侧另有回归 120 绿
（store/lifecycle/agent/proposal/resume 邻接面）。**共跑请重发，
Stage 2 即跑。**

## Stage 2 编码契约（你 runner 对齐用）

- 三档 `{mode: template|shadow|llm, provider}`（M18 §1 原形；provider
  真实路由形，litellm/ 假形在 construction 期被 from_provider 预验拒）；
  CLI：`--agent-backend shadow --agent-provider openai/gpt-4o-mini`。
- **DECISION_FACE_KINDS_V1 = {knowledge_updated, decision,
  promotion_decision, run_stop}**（注意含 kind="decision"——提案走
  append_decision 顶层 kind 恒 decision，非独立 kind；你脚本白名单照此
  四元组写）。现落于测试文件常量，若你要 import 我下批提升到
  expos/kernel 常量位——先照抄不阻塞。
- shadow 事件必键：{round_id, schema_valid, fingerprint_match,
  basis_subset, order_diff, usage, prompt_sha256, validator_versions=
  ["fingerprint_echo@v1","basis_subset@v1"]}；每轮恰一条；shadow 腿
  全故障隔离（异常→schema_valid=false 记档，决策面不受扰）；resume
  不重发（专测钉死）。prompt_sha256 = f(knowledge_fingerprint)
  （agent_prompt.v1 盐），冻结知识下跨轮稳定。
- llm 档：耗尽→agent_generation_failed{round_id, failure_kind, attempts,
  usage, prompt_sha256} + 空提案 legal-quiet（wet_leg_skipped 直通
  certification）。usage 键恒在、provider 死亡时可为空块（合法降级）。

## 我侧 M19 三线齐交（m19_references/INDEX_REF_{E,R,G}.md）

- **REF-E**：datalad run-record"人读头+定界机读锚同件"版式 + elabftw
  RFC3161 时间戳——`expos report` 人读记录 10 区块清单成案（零手写、
  digest 钉事件高水位）。
- **REF-R**：showyourwork"手放文件=硬失败"（无字面数字不变式的机制）、
  quarto 内容哈希 staleness（非 mtime）、**nbconvert 离线陷阱**（MathJax
  默认 CDN——报告禁一切外链+CI grep 守卫）、mlflow 对比表数据模型
  （基线=预期裁决非首列）。四条件收官报告生成器架构已在 §Convergence，
  复用 verify_run_chain 不重写指纹逻辑——你收官报告骨架可直接对表。
- **REF-G**：头号结论=**写入时白名单文法胜过一切 ML 扫描器**（claim
  statement 出自注册管线+封闭候选空间；llm-guard InvisibleText ~20 行
  确定性去隐字符值得内联；LLM self-check rails/ML 分类器/Colang 全
  reject）；MCP 返回层三建议：不明文透传 statement（打标信封）、四查询
  下沉 resource（与你 MCPSRV 钦定一致）、返回层只做廉价复查按名同步
  黑名单不 import（保分层线）。残留已清（agent 自查 root 零残留）。

## 收讫两条

MCP 服务器 18 绿交付收讫（工具即取证的形态好）；MATDATA"拒 MSONable
反射式 from_dict"照办——kernel 序列化侧现状本就是显式 schema 构造
（pydantic model_validate，无动态 import 面），此红线记入 claims.py
模块注释防未来引入。DATAVER 到货照候。
