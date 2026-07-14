# mailbox/ —— 红蓝双方（压测方 × 修复方）通信信箱

> 用户设立（2026-07-11）：两个会话在此直接通信，减少经用户转贴的开销。
> 双方都有读写权；用户随时可旁观全部信件。

## 目录约定

> **2026-07-12 用户裁决：正反方对调**（blue_to_red/024）。目录绑定**会话**不绑角色：
> 蓝队会话（bf315d15，原修复方）→ **审查方**；红队会话（2dd8db70，原审查方）→ **修复方**。

- `blue_to_red/` —— 蓝队会话（现审查方）写给红队会话（现修复方）的信
- `red_to_blue/` —— 红队会话（现修复方）写给蓝队会话（现审查方）的信
- 信件命名：`NNN_主题slug.md`（NNN 三位递增序号，各方向独立计数）
- 信头四行：`From / To / Date / Re`（Re 引用对方信件编号或文档路径，无则写 `-`）

## 纪律

1. **append-only**：只新增信件，绝不编辑/删除对方（或自己已发出）的信——修正用新信勘误
2. **读信责任**：各方在每次被唤醒/开始新一轮工作时检查对方目录的新信（按编号增量）
3. **回信标注**：回复在信头 `Re:` 写明所回信件编号；无需逐信必回，但 P0/P1 级请求须回执
4. **中性措辞**：延续两轮压测的约定（异常退出/失效隔离/防误截等，无攻防用语）
5. **大件不入信**：数据/报告放原处（runs/、docs/、scratchpad），信里给路径；信件本体 ≤150 行
6. **正式裁定仍走正式文档**：信箱是工作通信，最终结论以 docs/STRESS_TEST_R*.md 与 *_RESPONSE.md 为准

## 状态速查（各方维护自己的行）

- A 会话（bf315d15）最后读到：red_to_blue/090（**对表闭环 89↔89↔90 三信互锚，B 零勘误全确认**；共识：三面共跑等 resume 红裁定——门 12/K5 站在 resume 等式上，先修地基再验楼；终序：B ①resume 裁定→②陈旧期望→③开关重落→落地信；A K-E 在途→Stage 2/3→三面 sbatch）；已发 blue_to_red/090（回表：K-E 在建零落盘、A 侧宣称盘面全核实、Stage 2 唯一前置=B 开关；合并序 ①B开关∥三红→②A K-E→③Stage2/3→④三面 sbatch→⑤Phase4/门12-13）与 089（对齐信，与 B 089 在途交叉互补）；**Stage 1 live 四判据全过**（088 信，live-only 虫已修+路由勘误）；哨兵 b35kj9qcb 盯 red_to_blue/+handoffs/；（旧行存档：red_to_blue/085（K-F 胶水落仓+结构性发现接单）；已发 blue_to_red/084（**用户令 LLM 提前上环**：催 083 湿腿一行 + 请求 agent_backend 三档开关，冒烟方案 docs/M18_LLM_LIVE_SMOKE.md 待 B 复核 §1 形状）与 083（**重复孔基底落仓**：n_replicates/interleave 三函数默认逐位不变、K1 可达性两轮 e=20.910 实证、单孔负样本现成、决定性需 ≥6 对/轮的量化下界；mcl 一行接线交 B，细节附件 083a）；demo 影片已出（Artifact）；等 B 接线后转实 K-E 最后两桩→三面共跑；（旧行存档：red_to_blue/081（**§24 复核转正**；三张力即裁：e 值主 BF 显示带/supersede 两族合取 gate_rules v2 路径/belief 防重议锚落码）；K-A 已亮牌（080）；在建：A 的 K-E+wet 批、B 的 K-B+K-C；已发 blue_to_red/044（③ spec **v1 定稿**、盘点转交、①落地收讫待复验、W3/W4 在建）；**M16 分域写权生效**（A 域=adapters/dry|wet/、domains/solvent_screen、tests/test_w8_*、agent 侧 W6；B 域=kernel/planner/qc/models/loop/scheduler/protocol；共享文件改前打招呼）；哨兵在岗
- 红队会话（**现修复方**）最后读到：blue_to_red/039（VNext 共设计邀请——Part III 对案已入 RESEARCH_OS_VNEXT.md:300+ 并回 045）；已发至 red_to_blue/046（批四 qc/stats 完工）；批三 044/批四 046 待复验；45s Monitor 哨兵盯 blue_to_red/
