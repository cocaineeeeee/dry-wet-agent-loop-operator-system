From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（OS3：内核作为操作系统的整体对账——原则宪法/权限矩阵/资源 quota/故障隔离/入口面）

## 先说最硬的正面认定

truth 隔离是**四重结构强制**（写者唯一 loop / 读者唯一 eval / 无人 import eval /
ReadOnlyRunView 无字段）——全内核最像 MMU 保护模式的地方；events.jsonl 崩溃一致性
体系是产品级。**这两块已经敢承重**，配得上"实验 OS"。

## 一、[P1 系统性] 物化视图零故障隔离——单坏 obs 文件 = 全 run DoS（实测）

四个探针实锤：一个非 UTF-8 或坏 JSON 的 obs 文件使 status/verdicts/UI/export_view
**以及 --resume 写者路径**全部 exit 1 裸 traceback（UnicodeDecodeError/ValidationError,
非 ExposError, 无定位无自愈指引）；而 **expos check 对此报 clean exit 0 全盲**。
根源=投资倾斜：日志有 torn-tail 三方同谓词+自愈+四档退出码，observations/（每轮被读
几十次的东西）零 quarantine 零诊断。修法方向：list_observations 单文件解析失败改
"隔离该文件+响亮列名+继续"（quarantine 语义），expos check 扩展到物化视图层。
这也是 PLUGIN_API 前置（坏插件写一个坏 obs 即可 DoS 全 run）。

## 二、权限矩阵四个格子（低危但措辞/前置要紧）

① eval 无锁直写 report/（四处 write_text）——与 CAPABILITY_MODEL "eval 只读"叙述
不符，建议登记能力位或由 loop 收口物化；② **check --fix 不取 writer.lock**——
"override 是唯一 CLI 写通道"已不成立，truncate 前应取锁、取不到即拒（与 loop 同协议）；
③ **RunStore 写方法是裸公有 API**——隔离靠"可信持句柄者"非 default-deny 闸门，
CAPABILITY_UPPER_BOUND 落地前 PLUGIN_API 的 default-deny 是纸面的（插件头号前置）；
④ agent/policy.py 持全量句柄且不在"agent 无写"守门覆盖内——§11 措辞应区分
"推理层（结构无写）"与"装配 shim（可信持句柄）"。

## 三、资源 quota：事件日志是记账最薄弱资源

无 rotation、无增量游标、每轮 **24 处** full-scan 读调用点（grep 实证）→ O(R²) 读放大
——与 IDX3 的 M-2 同族，同一"内存态增量维护"修法受益；human-only 提案
（GOAL_TRANSLATION/PRIOR_PROPOSAL）永不自动裁定、无 human 时在日志无限堆积并每轮
被重扫——建议加"未决上限+超限告警"或终轮批量 reject 留痕（与 R2 U-3 同源）。
孔预算记账是最成熟的（正面）。

## 四、给 PLUGIN_API 落地的三前置（"敢让别人的插件跑上来"判据）

能力位闸门先于句柄下放（格子③）→ StageContext/PlateContext 句柄泄漏审计（守卫从
"日志层过滤署名"升级"句柄级 default-deny"）→ 物化视图故障隔离补齐（§一）。

全文+探针：/tmp/claude-1128/dimos3/。域隔离正面认定：run_cell 进程级隔离良好；
compare 单进程 fail-fast 非 fail-isolated（可辩护，点明即可）。

—— 红队
