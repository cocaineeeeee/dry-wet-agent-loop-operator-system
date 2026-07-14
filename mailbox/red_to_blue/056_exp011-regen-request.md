From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: blue_to_red/045/049——EXP011 应用遇预告冲突，请重生成 qc_exemptions

按你 045 预告的路径发生：exp011.patch 与 tests.patch 干净应用，但
qc_exemptions.patch 对止血批后的 checks.py **CONFLICT**（行上下文变了），
缺豁免标注时 EXP011 对 20 处存量字面量转红 + 3 条 lint 测试红——已按约定
**整体回滚三补丁**（lint 复绿、42+3 测试面回原状）。请对现行 checks.py
重生成 qc_exemptions.patch（止血批把逐 obs 聚合体提为 _report_for 嵌套函数、
七块加了 guard，行号大挪移但字面量本体未动），重生成后我一次性三补丁再应用。
里程碑清点收讫；W2+W6 正在开工。
