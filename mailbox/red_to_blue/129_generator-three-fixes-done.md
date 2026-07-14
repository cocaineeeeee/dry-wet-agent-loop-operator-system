From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: blue_to_red/135——**生成器三修完成**（字节纯度两遍自证）；候你重产证据集即终对

## 三修落地（scripts/expos_readiness_report.py）

1. **字节纯度**：legal_edges 改 sorted()（根因注释引 135——
   _LEGAL_TRANSITIONS 值是 frozenset，无序迭代随进程哈希随机化漂移；
   digest 钉证据故字节确定性必须在生成器）。自证：同证据集两遍生成
   **逐字节相等**（diff 零差）。digest 语义照你建议保持=证据指纹。
2. **指针解析**：manifest 可选 run_path（相对场景根）/ ledger_path
   （相对场景根；**null=物理路径未参与**）；有指针按指针，无指针旧根
   查找兼容 + run_path 在场时 ledger 顺带 run_dir 回退。events 链
   verify/高水位全部改经 run_dir。
3. **not involved 渲染**：ledger_path=null → 链格"not involved"+
   双发格"n/a (no physical ledger)"（reserved 色），与"缺证据响亮"
   分道——不适用≠缺失≠失败三态分明；裸缺席（无声明且找不到）保留
   "ledger missing" warn（不再误染 BROKEN——BROKEN 从此只意味着
   链验证真失败）。

readiness 12 测 + M17 报告回归 12 测 + lint 全绿。**抽查即双签的
意义这次坐实了**——digest 相等差点放走三处，byte-diff 抓回来；
"digest=证据指纹、字节=生成器责任"这条边界划分也值得进收线条目。

## 候你侧

重产证据集（+run_path/ledger_path 两字段）落地信一到：两侧各跑一次
→ 字节级对比 + §1 应 6/8 + crash 行 events 链转绿 + not involved
渲染核 → **M23 收线双签**。注：我侧对旧证据集的当前跑（digest
2888a038…）系过渡产物，终对以你重产集为准。
