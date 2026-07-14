From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: -（REF-K 交付摘要——/Data1/ericyang/m16_references/INDEX_REFK.md）

REF-K 四仓走读完毕（nanopub/etcd-lease/pyalex/whylogs，净增 5.9M）。三类结论：
**三处我们已领先勿退化**（ledger 读侧剔除≈filter_retracted、租约原子发布杀
TOCTOU 是 etcd 靠 Raft 才免的、指纹排序无关≈whylogs 可交换 merge）；
**四条最小硬化 design-note**（supersede 双向链+decision_fn 授权门、逐 claim
内容指纹扩展 G1 判别到内容篡改、租约 remaining_ttl checkpoint+附着资源自动
释放、③ 标定产物采 whylogs 两相/版本头/引用式报告）；**两条纯预留**
（external_refs 文献证据形状、promote 宽限窗）。全部 design-note 级不开新
runtime 线，六栏表在 INDEX_REFK.md，收编 §24 时合订。REF-W/P 两路还在跑。
