From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（W3 新人实操复跑完成）

## 好消息先说

- README cmd#1（31s EXIT 0）、CLI 七命令+六条错误路径、make_demo 三幕（act3 双路径伪造
  均被拒）、mkdocs --strict 全部实跑通过。
- **你方修的两条确定性红 + 3 个顺序 flaky 在最新树全转绿**——fixture 齐备时全量
  543 passed / 1 skip / 0 真红（32m34s 实跑，log 在 /tmp/claude-1128/dimw3/）。

## 三条高优先断点（新人/CI 视角）

1. **hypothesis 未声明（W3-1）**：dev extra 只有 pytest；5 个测试文件裸 import hypothesis
   （无 importorskip）——干净环境照 CONTRIBUTING 走 `pip install -e ".[dev]"` 后
   `pytest -q` 收集阶段即 Interrupted×4；**CI 两作业同样不装 hypothesis，出厂 CI full
   在干净 runner 上跑不过**（现在没炸是 runner 预装掩盖）。修：dev extra 补 hypothesis；
   streamlit 的 find_spec+skipif 是正确范本，照抄兜底。
2. **pre-commit 出厂红（W3-2，R2 遗留未修）**：ruff format/check 要改 91 文件 +
   codespell exit 65（FPR/HTE/Mater/abl 等域缩写未入 ignore-words-list）。维护者收敛
   一次并提交即可。
3. **测试 fixture 落在被 gitignore 的 runs/ 下（W3-3，结构性）**：test_compare×4 +
   test_gen_ablation_manifest 读 runs/full_sweep/scenarios 与 _tools——fresh clone
   场景下这 5 条必红（除非 git add -f 强跟踪）。修：迁 tests/fixtures/ 或 .gitignore
   加 ! 例外并确认跟踪态。这也解释了 MU/MU2 副本里的 4 条 FileNotFoundError。

## 中低优先（清单在报告）

make_demo act3 audit_store 跨跑累积破幂等承诺（证据文件逐跑变脏，卖点文件应逐字节
可复现）；mkdocs/mkdocs-material 无 extra 声明；CONTRIBUTING 需补"非 git 仓先 git init +
pre-commit 需先安装"；run/status 的 Rounds 分母口径不一；inspect events 无分页且
BrokenPipe 退 120。完整七条修复清单：/tmp/claude-1128/dimw3/。

—— 红队
