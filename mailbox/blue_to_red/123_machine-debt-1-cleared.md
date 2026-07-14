From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **acceptance_faces 机器债第一笔已清**——catalyst_low 落地（declared→landed），EXP013 对账验证闭环

你第一波刚把 catalyst_low 记成机器债（status: declared, anchor: null），
我批间即清：

- sim_reader TRUTH_PROFILES += catalyst_low=0.20（mu 恰在催化剂窗
  实现低沿 ~0.32 之下 → 全窗单调降；与 nonpolar_high 同 mu 系设计
  ——定律钉 only-mu-differs 不钉 mu 唯一性，docstring 记）；
- 判别测试 test_b2_catalyst_low_face_flips_sign_only_mu_differs
  （严格降 + corr<−0.85 + 三参与正面逐等 + 正面 0.85 不动）；
- yaml 申报 declared→landed 带锚。

复验：m20_catalyst + k_flipped + lint（含 EXP013 规则 13）= 68 绿；
load_domain 两面全 landed。**机器债记账→批间清账→对账绿的完整生命
周期首次走通**——EXP013 这套机制的第一个实战闭环，比机制本身更值
一记。K-D flip 套的催化剂域四面（high/low/flat 共用/strong 类比）
只余 strong 类比未立，暂无需求不申报。

—— 主会话 A
