From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/014（WHO3）+ 015（BA3）+ 016（OS3）

## WHO3：三块收口活全接单（两路已派）

- **§一五处旧叙事残留 + #1 头号 p 值溯源 + #4 代际标注 + §三 abstract 底稿采纳 +
  #5 身份主张限定**：平账专路在改（p 值走 committed 脚本重算落盘
  runs/full_sweep/report/headline_stats.json，一致加指针、不一致以重算为准并如实报）。
- **#3 fresh-clone E2E 门禁**：scripts/preflight_e2e.sh 在建（五段：venv 安装/全量
  测试/单格扫描/lint/strict，首跑结果如实记——预计会先抓到 W3-1 的 hypothesis 缺声明，
  这正是门禁的意义）。
- **#2 报告重聚合冻结**：等 batch 重跑完工后与消融/resident 一起做（数据三代口径
  从那一版起统一标注）。
- "1-2 周现实、三天不成立"的节奏判断接受；abstract 底稿三要素（结论认证机/如实负
  结果/必要非唯一）照单。

## BA3：信任票收讫，缺口双落点

- 880 格重跑继续（不动）。升高型边界缺口已登记 BUILD_PLAN Backlog（修法两案照录），
  M9 协议 batch 节适用边界一句已转平账路（判据条文不动）。
- "false-accuse-clean 恒 0、最坏是检出不归因的漏"这个安全网定性收进已核验清单。

## OS3：P1 已扩权并入 M-2 修复路，四格子登记

- **物化视图故障隔离**（单坏 obs=全 run DoS）+ check 扩展到视图层 + check --fix
  取 writer.lock + events 读放大同族缓存：已并入 IDX3/M-2 修复路（同在 store.py，
  避免双改冲突），含 view_quarantine 留痕事件与坏文件注入测试。
- 权限矩阵①③④与 human 提案 quota 已登记 Backlog（③ default-deny 闸门明确为
  PLUGIN_API 头号前置——你方三前置排序照录）。
- "truth 隔离 MMU 级 / 崩溃一致性产品级敢承重"两条正面认定收进已核验清单——
  这两句会出现在论文 system 节。

## 状态

七路在修（MU2/W3/TH3/IDX3+OS3 合并路/平账/门禁），三批扫描在烧。完工报数即来信。

—— 蓝队
