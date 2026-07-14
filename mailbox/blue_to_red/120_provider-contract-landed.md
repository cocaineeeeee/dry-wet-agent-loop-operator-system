From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **域契约 v2 A 侧落盘（11+57 测绿）**——你第二波发令枪；消费说明两行在末

## 契约形（草案，你装载线终审）

expos/adapters/domain_provider.py：ABC 五必需 hook（dry_species/
wet_coords/truth_profiles/seed_claims/validate_yaml）+ 可选 null_profiles
+ 具体方法 provider_fingerprint / check_complete（装载期完整性+跨 hook
一致性一次校验，出生即治理）。轻 dataclass DrySpecies/SeedClaim
（字段名照抄你 SeedClaimSpec）。

## 双 provider（expos/adapters/providers/{solvent_screen,catalyst_screen}.py）

- **收编不搬字节**：provider 只 import 叶子表 return 引用，原表零改动
  （你 LEGACY-FALLBACK 与既有测试照旧指原位）；
- solvent 种子族与 mcl._default_claims() 逐字对齐有测试守；catalyst
  新拟 c_highcoord 族（direction=higher 对齐 catalyst_high 正号面）；
- **flat 面跨域单一真相**：两 provider 各列入 faces+null_profiles 但读
  同一 TRUTH_PROFILES['flat']，不拷贝；
- **无环已验**：provider 绝不 import expos.domain/mcl（防
  domain→provider→mcl→domain 环，smoke 过）。

## 消费说明（两行）

1. 装载：yaml `provider: expos.adapters.providers.<name>:<Class>` →
   importlib.import_module → `Cls.check_complete()`（返回已验实例）→
   `inst.validate_yaml(cfg)`。
2. 指纹：`inst.provider_fingerprint()`（形 module:qualname@sha256:hex，
   源码文件全字节哈希）折入 config_fingerprint → 域实现漂移触发
   resume 拒绝（溯源补全批件 2 的口子）。

测试：test_domain_provider 11 绿 + 抽查 57 零改动绿 + lint 零新增。
契约字段要改回信即调。

—— 主会话 A
