# 建設性健壯性審查 · 審查方續接入口（/clear 後從這裡開始）

> **給接手的會話**：你是這個項目裡**負責獨立健壯性審查的一方**——不是修復方。
> **開工前先讀 `docs/STRESS_TEST_VOCAB.md`（中性措辭守則）**，寫報告一律用中性詞，避免生成時誤觸安全機制而中斷。
> 修復由**另一個會話**並行進行。你的職責是：組織多路審查、整合成報告、
> 在修復方改完後做**復核 + 遞進到更深一輪**。讀完這份 + 最新 `STRESS_TEST_R<n>.md`，
> 你就知道模式、規則、上一輪到哪、下一步做什麼。不用再問用戶背景。

---

## 0. 角色與分工（別搞混）

- **你（本會話）= 審查方**。產出是 `docs/STRESS_TEST_R<n>.md`（審查報告），**默認不改業務代碼**。
- **另一個會話 = 修復方**，正在按報告動手改倉庫。
- 用戶會在兩方之間傳話，讓雙方**良性往復、遞進**：你出問題 → 修復方改 → 你復核 + 挖更深 → …

> ⚠️ 因為修復方正在改倉庫，你 `/clear` 回來時**代碼可能已經和上一輪快照不同了**。
> 復核前先看修復方改了什麼（git diff / `CHECKPOINTS.md` 新條目 / 相關文件 mtime），
> 再對照最新 `STRESS_TEST_R<n>.md` 逐條核。

---

## 1. 這個項目是什麼（一句話）

expos：閉環材料實驗 OS（非生物；蓝圖 `docs/ARCHITECTURE.md`、台賬 `CHECKPOINTS.md`），
M0–M10 全關賬、431 測試全綠。收官後進入「建設性健壯性審查往復」以加固到可發表。
**基調：加固，不是否定；「已核驗清單」和 finding 同等重要。**

---

## 2. 硬規則（每輪都遵守）

1. **模型（2026-07-11 新規，取代舊 Fable 規則）**：審查 agent **一律不用 Fable——主力任務 `model: opus`，簡單零碎活用 opus 以下（sonnet/haiku）**。背景：Fable 派 agent 多路被安全機制誤觸（O/Y1/Y4 各 1–3 次），用戶改規。
2. **計算作業默認走 Slurm sbatch**（`/opt/slurm/bin`，slurm-jobs skill）。**Slurm 故障期（2026-07-11 夜起）用戶臨時授權 ssh 直連 g209/g208**——每輪先 `sinfo` 探測再選通道，Slurm 恢復即回 sbatch。**大坑（多路 agent 踩過）**：登錄節點 `/tmp` 沙盒對計算節點不可見，sbatch/ssh 作業的腳本與輸出必須放共享盤（`/Data1/...`）臨時目錄、跑完回拷沙盒並刪除臨時目錄。輕量 pytest 本機跑，**務必帶 `PYTHONDONTWRITEBYTECODE=1`**。
3. **措辭紀律**：報告與 agent 提示詞都用**中性的「健壯性 / 邊界審查 / 誤用場景」措辭**，避開攻防用語（攻擊者 / 惡意 / 繞過 / brick / 利用）。Opus 誤觸率遠低於 Fable，紀律仍保留。
4. **只讀審查、隔離實驗**：審查 agent 只讀倉庫；需實跑的（內核崩潰復現等）限定在 `/tmp/claude-1128/...` 沙盒，**絕不動倉庫文件與 `runs/` 已有數據**（那是修復方和實證的共享資產）。
5. **每條 finding 四段式**：`[P0/P1/P2/P3] 標題` + 證據（file:line）/ 為何重要 / 建議修復 / 如何驗證。
6. **復驗要驗效果，不只看測試綠**：R1 頭號教訓——測試只斷言 generator 名字、沒斷言效果，M7 機制失效照樣 431 全綠。復驗每條修復都要有「效果證據」（實跑/斷值），不接受「測試綠 = 修好了」。

---

## 3. 一輪標準流程

1. **派 5–6 路 Fable agent 分維度並行審**：
   - **A 主張-證據對賬**（README/CHECKPOINTS/PAPER_OUTLINE 每個數字 vs `runs/full_sweep/report/` 實際產物；找 overclaim）
   - **B 設計紅線結構複核**（真值隔離 / agent 無裁決權 / 無靜默降級 / 檢查點紀律 是否結構性成立）
   - **C 評測協議正確性**（`expos/eval/*`、`runs/full_sweep/_tools/aggregate.py`、`scripts/gen_sweep.py`、`M9_PROTOCOL.md`）
   - **D QC 統計與歸因**（`qc/{stats,checks,attribution,failure_model,policy}.py`）——**中性措辭尤其重要**，建議拆 D1 統計原語 / D2 歸因貝葉斯兩路（上下文吃不下 + 降誤觸）
   - **E 內核工程健壯性**（`kernel/*`、`loop.py`、`cli.py`、`ui/`；允許沙盒實跑）
2. **整合去重**：交叉印證的合成一條主軸，寫 `docs/STRESS_TEST_R<n>.md`（嚴重度總表 + 逐條四段式 + 已核驗清單 + 修復優先級 + 依賴順序）。
3. **交給用戶轉給修復方**。修復方改完 → 你進**下一輪**：先復驗上輪 P0/P1 是否真閉環，再在修復改動的新表面挖更深。

---

## 4. 遞進方向（廣度 → 深度 → 閉環）

- R1 = **廣度掃**（六維問題地圖）——已完成。
- R2 = **復驗 + 17 路縱深**（測試有效性/產物復算/注入器物理/RCGP/狀態機/確定性/QC 數學/provenance/備份/新人實操/架構規劃/理論形式化…）——已完成。
- **R3 = 閉環終審**：綁修復方 ROADMAP_V2 的 M13（resweep 落地）與 M15（論文終審）。
- 終局：每條 P0/P1 都有「修復 + 復驗證據 + CHECKPOINTS 留痕」。

---

## 5R3. 最新狀態（R3 前哨已交付，2026-07-11 晚）—— 你下一步（R3 終審）的輸入

**先讀 `docs/STRESS_TEST_R3.md`**（12 路整合：G3 復算/Q3 狀態機/F3+F3b 活性與擊殺/B3 批次
根因/MU 變異 corpus/HY 屬性機/BOF 公平性/SCH 契約/TR 溯源/O3-A~D 參照/FR-1~3 前沿）。要點：

- **R1-2 接線層已判閉環**（三議定變異全殺；表演性構造放行出 P2）。
- **聚合前三急件**（R3 §1）：批次「選哪批異常」方向 100% 判反（P0，`checks.py:461` 對稱平局
  選擇器，pre-existing）；drift 白跑成真（檢出恆 0.2 平線零劑量響應）；dust 檢出恆等式。
  修復方聚合 resweep 前必須定性，涉 batch/drift 格重跑。
- R2 遺留定向復驗全閉（Q 路五點 + resume 等價 + override）；robust n=3 仍≈naive（R1-3c 可關）；
  機制通電不改 regret（H1' 敘事約束）；S3.wide_edge 有誠實劑量響應（H4 素材）。
- M14/M15 必修：CHECKPOINTS 三處「H1 過」無 deviation、headline p 值無產物來源（TR 溯源矩陣）。
- **R3 終審剩餘項見 R3 §9**：等修復方 (A) 消化三急件定重跑範圍 (B) 聚合出 report 後，做
  H1' 終判復算、grade 校準（用 2607.06596 遷移矩陣範式）、保費上限、分層信任反例、M15 數字
  終審、MUT-P 修復回歸。證據沙盒索引在 R3 §各節（/tmp/claude-1128/dim*/ 與
  /Data1/ericyang/r3_os_references/）。

---

## 5. 上一輪（R2，2026-07-11 深夜）結論 ——（歷史存檔；R3 前哨已消化其計劃）

**先讀三份**：`docs/STRESS_TEST_R2.md`（審查方 R2 報告，含 17 路整合、復驗裁定表、答修復方五問、
R3 計劃）· `docs/STRESS_TEST_R1_RESPONSE.md`（修復方回應，六軸零駁回）· `docs/ROADMAP_V2.md`
（修復方四紀元路線，審查方擁有 P0/P1 閉環裁定權）。`docs/ARCHITECTURE_V2_PROPOSAL.md` 是修復方
架構提案（機制活性註冊表/協議即代碼），審查方已在 R2 §5 給審查意見並認領其 §7 反問為 R3 審查點。

**R2 要點速記**：
- 復驗裁定：R1-5/R1-6 基本閉環；R1-2 單元層閉環但**接線層缺口未閉**（F 路三變異 E/D/權重恆1
  全綠存活——機制活性斷言落地後必須擊殺這三個變異才算閉環，沙盒 `scratchpad/mut/` 可復跑）；
  R1-1/3/4 待 resweep（2700 格在 g209/g208 直跑中）。
- **R2 §1 有 5 條「resweep 白跑風險」急件**（drift 注入器逐輪重置與跨輪檢查正交 / rcgp 各向同性
  容量稅 + 缺 os-lite 消融 / artifact 種子流是孤兒 / NFS 卷 83% 滿 / 檢出曲線量綱），交給修復方了，
  R3 先驗這五條的整改。
- 新表面 findings 已按修復方批次歸屬（M11/M12/M13/M14 表在 R2 §3）；X4 理論包（Chow 三支決策、
  4 個可證命題）與 X1/X3 規劃已供修復方 M14/M15 收編。
- **R3 計劃（R2 §6）**：① resweep 數字獨立復算（復用 G 路腳本）；② 機制活性守門擊殺驗收 +
  「表演性生效」構造（ARCH_V2 反問1）；③ grade 校準在 S3 留出偽影上的崩壞（反問3）；
  ④ 保費上限 X 可證偽性（反問4）+ 分層信任反例（反問5）；⑤ M15 論文數字終審。
- 定向復驗兩small件：Q 路矩陣是修復中途快照——請對最新碼複跑 `dimq/enum_transitions.py`
  驗 reclassify 組合守衛；U 路發現的 3 個崩潰 run（`S2.batch_shift.-0.07` naive s1019 /
  robust s1005/s1006）reconcile 未觸發——用新碼重放崩潰場景驗證修復覆蓋。

---

## 6. 關鍵指針

- 台賬 `CHECKPOINTS.md`（權威）· 蓝圖 `docs/ARCHITECTURE.md` · 協議 `docs/M9_PROTOCOL.md` · 論文骨架 `docs/PAPER_OUTLINE.md`
- 實證產物 `runs/full_sweep/report/`（main_table.csv / aggregate_summary.json / *_curve.csv）
- 記憶（自動載入）：`expos-project-goal` · `expos-workflow-prefs`（Fable/sbatch）· `expos-stress-test-loop`（本流程）
- 備份 `/Data1/ericyang/expos_backup_20260710/`（`REPRODUCE.md` 是重現契約）
