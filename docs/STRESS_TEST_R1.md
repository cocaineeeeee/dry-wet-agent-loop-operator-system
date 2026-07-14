# 建設性健壯性審查 · 第一輪整合報告（R1）

> 2026-07-11。六路並行代碼審查員複核 M0–M10 收官態，主對話整合去重。
> 目標：建設性——每條 finding 給「證據 / 為何重要 / 修復 / 驗證」，供修復方直接動手。
> 六路維度：A 主張-證據對賬 · B 設計紅線結構複核 · C 評測協議 · D1 QC 統計 · D2 歸因/貝葉斯 · E 內核健壯性。
>
> **一句話總判**：數字抄寫層與真值隔離幾乎無懈可擊（B 的 14 條邊界路徑全被守住、A 的 12 項複核站得住、
> 備份可校驗、431 測試屬實）。缺口集中在三處**互相掩護的結構性問題**：
> ① M7「失敗感知規劃」的核心機制在生產鏈路上**實際失效**（風險折扣恆零、風險圖恆常數、drift 檢查從未接線）；
> ② 評測協議有**系統性偏向 os 敘事**的口徑問題（A/B 分離未被聚合器遵守、污染分母不公、robust 對照被削弱、統計檢驗零實現）；
> ③ 推論層**過度聲稱**（H1 假設替換、旗艦數字失效、單格代表全掃描）。
> 三者彼此掩護：沒掃 drift 場景 → drift 檢查沒接線沒暴露；規劃機制失效但 regret 對比又偏向 os → 沒人追問 M7 到底有沒有生效。

---

## 嚴重度總表（去重後 6 P0/P1 主軸 + 15 P2 + 14 P3）

| # | 級別 | 主軸 | 來源 | 一句話 |
|---|---|---|---|---|
| R1-1 | **P0** | H1 主假設判定被替換 | A | 預註冊場景集上 os regret 全面劣於 robust（13/13 反向）、S4 未跑、置換檢驗從未做；「H1 過」引用的全是不在預註冊集內的 S0.demo |
| R1-2 | **P1** | M7 規劃機制生產態失效（三合一） | D2×2 + D1 | 風險折扣讀錯鍵恆零 + 風險圖傳 None 恆空桶恆常數 + drift 檢查 ewma/cusum 從未接線 → failure-aware 臂與普通 UCB 逐位相同 |
| R1-3 | **P1** | 評測協議系統性偏向 os | C×4 + A | A/B 分離未被聚合器遵守 + 污染分母用 raw 非訓練集 + robust 對照弱於凍結規格（n=2 退化非 3+Huber）+ bootstrap/置換檢驗零實現 |
| R1-4 | **P1** | 場景覆蓋缺口被沉默掩蓋 | A + C + D1 | 六注入器基準實際只有 4 個；drift/dust 無單伪影證據、S3 留出伪影 / S4 組合全缺；H2/H4 無數據可裁但收官宣稱三主張全成立 |
| R1-5 | **P1** | 崩潰一致性三硬傷 | E×3（實證） | torn-tail 與 seq 恢復互斥把正常崩潰後的 run 讀成不可讀 + checkpoint 落後重做非幂等雙計訓練集 + resume 不等價且指紋對超參盲 |
| R1-6 | **P1** | 旗艦數字與門面過度聲稱 | A×3 | best=1.007 現行代碼失效（實測 0.9751）+ batch−0.18 os 污染反超 naive + README 單格當 1450 格掃描代表 |

---

## R1-1 【P0】H1 主假設判定被替換（來源 A）

**證據**：`docs/M9_PROTOCOL.md:221-222` 預註冊 H1 =「結構性場景（S2 edge/gradient/batch 中高檔、**S4 結構疊加**）os 的 regret 與污染利用率**顯著優於** robust-blind（**置換檢驗 p<0.05**）」。實測 `main_table.csv`：S2 edge/batch/thermal **13 個檔位 os regret 全部劣於 robust**（edge 0.2：os 0.0239 vs robust 0.0067；edge 0.35：0.0429 vs 0.0057；thermal 0.3：0.0295 vs 0.0122；batch −0.18：0.0212 vs 0.0072）；S4 未跑（aggregate_summary.json 只有 S0/S1/S2）；全 report grep 無任何 permutation/p 值。`CHECKPOINTS.md:111`「H1 過」引用的全是 **S0.demo**（不在 H1 預註冊集內）。

**為何重要**：這是論文唯一可證偽主張的判定。按跑前凍結的判據，H1 的 regret 半邊在其自己指定的場景集上**方向反了**。宣稱「H1 過」而用 S0.demo 頂替預註冊場景集、且從未做顯著性檢驗——正是預註冊制度要防的事，也是審稿人第一個會查的地方。

**修復**：CHECKPOINTS/README/PAPER_OUTLINE 每處「H1」都改寫為場景集限定 + 統計狀態：「H1 在 S0.demo 上以定向指標（假最優/污染）成立；在預註冊 S2 中高檔上 regret 半邊**不成立**（與『regret-污染解耦』finding 合併為誠實負結果）；S4 未跑、置換檢驗未做——H1 判定降級為部分成立/待補」。或正式修訂假設並標「判據跑後修訂」（違反凍結，需如實記 deviation）。

**驗證**：grep「H1」每處都帶場景集限定與檢驗狀態；補跑置換檢驗後 report/ 有 p 值產物。

---

## R1-2 【P1】M7 規劃機制在生產鏈路上實際失效（三合一，來源 D2×2 + D1）

這是本輪最隱蔽、影響最深的一條：M7「失敗感知規劃器」是項目賣點之一，但它的三個核心機制在生產接線裡**都沒真正工作**，而測試只斷言了 generator 的名字、沒斷言效果，所以一路綠燈掩護到收官。

**(a) 風險折扣恆為零**：`planner/policy.py:326` `p_bar = fm.summary().get("global_rate", 0.0)`，但 `summary()` 只產 `"p_global"` 鍵（`failure_model.py:240-247`）→ `.get` 默認 0.0 → `discounted_scores(scores, 0.0)` 折扣因子恆 1 → **failure_aware 臂與普通 UCB 臂逐比特相同**。

**(b) 風險圖恆常數 0**：`planner/policy.py:157-159` 調 `fm.p_artifact_optimistic(..., solution_batch=None)`，而 `Bucket(solution_batch=None)` 與真實桶（`"R{r}-B{k}"`）永不相等 → `_agg_full` 恆 `(0,0)` → 每孔返回同一先驗下界（默認 clip 到 0）→ **風險避讓布局完全惰性**。被測試覆蓋的正確路徑 `FailureModel.risk_map` 在生產接線裡是死代碼。

**(c) drift 檢查從未接線**：`qc/stats.py` 實現了 `ewma`/`cusum`（文檔多處聲稱「跨輪 EWMA/CUSUM 累積解決漂移」），全倉庫 grep **零調用方**；`checks.py:652-655` 漂移檢查 score 恆 0。instrument_drift 伪影在任何輪都不會被 QC 打分。

**為何重要**：三條合起來，M7 的「風險感知」在生產態基本是空殼。這直接關聯 R1-3 的 regret 對比——如果 failure_aware 臂其實等於普通 UCB，那麼「os vs robust」對比裡 os 臂的規劃增量根本沒生效，regret 反輸 robust 就有了機制解釋。

**修復**：(a) 改 `fm.summary()["p_global"]`（KeyError 響亮失敗，不用 `.get` 兜底）；(b) 給 `p_artifact_optimistic` 加 `solution_batch=None → _agg_batch_marginal` 分支，或 planner 直接用 `fm.risk_map(layout, hint=None)`；(c) 決策 drift 是接線 EWMA/CUSUM 跨輪累積，還是誠實刪掉「跨輪解決漂移」的文檔聲稱並把 ewma/cusum 標為未接線。

**驗證**：構造 k/n 非零的 FailureModel 跑 `_generate(..., "response_gp+ucb+risk_discount")`，斷言候選評分 ≠ 無折扣臂；復用 `test_risk_map_edge_higher_than_center` 走 `_plate_risk_map` 斷言邊緣孔 > 中心孔（現狀失敗：兩者相等）。

---

## R1-3 【P1】評測協議系統性偏向 os 敘事（來源 C×4 + A）

四刀方向清一色有利於 os，正是對抗審稿人會集中複核的地方：

**(a) A/B 標定/評估分離未被聚合器遵守**：`gen_sweep.py:74-93` 按幅度檔奇偶分 A（seed 0-9）/B（seed 1000-1019）；`aggregate.py:42-57` 讀入全部 cells 無 `seed_set` 過濾 → 主表/檢出曲線/預註冊判據**混用 A/B**。實錘：`detection_curve.csv` 中 n=10（A）與 n=20（B）沿幅度軸交替；os-soft 預註冊三檔中 `edge0.15` 是標定集 A。違反 M9_PROTOCOL §2「B 為全部報數來源、A 鎖定後凍結絕不回標」。

**(b) 污染率分母是 raw 觀測而非各臂實際訓練集**：`scoring.py:153-161` 訓練集 = 累積 raw TRUSTED 觀測；但 robust 臂模型實際訓練於中位聚合後的合成觀測、os-soft 額外含 QUARANTINE 降權觀測。後果：robust≈naive 被度量口徑**保證**成立（非實驗證明）；os-soft 實際消費污染隔離觀測但污染率報得與 os 硬隔離逐位相同。核心對比指標當前口徑下**無法反駁主張**。

**(c) robust-blind 臂弱於凍結規格**：`M9_PROTOCOL:39-48` 明訂「副本升 3 + median+Huber(δ=1.345·MAD) IRLS + alpha=max(noise_sd², s²/r)」；實際 19 個 scenario yaml **全部 replicates:2**，`MedianAggregation` 無 Huber、無 alpha，n=2 走保守選。跑的是協議自己承認「對孤立伪影零保護」的退化模式——對照臂上限被人為壓低，H1 達成被放水。

**(d) 預註冊統計分析零實現**：協議要求「N≥20 + 效應量 + bootstrap 95%CI + 置換檢驗」，`aggregate.py:76-82` 只算 mean+pstdev，全倉無 bootstrap/permutation。實例：os-soft 預註冊 edge0.1 上 os-soft 0.01054 vs os 0.01087，差 0.0003 < SEM(0.0021) 的 1/6——任何方向都是噪聲。A 檔 n=10 還低於協議 N≥20 下限。19 場景×5 臂×≥5 指標無多重比較校正。

**為何重要**：(a)(b)(c) 都直接削弱「os 增量價值不可被穩健統計替代」這一唯一主張的可信度，方向全部有利 os；(d) 讓「顯著」二字無從談起。

**修復**：(a) `aggregate.py` 按 `cells.tsv` 的 `seed_set` 列拆分，主表/曲線/判據只用 B；(b) scoring 增「有效訓練集」口徑，復用各臂 `aggregation.prepare` 重放後逐條算污染，雙列報 raw/有效；(c) 升 replicates=3 + 補 Huber IRLS+alpha 重跑 robust，或協議顯式改版聲明 n=2 退化語義（記 deviation）；(d) aggregate 加配對置換檢驗（同 seed 配對，B 集內）+ BCa bootstrap CI + Holm 校正，判據輸出 p 值與效應量。

**驗證**：修復後 detection_curve 所有行 n 一致且 seed∈[1000,1020)；單測「兩副本一污染」→ robust 有效污染率 < naive；3 副本 (clean,clean,contaminated)→MedianAggregation 輸出 clean；S1 零伪影跑臂間置換檢驗假陽性率 ≈ 名義 α。

---

## R1-4 【P1】場景覆蓋缺口被沉默掩蓋（來源 A + C + D1 三方獨立命中）

**證據**：`runs/full_sweep/scenarios/` 僅 edge/batch/glare/thermal 四族 17 個 S2 場景；`grep dust_nucleation` 零命中；instrument_drift 僅在複合 S0.demo 出現無單伪影檔。M9_PROTOCOL §2 承諾六注入器×5 + S3 留出伪影×5 + S4 組合×4 = 41 配置；`aggregate_summary.json` 僅 19 場景。BUILD_PLAN M9 驗收線明文要求「留出伪影行為必報」，H2（含 dust、低檔 drift）/H4（S3）兩條預註冊假設**無數據可裁**，而 `CHECKPOINTS:26` 只寫「H1/H3 過」對 H2/H4 未跑一字未提，收官條目宣稱三主張全成立。

**為何重要**：主張一（結構化偏差注入基準）賣點就是六注入器 + 檢出邊界表，現有證據只支撐「四注入器基準」。H4（留出伪影兜底）是回應「QC 對未見類型失效」這一最常見審稿質疑的**唯一**實驗，缺席會被直接命中。與 R1-2(c) 互相掩護：沒掃 drift 場景，所以 drift 檢查沒接線這件事沒暴露。

**修復**：CHECKPOINTS M9 補記顯式「未跑聲明」（H2/H4 未評、S3/S4 未跑、drift/dust 無單伪影證據）與原因；PAPER_OUTLINE 待補實驗把這兩項從「待掃描」改「投稿前必補」；或補跑 drift/dust×幅度 + S3/S4 子掃描（走 Slurm sbatch）。

**驗證**：aggregate_summary.json 場景列表出現 drift/dust/S3/S4，或台賬有一條可 grep 的「未跑聲明」。

---

## R1-5 【P1】崩潰一致性三硬傷（來源 E，全部沙盒實證）

**(a) torn-tail 與 seq 恢復互斥，正常崩潰後的 run 變不可讀**：`store.py` 單寫者 append 只一次 write（`json+"\n"`）。崩潰恰丟尾換行（記錄字節已落、`\n` 未落）時：`_recover_next_seq`（104-115）末行能 json.loads → 計入 next_seq=last+1；`_heal_torn_tail`（117-130）文件不以 `\n` 結尾 → 截掉整條末行。兩者對「末行算不算數」判斷衝突 → resume 首次 append 製造 seq 空洞 → 此後 `read_events` 恆拋 `StoreError`（`user_facing=False`，exit 1 traceback），`status/inspect/verdicts/UI` 全部無法讀取。「崩潰偏斜保守」主張在此不成立。

**(b) checkpoint 落後重做非幂等，雙計訓練集**：`write_checkpoint` 先 append 事件後 atomic_write，崩於兩步間 checkpoint 落後一輪、resume 重做該輪。但 `run_loop` 重做每輪生成新 uuid exp_id、重新產觀測、無幂等去重。實測：resume 重做 round1 後該輪 47 孔各出現兩條觀測，後續 `list_observations(trust=TRUSTED)` 雙份喂進響應模型。`truth/` 那一路做了按輪幂等覆蓋，觀測/實驗/事件這一路沒有對應幂等。

**(c) resume 與一次跑完不等價（os 臂），且指紋對超參盲**：seed=11 crystal os 4 輪，一次跑 vs 2+2 resume，模型快照逐輪發散、best 不同。根因：`response_gp.py:292-302` `snapshot()` 只哈希 `direction|dim|"matern2.5"|(X,y)`，**不含擬合出的核超參**（length_scale/noise）→「訓練數據相同、擬合態不同」指紋相同。planner 狀態/budget/n_train 都相等，唯獨真正驅動選點的 GP 擬合態發散，審計層看不見。動搖三臂對比公平性根基（campaign 分段跑 ≠ 一次跑）。

**修復**：(a) `_heal_torn_tail` 判據與 `read_events` 統一——能解析末行則補 `\n` 不截，不能解析才截；或 append 前若文件非空且不以 `\n` 結尾先補一個 `\n`。(b) 重做某輪前先清該 round_id 的 experiments/observations，或以 round_id 為幂等鍵覆蓋、檢測到該輪已 CLOSED 即跳過。(c) `snapshot()` 納入 `self._gp.kernel_.theta` + alpha 模式標記；resume 重建後斷言重建指紋 == checkpoint 末輪 `model_updated` 指紋，不等響亮失敗。

**驗證**：(a) 重跑 torn-tail 場景斷言 heal 後 read_events 不拋且 seq 連續；(b) 故意 mid-round 中止再 resume，比對觀測數與板容量；(c) 加超參後 one-shot 與 split 全輪指紋應相等。

---

## R1-6 【P1】旗艦數字與門面過度聲稱（來源 A×3）

**(a) best=1.007 現行代碼已失效**：README:100 / PAPER_OUTLINE:25 / CHECKPOINTS:69 反覆引用「best=1.007 > 上限 1.0（假最優鐵證）」。磁盤唯一匹配 run `runs/m4_naive/`（seed=7 naive）實測全場最大 **0.9751**，188 條無一超 1.0。M6/M10 模擬器改動後該數字失效但文檔未更新，`make_demo.py:162` 無條件打印「越過上限 1.0」配 0.97X 實數。「超過物理上限」之所以是鐵證是因無需真值即自證，0.975 不具此性質。

**(b) batch−0.18 os 污染反超 naive**：`main_table.csv` S2.batch_shift.-0.18：os contaminated 0.419 vs naive 0.366、injected 0.722 vs 0.490，且同格檢出率 os 1.0（20/20）。打在 README:9「伪影數據在結構上不可能污染響應模型」的門面主張上。準確邊界是「被判 SUSPECT 的不入模」，漏檢/誤判 TRUSTED 照樣入模。

**(c) README 單格代表 1450 格掃描**：README:100「全量掃描（1450 格）：S0.demo regret os 0.0086 vs naive 0.0179」。逐場景核算 os regret < naive 僅 4/19（S0 + glare 三檔），edge/batch/thermal 全檔更差。掃描級真結論是「os 買的是污染防護與假最優拒斥（僅簽名匹配場景），regret 多數場景付費」。

**修復**：(a) 重跑 make_demo 取實數更新全文，或改用全掃描裡確實越限的 seed（3/20 存在），解說詞改條件生成；(b) README 電梯陳述改「被隔離觀測結構上不可能入響應模型」，誠實 finding 增至四個收錄 batch−0.18 反超及機理（複測再曝險）；(c) README 加掃描級總括句限定每個 headline 的場景範圍。

**驗證**：grep「1.007」全倉為零或帶歷史腳註；README 每個 headline 旁有場景範圍限定；新讀者只讀 README 能寫出「os 多數場景 regret 更差」。

---

## P2 清單（15 條，按來源）

- **[B] well_cost 無下界**（arbiter.py:223）：agent 提案 `n_wells` 負值可規避 30% 動作預算，餓死 BO 探索或響亮 LayoutError 廢掉一輪。修：`max(1,·)` + 拒負值。
- **[B] CLI override 死投遞**（cli.py:328）：投遞 overrides/pending/ 無消費者、不落 OVERRIDE 事件、`status` applied 恆 0，與 README「留審計事件」矛盾。人在環唯一改判通道是無聲空操作。修：接線消費端或誠實標「未接線」。
- **[B] QC 結構檢查 try/except 吞錯**（checks.py:341/374/452）：降級 record-only 靜默不判假，構成可誘導的假陰性。修：降級升格為顯式 QCCheck(passed=False)+flag。
- **[C] regret 的 f\* 每格不同**（scoring.py:127）：觀測真值併入 f\* 破壞跨臂配對，方向系統偏 os（探索更高真值點的臂反遭懲罰）。修：f\* 按場景一次性離線算 + 緩存。
- **[C] 檢出率把任意 QC 警報當檢出**（aggregate.py:105）：與注入器零配對，假陽性/哨兵誤判都算「檢出」；且無聲排除 round 0（協議無此排除，3 格被壓低）。修：檢出判據與 truth per-well artifacts 標籤相交。
- **[C] 歸因精度不與 truth 逐孔配對**（aggregate.py:124）：乾淨孔歸因恰答場景注入器虛增精度；事件計數無去重多警報 run 權重大。修：correct 前提加 truth 標籤含該注入器。
- **[C] resume 半成品輪孤兒觀測 × 覆寫 truth 錯配**（loop.py:344）：obs_id 隨機 + mid-round 崩潰 resume → 舊觀測不清除 + truth 覆寫 → scoring 按 (round,well) join 錯配。修：resume 起點輪級 GC 或 obs_id 確定性派生 + scoring 對賬。
- **[C] compare.py QC 稅死代碼**（compare.py:148）：要求所有臂所有 seed 污染恆 0，純噪聲本底 0.3% 使其永不生成；與 aggregate 同名指標定義分叉。修：改按場景配置判定零伪影。
- **[C] wrong_optimum 3σ 閾在贏者詛咒邊界**（scoring.py:181）：單副本 raw max，零伪影本底 ~5%，robust S1 假最優率 0.15 純抽樣噪聲但主表原樣呈現。修：極值校正閾值或報 S1 本底行 + 用輪次占比。
- **[C] H3「regret 差 ≤5%」量綱未定義**：絕對讀法 1.2% 過、相對讀法 +152% 慘敗；aggregate 只存字符串不判定。修：協議補口徑 + 落布爾判定。
- **[D2] 歸一化伪後驗掩蓋絕對證據**（attribution.py:495）：唯一正分假設 → confidence=1.0，FLOOR/MARGIN 失效，單一弱信號 t=3.05 直接報 confidence=1.0。修：FLOOR 作用於未歸一化 raw score。
- **[D2] glare/dust 反駁器同義反復**（attribution.py:335）：板級門與反駁門同一布爾量，exposure 單點毛刺 1.26 → glare confidence=1.0 且頂掉真實空間/批次歸因。修：加方向一致性門。
- **[D2] _board_frame 硬編碼模擬器約定**（attribution.py:164）：批次公式/capture 序寫死，真實適配器下 batch 歸因靜默死亡（違反自己的「OS 可見證據」紅線）。修：逐孔讀 obs 的 solution_batch/capture_index。
- **[D2] round_band × 批次鍵含輪次前綴**（failure_model.py:51）：規劃期精確桶必空、偶數輪模型失憶回退全局 p̄。修：邊際回退加「去 round_band 維」層級或向前 band 借先驗。
- **[E] 六條 P2**（見 E 原報告）：單寫者零強制（無鎖，兩個寫句柄同目錄使日誌 seq 撞車）· override --obs 無邊界校驗可指向目錄外文件 · resume 只校驗 name/mode/seed，域配置漂移靜默放行使 config.json 與實際不符 · UI 緩存用秒級 st_mtime，亞秒連續寫讀到舊快照 · reclassify 繞過狀態機且可把 qc=None 觀測直送響應模型 · 末行帶換行但內容損壞被當 torn-tail 靜默丟棄。分別修：writer.lock、obs_id 正則校驗、resume 域配置全量哈希比對、`st_mtime_ns`、reclassify 要求已有 qc、torn-tail 判據加「不以 \n 結尾」物理條件。

## P3 清單（14 條，簡列）

subsample 弱門（0.5 系數無標定）· ΔR² 雙軸選擇無 Bonferroni · 簽名權重虛假聲明（aux=anchor、batch anchor 恆 1.0）· p_artifact_optimistic 正態近似小 α 失真 · QCPolicy 歸因寫入違反自家 WAL · 0.3/0.6 邊界無測試釘死 + soft_trust「連續衔接」過譽 · rebuild 不感知 supersedes 雙重曝險 · edge 方向硬編碼 r>0 · 缺失 obs 拋 FileNotFoundError 非 ExposError · 舊 run 無 seq 混寫邊界 · checks.py 死導入 cohens_d/mad_z · 失效點對非單調曲線誤導（thermal 報「失效點 0.5」實為全幅不達標）· 歸因精度 0.997 headline 掩蓋 26–46% inconclusive 率 · REPRODUCE §5 字面執行無法重現（默認 naive,os 兩臂、cells_g209.tsv 未提）· rawdata tar 標 ~5GB 實 137MB。

---

## 未攻破清單（同樣重要——這是建設性審查的另一半）

- **真值隔離結構性成立**（B）：14 條邊界路徑全被守住——qc/models/planner/agent 無任何 truth 讀點、RawResult extra=forbid 無真值字段、secondary 簽名不接受 true_value、eval 是葉子只事後讀、UI glob 白名單排除 truth/。
- **agent 無裁決權結構性成立**（B）：偽造 acceptance 被 `_resolutions` 按 actor 過濾、agent 無 store 寫句柄、response_gp.fit 拒非 TRUSTED、提案 params 不入候選（用在案 params_lookup）。
- **數字抄寫零錯誤**（A）：12 項 headline 與 main_table.csv 逐位吻合、QC 稅兩數準確、歸因精度算術成立、1450 格零失敗屬實、備份 SHA256 全過、431 測試屬實、os-soft/弱幅度負結果是真誠實披露、glare 族是 os 真實勝場。
- **Beta-Bernoulli 數學正確、冷啟動定義良好、GP fit 確定且訓練集順序無關**（D2/E）：resume 發散不是來自 GP 或排序，是 snapshot 對超參盲。
- **CSV ingest / 域 YAML / 中間行損壞 / advance_status 遷移表**（E）：均正確響亮失敗。

---

## 建議修復優先級（給下一輪執行方）

1. **先修「失效但無人知」類**（R1-2 三條 + R1-5 三條 + P2 的 override 死投遞/risk 相關）：這些是「機制根本沒生效」，修了才能重評。
2. **再重評測協議**（R1-3 四條 + R1-4 補場景）：A/B 拆分、污染有效口徑、robust 按規格重跑、補統計檢驗、補 drift/dust/S3/S4——**走 Slurm sbatch**。
3. **最後校門面**（R1-1 + R1-6 + 各 overclaim）：多為改措辭 + 補聲明，成本低但不修則審稿人短時間內即可複現同樣質疑。

> 注意順序：1 會改變 2 的數字（修好 M7 規劃機制後 os 臂 regret 可能變化），2 會改變 3 的結論（重評後 H1 判定才能定稿）。不要先急著改門面數字。
