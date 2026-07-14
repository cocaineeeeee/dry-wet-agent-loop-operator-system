From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **4 attribution 红全清——诊出真 latent bug（非测试漂移），你那个 F601 是它的面包屑**

## 根因：CAUSES 拼写笔误——glare 写了两遍、dust_contamination 整个缺失

`CAUSES` 元组把 `"glare"` 列了**两次**、**完全漏了 dust_contamination**，
`_REMEDY` 也无 dust_contamination key。一个笔误产生全部 3 症状：
- **dust**：dust_contamination 不在 CAUSES → 永不进 order → dusted 孔
  top_cause 恒 None（**非 §2.7 真 inconclusive，是结构性不可排名**）；
- **glare**：glare 在 CAUSES 重复 → order[0]==order[1]==glare → p_top−
  p_second==0<MARGIN(0.15) → 每孔被机械强制 inconclusive；
- **propose_action**：_REMEDY[dust_contamination] → KeyError。

**你的 F601"重复 glare key"正是这个笔误的面包屑**——_REMEDY 里那条重复
glare 是同一笔误的另一处，你删它零变（对），但 CAUSES 里"glare 顶替
dust"的底层 bug 比它更早、才是 4 红真因。CI 首跑+你的 lint 清理合起来
把这个潜伏 bug 逼出水面——公开发布的意外之得。

## 修（attribution.py 净 2 行，权威规范对齐）

CAUSES 第二个 glare→dust_contamination；_REMEDY 加
`"dust_contamination": (REPEAT_CANDIDATE, "addition", {})`——对齐
docs/M6_DESIGN §2.5 与 ARCHITECTURE（dust→REPEAT_CANDIDATE/addition/
"同条件另孔"）。判别证明：dusted→dust（≥70% 非 None）∧ clean 不误指
（FPR 界+每孔≤1 阳性假设仍成立，dust 不漏进干净板）。

## 复验

test_attribution **25 passed**（3 原红 + 22 原绿全绿）；k_b/m20 回归绿；
lint+ruff 绿；grandfathered 域字面量注释保留。**4 红全清**（我修 1
raw-substring + agent 修 1 笔误连解 3）——test_attribution 杂务账
彻底销账。full job 周定时到此项转绿。

诊断纪律照守：先诊哪侧错、不翻断言充绿——结果是真 bug 修 attribution
逻辑而非改测试。合跑照旧候你 yaml 两行。
