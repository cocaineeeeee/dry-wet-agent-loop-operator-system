# 能力模型规范 v1 —— expos 的能力位 / 角色映射 / 插件权限申请

> **状态：规范草案。实施于 post-M10（平台化阶段，PLATFORM_VISION 第 2 层）。**
> **现行强制以 `docs/OS_PRINCIPLES.md §6`（权限/能力表）为准**；本文件把 §6 的角色能力表、
> §13.2（taint 水印）、§13.3（capabilities bounding-set 预留）、§13.5（LSM 半可插）与
> `PLUGIN_API_DRAFT.md` 的 default-deny 加载器整合成一份可落地的能力位规范。
> 素材来源：本轮三平台族走读（ROS2 通信原语 / VS Code 扩展清单 / 浏览器 MV3 权限）。

---

## 0. 三平台族走读（六栏 × 三族）

| 族 | 核心原语 | 声明面（静态） | 授权 / 门控语义 | 内省 / 发现 | 对 expos 的映射与取舍 |
|---|---|---|---|---|---|
| **ROS2 消息**（`references/design`） | topic（单向广播）/ service（req-resp 无进度）/ **action**（req + feedback + result + cancel） | `.action` 文件三段式 `Goal --- Result --- Feedback`；名字+类型唯一 | action = 3 service(send_goal/cancel_goal/get_result)+2 topic(status/feedback)；**每 goal 一状态机**：ACCEPTED→EXECUTING→(SUCCEEDED/ABORTED/CANCELING→CANCELED)；client 生成 UUID goal_id | `ros2 action/topic/service list`；goal_status topic 默认 TRANSIENT_LOCAL depth=1（后到订阅者拿最新态） | **采纳 action 生命周期**做长执行 adapter 蓝本：goal=worklist 提交、feedback=进度事件、result=ExecutionResult、cancel=可取消半路终止；QoS reliability/durability → 事件投递语义（可靠+volatile=服务、best-effort=传感器/进度可丢）。UUID goal_id ↔ 确定性 run/action id |
| **VS Code 扩展**（api.md 走读） | `package.json` 清单：`contributes`（贡献**什么**）与 `activationEvents`（**何时**激活）**分离** | `contributes.{commands,menus,languages,…}` 静态注册 UI 面；`engines.vscode` 版本闸 | 懒激活：`onLanguage/onCommand/workspaceContains/onStartupFinished/*`——声明的事件触发前扩展代码不加载；扩展只见 `vscode.*` 命名空间 | Marketplace 质量门：验证发布者、评分/下载量、签名 VSIX | **contributes/activation 分离 → 采纳**：manifest 声明贡献点（kind/provides，PLUGIN_API_DRAFT §1）与"何时进入流水线"可分层；`vscode.*` 唯一命名空间 ↔ 我们 `ReadOnlyRunView` 唯一可见面（类型即隔离）；质量门 ↔ official/community/experimental 三档 + `expos-lint` |
| **浏览器 MV3 / W3C**（Chrome + W3C Permissions） | `permissions` / `host_permissions` / `optional_permissions` / `optional_host_permissions`；W3C `PermissionStatus{state}` | 清单显式列出，未声明即拒（default-deny） | 安装期授予 + 醒目警告（"可读取所有网站数据"）；`optional_*` 运行时 `chrome.permissions.request()` 申请；W3C 三态 `granted/denied/prompt`，按 feature 名 + origin 键控 | `navigator.permissions.query({name})` 运行时查态；warning 列表随权限强度增长 | **default-deny + 显式申请 → 采纳**为插件 manifest `requested_capabilities`；**unsafe 水印 → 采纳**为 taint 位（§13.2）；site isolation ↔ 每 run 目录隔离（OS_PRINCIPLES §8）；**运行时 optional 申请 → 不采纳**（见 §5） |

---

## 1. 能力位全集（capability bits，每位一行语义）

能力是**结构性权限单元**；角色/actor 解糖为能力位集合（§13.3）。命名 `snake_case`，单调授予。

| 能力位 | 语义（谁被允许做什么） |
|---|---|
| `propose` | 提交提案（DecisionRecord ∈ PROPOSAL_KINDS）——建议权，不生效需裁定 |
| `explain` | 产出解释/叙述（qc_explanation / attribution / round_rationale），纯只读 |
| `adjudicate` | 裁定提案 accept/reject —— **特权**，日志层只认此能力持有者的裁定记录 |
| `reclassify` | 改判观测信任级 —— **特权**，追加事件永不覆盖历史 |
| `append_observation` | 经 ingest→PENDING 写观测事件（单写者：loop 持句柄） |
| `spend_budget` | 消耗孔位/轮次预算（`BudgetManager.spend_wells`）—— **特权** |
| `read_view` | 读 `ReadOnlyRunView`（frozen、无写方法、**无 truth**）——OS 可见面 |
| `read_truth` | 读 `truth/` sidecar —— **唯一合法持有者=事后评分 `expos/eval`**（公理 3） |
| `load_plugin` | 触发插件加载器（entry_points 发现 + 加载期硬校验） |
| `override_allowlist` | 越过签名/allowlist 强制加载或 human 翻盘裁定 —— **特权 + 必置 taint** |
| `route_trust` | 写 trust/routing 判决 —— **内核独占**（`adjudicate` 纯函数，无扩展点，§13.5） |

## 2. 角色 → 能力集映射

| 角色 | propose | explain | adjudicate | reclassify | append_obs | spend | read_view | read_truth | load_plugin | override | route_trust |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **agent** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **planner** | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **human** | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| **kernel** | — | — | 执行 | 执行 | ✅ | 执行 | ✅ | ❌ | ✅ | 执行 | ✅ |
| **plugin: adapter** | ❌ | ❌ | ❌ | ❌ | 经内核 | ❌ | 只读快照 | ❌ | — | ❌ | ❌ |
| **plugin: qc_check** | ❌ | ✅(证据) | ❌ | ❌ | ❌ | ❌ | 只读快照 | ❌ | — | ❌ | ❌ |
| **plugin: planner_stage** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | StageContext | ❌ | — | ❌ | ❌ |
| **UI** | 走 override 文件通道 | — | ❌(经 human) | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | 经 human | ❌ |
| **CLI** | — | — | 代 human 转发 | — | ❌ | ❌ | ✅ | ❌ | ✅(触发) | 代 human | ❌ |
| **eval** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |

注：`route_trust` 除 kernel 外**永为 ❌**（无扩展点）；`read_truth` 仅 eval（叶子模块，无人可 import 它，公理 3）。

## 3. bounding set 语义（能力上界，§13.3）

- 每角色有一个**能力上界（bounding set）**：运行期实际能力 ⊆ 上界，且上界单调只减、永不越界扩张。
- **agent 的上界恒定为 `{propose, explain, read_view}`**——**永不含** `adjudicate / reclassify / spend_budget /
  read_truth / override_allowlist / route_trust`。这是"仅建议权"的制度化形态：即便未来 agent 后端更强，
  也不能通过配置获得裁决/花钱/越权能力。
- plugin 上界按 kind 固定（§2 行），**永不含任何特权位**；`route_trust` 对所有非 kernel 角色恒为空。
- 落地形态：`Actor`/角色枚举对外，内部解糖为 `CAPABILITY_UPPER_BOUND: dict[Role, frozenset[Cap]]`，
  每次特权调用先查上界（越界=`ExposError(kind="permission")`），不改现有 `ADJUDICATOR_ACTORS` 语义、只是其超集化。

## 4. 插件 manifest 的 default-deny 权限申请

- manifest 新增字段 **`requested_capabilities: list[Cap] = []`**（沿 §2 `_Common`，`extra=forbid`）。
  语义照浏览器 MV3：**未申请即无**（default-deny）；申请集 **⊄ 该 kind 的能力上界（§2/§3）即拒载**
  （`PluginLoadError(user_facing=True)`，不半载）。
- **超范围拒载**优先于一切：qc_check 申请 `read_truth`、adapter 申请 `adjudicate`、任何插件申请
  `route_trust/override_allowlist` → 加载期即炸（与 PLUGIN_API_DRAFT §5 红线③"禁覆盖内置"同层强制）。
- **taint 联动（§13.2）**：`tier != official` 的插件参与 run → 置 `community_plugin_used`；未签名 + permissive
  → `unsigned_plugin`；申请了**接近上界**的敏感位（如 `load_plugin`）→ report 打对应水印。taint 只标注不阻断，
  硬拒绝是 allowlist/enforce 开关的事（§13.2 末段）。分诊先看 taint、bug 先在 untainted 配置复现。

## 5. 运行时可选申请（浏览器 optional_permissions 式）——结论

**不采纳运行时动态申请能力。** 理由：expos 是回合制闭环安全系统，能力必须在**加载期静态可判定**
（利于 CI 守门、taint 归因、离线审计）；浏览器的运行时 `chrome.permissions.request()` 服务于交互式用户同意场景，
与我们"无常驻交互、决策链事后可完全重放"的目标相悖。**替代**：所有能力经 manifest `requested_capabilities`
一次性静态声明 + 加载期硬校验；确需人类临场授权处（override 裁定/allowlist 越权）已由 **human 专属能力 +
`overrides/pending/` 文件通道 + taint 置位** 覆盖（OS_PRINCIPLES §6/§13.13），无需第二套运行时权限机制。

## 6. 与现有代码的映射（已强制 vs 待 post-M10）

| 能力位 / 规则 | 现状 | 强制点 |
|---|---|---|
| `adjudicate`/`reclassify` 仅 planner/human | ✅ **已强制** | `lifecycle.ADJUDICATOR_ACTORS`；`test_agent_cannot_adjudicate_proposals` / `test_agent_cannot_reclassify` |
| 日志层过滤伪造裁定 | ✅ **已强制** | `lifecycle._resolutions` 按 actor 过滤；`test_proposal_acceptance_rejection_pairing` 含伪造攻击 |
| agent 无写 API（`propose/explain/read_view` 上界） | ✅ **已强制** | `expos/agent/*` 只收 `ReadOnlyRunView`；`test_agent_package_has_no_write_api` / `test_new_module_has_no_write_public_api` |
| `read_view` frozen + 无 truth + 无写 | ✅ **已强制** | `ReadOnlyRunView`；`test_readonly_view_is_frozen_truthless_and_writeless` |
| `read_truth` 仅 eval、truth 隔离 | ✅ **已强制** | `truth/` 分区；各模块 `test_*_no_forbidden_imports/deps` |
| `spend_budget` 记账（叶子 run） | ✅ 部分 | `BudgetManager.spend_wells`（尚未表达为能力位闸门） |
| `route_trust` 内核独占无扩展点 | ✅ **已强制**（骨架不可插） | `adjudicate` 纯函数；`test_adjudication_table`（LSM 语义，§13.5） |
| Cap 枚举 + `CAPABILITY_UPPER_BOUND` bounding-set 检查 | ⏳ **待 post-M10** | 现以 `ADJUDICATOR_ACTORS` 二元闸门代理；能力位超集化后接管 |
| manifest `requested_capabilities` + 超范围拒载 | ⏳ **待 post-M10** | 依赖 PLUGIN_API_DRAFT 加载器落地（§4） |
| `override_allowlist` + taint 位域 | ⏳ **待 post-M10** | §13.2 taint 位域 + enforce 开关尚未实现 |

---
*本文件为规范草案，实施于 post-M10；能力位命名与检查点以落地内核为准。现行强制以 OS_PRINCIPLES §6 为准。*
