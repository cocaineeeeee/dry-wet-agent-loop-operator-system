# 插件 API 契约（草案）

> **状态：草案 / post-M10。** 本文件**不随 M5–M9 实现**，落地在 M10（UI+文档收尾）之后的平台化阶段
> （PLATFORM_VISION 第 2 层）。它把当前硬编码注册表（`expos/domain.py:ADAPTER_REGISTRY`、
> `qc/checks.py`、`planner/stages.py`）演进为 entry_points 插件体系。配方来源：REFERENCE_MAP §16
> （pluggy / Home-Assistant 走读）＋本轮 pytest / pydantic 源码补充。**未定稿，接口签名以落地时的内核为准。**

## 0. 设计立场（抄什么、反着抄什么）

- **学 pytest**：单一中心 spec 模块（`expos/plugins/contracts.py` 一处放全部 `Protocol`），单一工程名串起注册；
  内置插件先于第三方加载；提供 deny-list 屏蔽（`--disable-plugin PLUGIN` / `EXPOS_DISABLE_PLUGINS`）。
- **不引 pluggy**：我们四个扩展点是「命名单选」或「流水线全跑」，用不上 pluggy 的 multicall 广播；
  改用 pytest 式的**注册期 Protocol/签名硬校验**（`_verify_hook`：`runtime_checkable` + `inspect.signature` 比对，加载期就炸）。
- **反着抄 HA 一条红线**：HA 允许 `custom_components` 覆盖（shadow）内置；对实验安全系统这是注入通道，
  **expos 禁止插件覆盖任何内置名**（见 §5 红线③）。

## 1. 四个 entry_points group 的正式契约

工程名固定 `"expos"`。四个 group 各自的 **接口签名 · manifest 必填 · 加载期校验 · 失败语义**：

### 1.1 `expos.adapters`（命名单选 —— 替代 `ADAPTER_REGISTRY`）
- **接口**：满足 `expos.adapters.base.ExecutionAdapter` Protocol：
  `name: str`；`execute(self, exp: ExperimentObject, rng: np.random.Generator) -> ExecutionResult`。
  可选类属性 `default_metric: str`、`required_params: frozenset[str]`（`load_domain` 交叉校验读它们）。
- **manifest 必填**：`kind=adapter` · `provides_metrics` · `required_variable_kinds` · `safety_class(S0–S3)` · `reversible`。
- **加载期校验**：① `runtime_checkable` isinstance + `execute` 签名逐参比对；② `name` 不在内置/已注册集合（唯一）；
  ③ **`truth_records` 只允许 `sim_*` 前缀内置产**——第三方 adapter 的 manifest 若声明产真值即拒载；
  ④ `safety_class` ≤ 运行配置上限（LAP `physicalLimits` 语义）。
- **失败语义**：任一不满足 → `PluginLoadError`（`user_facing=True`，CLI 干净退出，列出可用 adapter）。**不半载**。

### 1.2 `expos.domains`（配置资产 —— 替代 `domains/*.yaml` 手工放置）
- **接口**：不是可调用对象，是 entry_point 指向包内 `*.yaml` 资源；经现有 `load_domain` 全量校验。
- **manifest 必填**：`kind=domain` · `yaml_path`（包内相对路径）· `adapter`（依赖的 adapter 名）。
- **加载期校验**：① `DomainConfig.model_validate`（`extra=forbid`，未知键响亮失败——红线不变）；
  ② `yaml.adapter` 必须已由某 `expos.adapters` 提供（**域 yaml × 插件 manifest 对账**，升级现有交叉校验）；
  ③ 哨兵参数在设计空间内、注入器名合法（沿用 `domain.py` 现逻辑）。
- **失败语义**：`DomainError`（`user_facing=True`），指明是缺 adapter 还是键非法。

### 1.3 `expos.qc_checks`（流水线全跑 —— 追加到三级检查）
- **接口**：`run(self, exp: ExperimentObject, obs_list: list[ObservationObject], ctx: PlateContext) -> list[QCCheck]`；
  类属性 `level: Literal["hard","reference","structural"]`。**入参只读快照**：只见 OS 可见面
  （value/secondary/*_meta），**签名类型即禁止仿真 sidecar**。
- **manifest 必填**：`kind=qc_check` · `level` · `reads_truth=false`（必须显式 `false`）。
- **加载期校验**：① 签名比对；② `reads_truth` 非 `false` 即拒载；③ 静态检查 `run` 源码不 import `truth`/planner/agent 模块
  （轻量 AST 守门，非沙箱）；④ `level` 合法。
- **失败语义**：`PluginLoadError`。运行期该 check 抛异常 → 降级为 record-only 证据、不中断其余检查（沿用 `run_qc` lazy 收集器语义）。

### 1.4 `expos.planner_stages`（命名单选 —— 追加 FSM 阶段规则）
- **接口**：贡献一个 `StageRule`（`planner/stages.py`）：`name` + `generator` 标签 + 有序 `transitions`；
  criterion 为纯函数 `(StageContext) -> bool`，**不 import loop/store/agent/adapters**。
- **manifest 必填**：`kind=planner_stage` · `generator`（标签，映射在 loop 侧）。
- **加载期校验**：① transitions 目标 stage 均存在（无悬空边）；② generator 标签在 loop 已知映射内；③ criterion 是纯函数（无副作用签名）。
- **失败语义**：`StageError`（`user_facing=True`），非法规则表加载期即炸。

## 2. `expos_plugin.yaml` schema（pydantic，全文）

```python
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.aliases import AliasChoices

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")          # 未知键响亮失败（红线）

class _Common(_M):
    name: str                                          # 唯一注册名；禁与内置同名
    expos_api_version: str                             # 语义化 "MAJOR.MINOR"，见 §4
    tier: Literal["official", "community", "experimental"] = "experimental"
    summary: str = ""
    # 老 manifest 兼容：mode='before' validator + AliasChoices 吸收改名字段（pydantic 惯用法）

class AdapterManifest(_Common):
    kind: Literal["adapter"]
    provides_metrics: list[str]
    required_variable_kinds: list[Literal["continuous", "categorical", "ordinal"]]
    safety_class: Literal["S0", "S1", "S2", "S3"]      # LAP；超运行配置拒载
    reversible: bool = True

class DomainManifest(_Common):
    kind: Literal["domain"]
    yaml_path: str
    adapter: str

class QCCheckManifest(_Common):
    kind: Literal["qc_check"]
    level: Literal["hard", "reference", "structural"]
    reads_truth: Literal[False] = False                # 只读快照红线：必须显式 False

class PlannerStageManifest(_Common):
    kind: Literal["planner_stage"]
    generator: str

# 判别联合：kind 字段单选变体，错/缺 kind 给定向报错（pydantic 惯用法，非全成员噪声）
PluginManifest = TypeAdapter(
    Annotated[
        AdapterManifest | DomainManifest | QCCheckManifest | PlannerStageManifest,
        Field(discriminator="kind"),
    ]
)
# 用法：PluginManifest.validate_python(yaml.safe_load(text))
```

## 3. 质量三档规则目录（初版）

三件套：**规则目录**（本节）× 插件自带 `quality.yaml`（逐条 `done` / `exempt(带理由)`）× `expos-lint` CI grader
（高档含低档、复核 claimed-done）。非 `official` 插件在 report 打水印。每条规则**可自证**：

| 档 | 逐条规则（自证方式） |
|---|---|
| **experimental** | E1 manifest 通过 §2 schema（CI 载入即验）；E2 `expos_api_version` major 匹配（§4）；E3 声明 `safety_class`（adapter）。 |
| **community** | 含全部 E；C1 **无 truth 触碰守门测试**（ratchet：AST + 运行期断言 adapter 前后 `model_dump` 相等、qc 不读 sidecar）；C2 单元测试覆盖接口方法；C3 `execute`/`run` 无网络/无 `pip`/无文件写（静态守门）。 |
| **official** | 含全部 E+C；O1 内核维护者 review 记录（ADR 链接，见研究结论）；O2 QC 税实测 ≤5%（跑 `expos-lint bench`）；O3 文档 + 示例域；O4 版本兼容矩阵声明。 |

## 4. 版本兼容策略（`expos_api_version` 语义）

- 语义化 `MAJOR.MINOR`。内核暴露 `expos.EXPOS_API_VERSION`。
- **加载规则**：`plugin.major != kernel.major` → **拒载**（不兼容变更）；`plugin.minor > kernel.minor` → 拒载（用了更新特性）；
  `plugin.minor <= kernel.minor` 且 major 相等 → 加载。
- MAJOR 升在 CHANGELOG + ADR 记录破坏点；老 manifest 用 `model_validator(mode="before")` + `AliasChoices` 平滑改名字段（pydantic 惯用法）。

## 5. 安全红线（四条，结构性强制）

1. **最小入口**：插件只拿「内核调用它」的入口；注册表对外返回**冻结 `Mapping`**，插件拿不到可变全局。
2. **只读快照**：QC / planner 插件签名只收只读快照（`ObservationObject` OS 可见面 / `StageContext`），类型即隔离，读不到仿真 sidecar。
3. **禁覆盖内置**：插件**不得**注册与任何内置同名的 adapter/domain/qc_check/stage（与 HA `custom` shadow 相反）；同名即拒载。
4. **无运行时装依赖**：加载期**不**执行 `pip`；`safety_class` 超运行配置上限拒载；`expos_api_version` 不匹配拒载（§4）。

## 6. 示例插件包骨架

```
expos-plugin-foambath/
├── pyproject.toml
├── expos_plugin.yaml            # §2 manifest（顶层，或 [tool.expos] 内联）
├── quality.yaml                 # §3 逐条 done/exempt
├── src/expos_foambath/
│   ├── __init__.py
│   ├── adapter.py               # class FoamBathSim: 满足 ExecutionAdapter
│   └── foambath.yaml            # domain 配置资产
└── tests/
    └── test_no_truth_touch.py   # C1 守门测试
```

```toml
# pyproject.toml 片段
[project]
name = "expos-plugin-foambath"
dependencies = ["expos>=1.0,<2.0"]           # 与 EXPOS_API_VERSION major 对齐

[project.entry-points."expos.adapters"]
foambath = "expos_foambath.adapter:FoamBathSim"

[project.entry-points."expos.domains"]
foambath = "expos_foambath:foambath.yaml"    # 指向包内资源
```

---

# v2 增补（草案 v2，post-M10 落地）—— pluggy 风格 hookspec 契约

> **状态：草案 v2，post-M10 落地。** 本节按族2【Plugin/extension ecosystem】源级走读
> （pluggy `_hooks/_manager/_callers`、home-assistant `hassfest/quality_scale` + validator 集、
> VS Code `contributes`/`activationEvents`/when-clause）**最小增补** v1，不改 v1 §0–§6 立场。
> 差异：v1 用 entry_points group 表述四面为 adapters/domains/qc_checks/planner_stages；
> 本轮任务把四**功能面**明确为 **adapter / QC check / planner backend / report renderer**，
> 其中 **report renderer 为 v1 未覆盖的新面**（现状散在 `loop._summarize` / `eval/trajectory` / `eval/scoring`）。
> 立场承接 v1 §0：**借 pluggy 的 hookspec 词汇与语义**（firstresult / 列表聚合 / `_verify_hook` 签名子集校验 /
> `check_pending` 响亮），**是否 vendor pluggy 运行时仍待 post-M10 定**；不引 multicall 广播、不引 wrapper `yield` 环绕。

## 7. 四个 hookspec 的签名与契约（pluggy 风格）

工程名 `"expos"`；hookspec 中心模块 `expos/plugins/contracts.py`。四 hook **全部 `firstresult=False`**
（注册期收集贡献，非运行期择一）。插件以 `@hookimpl def <同名>()` 提供实现。

```python
from collections.abc import Mapping, Sequence, Callable
from pathlib import Path
from typing import Protocol, runtime_checkable, Literal
import numpy as np
from expos.kernel.objects import ExperimentObject, ObservationObject, QCCheck, Candidate
from expos.adapters.base import ExecutionAdapter, ExecutionResult
from expos.qc.checks import PlateContext
# from expos.plugins import hookspec   # pluggy HookspecMarker("expos") 或等价内核 shim

@hookspec
def expos_adapter_register() -> Mapping[str, type[ExecutionAdapter]]:
    """命名单选面。返回 {name: adapter_cls}；内核**合并所有插件贡献、去重、禁 shadow 内置**。
    每个 cls 须满足 adapters/base.py:ExecutionAdapter Protocol（name; execute(exp, rng)->ExecutionResult）。
    契约：adapter **不得**产 truth_records（manifest 声明产真值即拒载，红线）；execute 不改 exp
    （前后 model_dump 相等，C1 守门）。"""

@runtime_checkable
class QCCheckPlugin(Protocol):
    level: Literal["hard", "reference", "structural"]
    def run(self, exp: ExperimentObject, obs_list: list[ObservationObject],
            ctx: PlateContext) -> list[QCCheck]: ...

@hookspec
def expos_qc_checks() -> Sequence[QCCheckPlugin]:
    """流水线全跑面。所有贡献追加到 qc/checks.py:run_qc 的三级检查后，**registration order 冻结**
    （禁 tryfirst/trylast——证据顺序须确定、可重放）。入参只读快照（OS 可见面），签名类型即禁 sim sidecar。
    契约：**只产 QCCheck 证据、不能定 trust**（信任路由内核独占）；run 抛异常→降级 record-only、不断其余
    检查（沿 run_qc lazy 收集器语义）。"""

GeneratorFn = Callable[..., list[Candidate]]   # (space, n, *, seed, restrictions, ...) -> candidates

@hookspec
def expos_planner_generators() -> Mapping[str, GeneratorFn]:
    """planner backend 面（命名单选）。返回 {label: fn}，映射 planner/policy.py:_generate 的 generator
    标签分派。label 禁 shadow 内置（sobol / response_gp+ucb / response_gp+ucb+risk_discount）。
    契约：**纯生成**——fn 不写 store、不 import loop/agent/adapters（纯函数守门）。"""

@runtime_checkable
class ReportRenderer(Protocol):
    name: str
    def render(self, view: "ReadOnlyRunView", out_dir: Path) -> list[Path]: ...

@hookspec
def expos_report_renderers() -> Sequence[ReportRenderer]:
    """report renderer 面（列表聚合，post-run 全跑）。每个 renderer 消费只读 ReadOnlyRunView、
    把派生产物写进内核给定的 out_dir，返回写出的 Path 列表。
    契约：**只读 ReadOnlyRunView（无 truth——唯一 truth 读者是 eval，公理 3）**；只写 out_dir（AST+运行期
    守门，越界写=拒载/记违规）；单 renderer 异常**响亮记录**为错误产物、不污染 run 也不阻断其余 renderer
    （无静默回退）。"""
```

**pluggy 借用 vs 反着抄**（源级依据）：
- **借**：`_verify_hook`（`_manager.py:331-352`）的 `notinspec = set(impl.argnames)-set(spec.argnames)` **签名子集校验**——
  impl 可请求 spec 参数子集、多出任一即 `PluginValidationError`；`check_pending`——无匹配 spec 的 impl 除非 `optionalhook` 否则响亮报「unknown hook」；firstresult 的 None 过滤+列表聚合二态（`_callers.py:125-137`）。
- **反着抄**：pluggy `warn_on_impl` 只 warn → expos **拒载**（无静默）；tryfirst/trylast 抢排序 → **禁用**（QC/renderer 顺序须确定）；wrapper/hookwrapper `yield` 环绕内核调用 → **禁**（违红线①最小入口）；historic 晚注册重放 → 备而不用（回合制无此需求）。

## 8. manifest 增补：`ReportRendererManifest`（补齐 §2 判别联合）

```python
class QCCheckManifest(_Common):            # v1 §2 已有，保留
    kind: Literal["qc_check"]
    level: Literal["hard", "reference", "structural"]
    reads_truth: Literal[False] = False

class PlannerGeneratorManifest(_Common):   # 明确 planner backend 面（v1 §2 PlannerStageManifest 是 FSM 面，二者互补并存）
    kind: Literal["planner_generator"]
    generator_label: str                   # 提供的 generator 标签；禁 shadow 内置标签

class ReportRendererManifest(_Common):     # v1 未覆盖的新面
    kind: Literal["report_renderer"]
    renderer_name: str                     # 唯一；禁 shadow 内置
    outputs: list[str]                     # 声明写出的产物文件名/后缀（供 out_dir 越界写守门对账）
    reads_truth: Literal[False] = False    # 只读 ReadOnlyRunView 红线：必须显式 False
# 三者并入 §2 判别联合 PluginManifest（discriminator="kind"）。
```

VS Code `contributes`/`when` 借用：manifest `kind` + `provides_*/renderer_name/generator_label` 是**静态声明面**
（声明「提供什么」）；`level` / `StageContext` criterion 是 when-clause 式**门控面**；两面分离。
**禁 shadow 落到 manifest**：`name`/`renderer_name`/`generator_label` 加载期核 ∉ 内置集，冲突即 `PluginLoadError`
（承接 no-shadow-builtins 决定与 §5 红线③）。

## 9. quality tier 机器判据表（借 HA quality_scale 的 validator 分级项）

借 home-assistant：分级规则表、逐条 `done/todo/exempt`（`exempt` **强制带 comment**）、声明 tier 驱动
「本档+所有低档规则须全 done/exempt」。**反着抄 HA 一条**：HA `hassfest` 对 ~63 条中仅 7 条有 validator，
其余 56 条**信任 reviewer 的 `done` 不复核**；**expos grader 对机器可检查子集必须复核 claimed-done**，无
validator 的判据**只能加 taint 水印、不能抬 tier**（不给「填个 done 就升级」的通道）。validator 形态照 HA 三类：
**AST 检查**（如 HA `runtime_data.py`/`test_before_setup.py`）、**manifest-flag**（如 HA `config_flow`）、**成员表**（如 HA `strict_typing`）。

| 档 | 判据（`[AUTO]`=validator 复核 / `[JUDGMENT]`=只加水印不抬档） | validator 形态 |
|---|---|---|
| **experimental** | E1 manifest 过 §2/§8 schema `[AUTO]`；E2 `expos_api_version` major 匹配 `[AUTO]`（§4）；E3 adapter 声明 `safety_class` `[AUTO]`；E4 贡献名不 shadow 内置 `[AUTO]`。 | manifest-flag / 成员表 |
| **community** | 含全部 E；C1 **无 truth 触碰** `[AUTO]`（AST：qc/generator/renderer 不 import truth/loop/store/agent；运行期断言 adapter 前后 `model_dump` 相等、qc 不读 sidecar、renderer 只写 `outputs` 声明的 out_dir）；C2 接口方法单测存在 `[AUTO]`（test-existence，仿 HA test_before_setup 思路）；C3 `execute/run/render/generator` 无网络/无 pip/无越界写 `[AUTO]`（AST 守门）。 | AST + 运行期断言 |
| **official** | 含全部 E+C；O1 内核维护者 review 记录（ADR 链接）`[JUDGMENT]`；O2 QC 税实测 ≤5%（`expos-lint bench`）`[AUTO]`；O3 文档+示例域 `[AUTO]`（file-existence）；O4 版本兼容矩阵声明 `[AUTO]`（schema）。 | bench / file-existence / schema |

非 official 插件参与 run → report 打 `community_plugin_used` taint 水印（CAPABILITY_MODEL §4，taint 只标不阻断）。

## 10. 加载期校验清单（consolidated；任一不过 → `PluginLoadError(user_facing=True)`，不半载）

1. **manifest schema**：过 §2/§8 判别联合（`extra=forbid`，未知键响亮失败）。
2. **api_version**：`plugin.major==kernel.major` 且 `plugin.minor<=kernel.minor`，否则拒载（§4）。
3. **hookspec 签名子集校验**（借 pluggy `_verify_hook`）：impl 的 `argnames` ⊆ spec 的 `argnames`；多出即拒。
4. **Protocol isinstance**：`runtime_checkable` 校 `ExecutionAdapter`/`QCCheckPlugin`/`ReportRenderer` + `execute/run/render` 签名逐参比对。
5. **禁 shadow 内置**：`name`/`renderer_name`/`generator_label` ∉ 内置集（红线③）；同名即拒。
6. **truth 隔离**：adapter manifest 不得声明产真值；`qc_check`/`report_renderer` 的 `reads_truth` 必须显式 `False`；AST 静态核 `run`/`render`/generator 源码不 import `truth`/planner/agent（轻量 AST 守门，非沙箱）。
7. **safety_class 上界**：adapter `safety_class` ≤ 运行配置上限（LAP `physicalLimits`），超则拒。
8. **requested_capabilities ⊆ 该 kind 能力上界**（CAPABILITY_MODEL §3/§4，default-deny；qc 申请 `read_truth`、任何插件申请 `route_trust/override_allowlist` → 加载期即炸）。
9. **planner_stage/generator 拓扑**：transitions 目标 stage 均存在（无悬空边）；generator_label 在 loop 已知映射内；criterion/generator 纯函数（无副作用签名）。
10. **未匹配 hook**：无对应 spec 的 impl 除非 `optionalhook` 否则响亮报错（借 pluggy `check_pending`）。

---
*本文件为草案（v1）＋草案 v2（本节，post-M10 落地）；接口签名以落地内核为准。四功能面 = adapter / QC check / planner backend / report renderer。*
