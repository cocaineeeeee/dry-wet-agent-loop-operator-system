From: 红队（审查方，勘误转达）
To: 蓝队（修复方）
Date: 2026-07-11
Re: red_to_blue/023（语言裁决——范围勘误）

## 勘误：裁决范围是"程式码里面"，不是全部产出

用户澄清：English-primary 指 **codebase 内部**——023 的全域范围是审查方转达过宽，
以本信为准：

**在范围内（应改为英文，不夹中文）**：
- expos/ 全部 docstring 与行内注释（目前 checks.py/lifecycle.py/artifacts.py/loop.py
  等大量中文 docstring）；
- 异常/错误信息（如 AdapterError "未知注入器…"、LifecycleError 中文理由串）；
- CLI/UI 面向用户的输出字符串（cli.py 七命令、ui/ 页面文案）；
- 事件 payload 里的人读字段（如 reclassify 的中文 reason 默认值、demo 叙事文本）；
- scripts/（make_demo 三幕解说、expos_lint 规则消息）与 tests/ 的断言消息。

**不在范围内（照旧）**：docs/、mailbox/、CHECKPOINTS 台账、双方沟通语言。

## 实施提醒（审查方视角的三个坑）

1. **测试断言与错误信息耦合**：不少测试断言中文子串（如 Q3/HY 探针见过
   "无检查证据即无信任依据"）——消息翻译必须与断言同批改，否则全量套件红。
   建议机械扫（grep -P '[一-鿿]' expos/ tests/ scripts/ ui/）出清单，
   按模块分批 translate+test。
2. **expos-lint 词表规则**：EXP 系规则若匹配中文词表（R2 曾抓 action_skipped
   词表漂移），规则与词表要同步英文化，防 lint 假红/假绿。
3. **事件日志兼容**：已落盘 run 的中文 payload 是历史数据，读路径不得假设英文；
   只改**新写入**的默认串，勿动旧数据或做迁移。

建议排期：与 M14 门面批合并（同是全仓扫改性质），fresh-clone E2E 门跑绿后一次
regression 收口。审查方将在下一轮对"中文残留 grep 清单"做复查。

—— 红队
