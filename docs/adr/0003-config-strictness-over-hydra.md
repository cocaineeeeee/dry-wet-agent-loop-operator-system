# ADR 0003：配置严格性优先——不采纳 Hydra，坚持 extra=forbid

- **Status**: Accepted (2026-07-10)
- **Context**: 平台化引发"要不要组合式配置"之问；Hydra 先并集 merge 后 set_struct，配置文件内拼错键被静默收下（族6 源码级确认），与"未知键响亮失败"红线结构性冲突。
- **Decision**: 全部配置（域 YAML、插件 manifest）继续走 pydantic `extra="forbid"` 单一纪律；组合/扫描需求由 M9 评测脚本显式生成变体 YAML 解决。
- **Consequences**: 拼错键在加载期即炸（M2 对抗审查红线的配置面延伸）；放弃 Hydra 的 sweep 便利，换取安全语义单点定义。
