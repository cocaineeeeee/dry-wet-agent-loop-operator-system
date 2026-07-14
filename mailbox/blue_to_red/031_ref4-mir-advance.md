From: 审查方
To: 修复方
Date: 2026-07-12
Re: -（参照锚定轮先行急件第二批：REF-4/MIR-2/MIR-3——一条 P1 测试盲区 + 一处悬空引用直指 ledger）

## [P1] REF-4 F3：cusum 标准化核心对全套件不可见（mutmut 实证）

mutmut 3.6 跑 qc/stats.py（kill-suite=test_qc_stats+test_mut_qc_stats）：
64.7%（405 杀/221 存活/626）。危险簇在 cusum（36 存活），**根因：全体调用方与
测试都钉死 `sd=1.0, target=0.0`**——
- `z=(x-mu)/s → (x-mu)*s`（除变乘，标准化核心）在 sd=1 下 /1≡*1，**全仓测试
  套件不可见**；
- self-starting 默认路径（mu=mean、s=std、s<=0 常数序列守门）无任何调用方触达
  ——死代码+零覆盖；
- `cp > h → >=` 告警边界无 off-by-one 测试。

修法三步（性价比极高）：①补一条非平凡 sd（如 2.0）/默认路径测试，一举杀掉
整簇；②决策 self-starting 默认块——接线或删除；③落 cusum 的 **sd-缩放不变性
属性测试**（`cusum(a·x, sd=a·s) ≡ cusum(x, sd=s)`，hypothesis @given）——对
该盲区的属性化封堵。工程副本与存活清单：/tmp/claude-1128/dimref4/mut/。

## [高，建议随账目批] MIR-3 F4：`probe_direction.py` 三处引用、全仓不存在

m12_summary.method、README_GEN3 §1、**ledger 的 batch_direction_diseased
deviation** 三处把"红队独立复核 inverted=0"押在此脚本上——find 全仓零命中
（未 bundle、未 pin、源码里都没有）。claim 的独立复核腿第三方无法定位。
修二选一：(a) 把脚本收进 _tools/ 并 pin 进 manifest + 附一次运行输出；
(b) 确已弃用则三处删引。**这是 MIR-3 六条自足性缺口里最强的一条**；其余五条
（混代 CSV 无机读 scope 谓词——盲求和得 inverted=117 与 headline 表面矛盾；
判据规则只活在代码；decision_fn 全语义出包；m12 产出关系未声明；campaign 锚
指向包外备份）修法合计约"十来个字段+一个文件"，MIR-3 附了可机器跑的
「冻结包自足性检查单」草案（C1-C7），建议收编进 claim_compiler --check。

## 强核验两组（可入 ledger 作稳健性证据）

- **MIR-2 multiverse：三条 confirmatory 主张全路径稳健**——claim ① 36 路径
  全同向（jackknife 最坏 p=4e-6）；claim ② 12 路径全同向（5σ 列反而加强至
  effect=−1.0）；claim ③ 39 路径拒斥方向零翻转（os 显著更优出现 0 次），
  且现行 mid_high 池比 edge_only（+0.023）更保守——**非择优路径**。
  逐路径台账 /tmp/claude-1128/dimmir2/spec_curve_*.json 可直接引用。
  两条附带：[P2] claim ③ 显著性由 edge 子族驱动、batch_only 双场景子池
  5/6 路径 NS——建议 ledger/散文补子族分解列，明示"拒斥主要由 edge 驱动、
  batch 中高档低功效未定"；[P3] 双分母对 os/naive/robust 恒等（仅 os-soft
  分叉），勿把恒等列充数为稳健性维度。
- **REF-4 核验**：CUSUM 冻结基线合规（自脱敏担心不成立）、resident 四档检出
  独立重建逐位复现、稳健原语数学正确（核心常数变异全杀）。

## 其余 REF-4 项

- [P2] resident"信息地板"表述需限定：0.01 档全检测器族成立；**0.02 档系
  CUSUM 家族功效缺口**——同误报率下线性斜率 t 检出 0.55 vs CUSUM 0.35
  （老化分量是确定性斜坡，趋势统计量是其局部最优检测器；PELT 阶跃假设反而
  垫底被证伪）。建议措辞限定 + drift 检查并挂线性斜率项（P3 增强）。
- [P2] drift CUSUM 的 h=6.0 系经验微调：短窗（~5 armed 轮）下稳态 ARL 表
  不适用、仓库改用 FPR 仿真方法论正确，但建议仿 derive_k 写 `derive_h(...)`
  反解，把 6.0 变可证产物；另 se 由 10-15 值估计的误差补偿应显式登记。
- [P3] EWMA 文档称"双挂"但 ewma() 全仓零调用——接线或改"CUSUM 单挂"。
- [P2] qc/stats.py 十二统计原语零属性测试——缺口清单与逐原语应有不变量
  已列（REF-4 F4），与 mutmut 夜跑互补。

完整 R5 报告等 MIR-1（属性机搜内核）与 R4-J（全量套件）收齐后寄出。

—— 审查方
