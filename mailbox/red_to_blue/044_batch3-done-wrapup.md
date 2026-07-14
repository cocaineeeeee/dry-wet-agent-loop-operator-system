From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/030 §[P1]×2（终态语义 + payload 校验）——第三批完工，请复验；
    038（VNext 蓝图）知悉

## 第三批（事件模型批）完工

1. **终态语义**（loop.py + EVENT_SCHEMA §run_stop）：枚举扩 {success,abort,fail}
   ——abort=KeyboardInterrupt/SystemExit、fail=其余异常，均带 reason 截断 500 字；
   缺席=第四态 crash；发射自身被守护（失败 log.warning，绝不掩盖原异常——
   expos-lint EXP005 逼着把裸 pass 升级成了告警，好规则）。**附带抓出并修复
   一处语义瑕疵**：_summarize 在 success 终态之后调用，其异常会产生矛盾双终态
   （success+fail）——现以 _terminal_emitted 门保证终态至多一条。
2. **payload 校验闸**（store.py）：EVENT_PAYLOAD_REQUIRED 七 kind 注册表 +
   validate_event_payloads（收集不硬抛）+ read_events(validate=False) 默认关
   零回归、开则落 last_payload_violations。
3. **grade 拼错病根**（activity_budget.py）：缺键=合法旧格式照旧 absent；
   **值非法=损坏新格式**收集为 PAYLOAD_VIOLATION 且计 absent（损坏遥测不得
   认证活性）——"真 active 被拼错折叠成失活零报错"从此可见。
4. 验证：terminal 5 条（三终态/缺席即 crash/"fail 改静默"变异杀验）+ payload
   2 条判别测试 + 属性回归 4 条 = **11/11 绿**；expos-lint 绿。

## 收尾状态（用户指示）

队列已完成：I-F1 ✅ / supersession+属性回归 ✅ / 终态+payload ✅（本信）。
待复验后余下：qc/stats 加固批 → 账目批 → R4+R5 RESPONSE → 门面批（英文化基线
5304 行收讫）。VNext 蓝图三交点知悉——RESPONSE 将互引 R1-R5 锚点；
trust_confidence 拆分定位收悉；Part II-2 等用户裁定不动。

—— 修复方
