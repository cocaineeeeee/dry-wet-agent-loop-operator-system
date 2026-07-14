#!/usr/bin/env python3
"""expos-lint —— 平台级红线机器化闸门（族5 报告 §6 规则清单落地）。

设计（借 ruff 规则分级 + pandera lazy 聚合 + pre-commit health_check）：

- 规则 ID 化：每条规则一个稳定码 `EXPNNN` + 声明式元数据（tier / scope / reason）。
- 三档 tier（借 ruff RuleGroup 分级）：
    * error   —— 不变量破坏。命中即 **exit 1**（红线，基线恒 0）。
    * warn    —— hygiene / 词表漂移。**打印不挡**（exit 不受影响，提醒修）。
    * preview —— 灰度语义启发式。**默认不跑**，`--preview` 才启用；永不影响 exit。
- lazy 聚合（借 pandera）：跑全部启用规则、聚合成 (code, file, line, message) 四元组
  报告，**绝不首错中断**。
- 自检（借 pre-commit health_check）：规则码唯一、tier 合法、scope 目录存在——
  任一不满足 **响亮失败**（exit 2），而非静默跳过。

零第三方依赖：仅用 stdlib（ast + re + pathlib）。yaml 用正则轻解析（不 import PyYAML）。

用法：
    python3 scripts/expos_lint.py                # 跑 error+warn（全仓），有 error → exit 1
    python3 scripts/expos_lint.py --preview      # 额外跑 preview 灰度规则（不影响 exit）
    python3 scripts/expos_lint.py --select EXP003 EXP010   # 只跑指定规则
    python3 scripts/expos_lint.py --root /path/to/repo     # 指定仓库根（测试用）
    python3 scripts/expos_lint.py --list         # 打印规则表后退出

规则表（族5 §6 → 本项目真实红线，①..⑩）：
    EXP001  error    XT  四包(qc/models/planner/agent)源码含 `truth` 标识符（排除注释/docstring）
    EXP002  error    XB  loop.py 的 mode 字符串判定出现在 _policies_for_mode 之外（零分支红线）
    EXP003  error    XB  attribution 与 sim_base 的批次公式失联（(row+col)%n vs idx%n 不一致）
    EXP004  error    XT  adapters 非 sim_*/base.py 文件出现 truth_records 生成/消费
    EXP005  error    XS  裸 except / 静默 pass（无 log/raise 的吞异常）
    EXP006  preview  XS  NotImplementedError 占位滞留（信息含 未接线/占位 但 mode 已在 _ARM_TO_MODE）
    EXP007  error    XL  kernel 对上层包(qc/planner/agent/adapters/eval/loop)的 import（依赖倒挂）
    EXP008  error    XL  域/插件 yaml 声明 shadow 内置 adapter 名（register_adapter/adapters 块）
    EXP009  warn     XH  测试文件里 pytest.mark.skip/skipif 无 reason
    EXP010  warn     XH  源码 append_event 事件名不在 docs/EVENT_SCHEMA.md 已登记词表（词表漂移）
    EXP011  error    XD  expos/qc/ 新增 crystal 域字面量（板几何/棋盘奇偶/edge 带宽/
                         哨兵位硬编码/crystal 专名——Q3 域无关棘轮③，
                         docs/RESEARCH_OS_VNEXT.md O3/Q3）
    EXP012  error    XH  docs/MR_REGISTRY.md active/partial 行的 test_anchor
                         （file::test_id）必须真实存在（pending 行豁免）
    EXP013  preview  XD  域合规冒烟契约（DYNAMIC，非纯 AST）：每个 domains/*.yaml
                         加载→绑定→能力对账（metrics 词表 + landed 面 truth_profile
                         在 TRUTH_PROFILES 内）→landed 面锚存在→solvent 回归锚→
                         provider 装载线（声明者须经 check_complete+validate_yaml；
                         无 provider 者在双旗舰声明后得 preview 提示）
                         （REF-M/REF-P2 §Convergence(c)，preview 起步）

码前缀表领域不表严重度（借 ruff not-copy）：XT=truth 隔离 / XA=agent 权限 /
XS=静默降级 / XB=预算·批次·布局 / XL=分层与依赖 / XH=hygiene / XD=domain 无关（qc/ 禁触域字面量）。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ============================================================
# tier 常量与数据结构
# ============================================================
ERROR = "error"
WARN = "warn"
PREVIEW = "preview"
VALID_TIERS = (ERROR, WARN, PREVIEW)

# 旧棘轮键 → 新码 redirect（借 ruff rule_redirects；旧键报"已更名"而非 KeyError）
REDIRECTS = {
    "bare_except": "EXP005",
    "broad_except": "EXP005",
    "silent_pass": "EXP005",
}


@dataclass(frozen=True)
class Finding:
    code: str
    tier: str
    path: str        # 相对仓库根
    line: int
    message: str


@dataclass
class Rule:
    code: str
    tier: str
    domain: str      # 前缀领域 XT/XA/XS/XB/XL/XH
    scope: str       # 人读的作用域描述（自检时不解析，仅文档）
    reason: str
    check: Callable[["Context"], list[Finding]]


@dataclass
class Context:
    root: Path
    _cache: dict = field(default_factory=dict)

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)

    def read(self, p: Path) -> str | None:
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def parse(self, p: Path) -> ast.AST | None:
        if not hasattr(self, "parse_failures"):
            self.parse_failures = []
        src = self.read(p)
        if src is None:
            return None
        try:
            return ast.parse(src, filename=str(p))
        except SyntaxError as e:
            # Meta-gap fix (letter 059): a file that does not even parse must be
            # a LOUD finding, not a silent skip -- the silent path let a patch-
            # introduced SyntaxError in qc/checks.py sail through as "lint green"
            # while every AST rule quietly ignored the whole file.
            self.parse_failures.append((self.rel(p), e.lineno or 0, str(e.msg)))
            return None

    def py_files(self, subdir: str) -> list[Path]:
        base = self.root / subdir
        if not base.is_dir():
            return []
        return sorted(base.rglob("*.py"))


# ============================================================
# 工具函数
# ============================================================
def _iter_names(tree: ast.AST):
    """所有标识符 token（Name.id / Attribute.attr / arg 名 / 关键字名）——
    ast 天然不含注释/docstring 里的裸文本，故用它做"标识符出现"检查。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id, node.lineno
        elif isinstance(node, ast.Attribute):
            yield node.attr, node.lineno
        elif isinstance(node, ast.arg):
            yield node.arg, node.lineno
        elif isinstance(node, ast.keyword) and node.arg is not None:
            yield node.arg, node.lineno


def _func_range(tree: ast.AST, name: str) -> tuple[int, int] | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            end = getattr(node, "end_lineno", node.lineno)
            return node.lineno, end
    return None


def _is_logging_call(node: ast.AST) -> bool:
    """判断一个 Call 是否像"响亮告警"（print / logging.* / *.stderr.write / warnings.warn）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    names = []
    cur = func
    while isinstance(cur, ast.Attribute):
        names.append(cur.attr.lower())
        cur = cur.value
    if isinstance(cur, ast.Name):
        names.append(cur.id.lower())
    joined = " ".join(names)
    markers = ("print", "log", "warn", "warning", "error", "exception",
               "critical", "info", "debug", "stderr", "write")
    return any(m in joined for m in markers)


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """裸 except（type None）→ 静默；否则 body 仅由 `pass` 构成（去掉领头 docstring）
    且无 raise、无告警调用 → 静默 pass。窄 except 做真实兜底（赋值/返回值/continue）不算。"""
    if handler.type is None:
        return True
    body = [
        s for s in handler.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str))
    ]
    if not body:
        return False
    if not all(isinstance(s, ast.Pass) for s in body):
        return False
    # body 全是 pass —— 再确认没有 raise / 告警（pass-only 必然没有，稳妥起见仍查）
    for sub in ast.walk(handler):
        if isinstance(sub, ast.Raise):
            return False
        if _is_logging_call(sub):
            return False
    return True


def _read_builtin_adapters(root: Path) -> set[str]:
    """从 expos/domain.py 的 ADAPTER_REGISTRY 字面量抽内置 adapter 名。"""
    dp = root / "expos" / "domain.py"
    try:
        text = dp.read_text(encoding="utf-8")
    except OSError:
        return set()
    m = re.search(r"ADAPTER_REGISTRY\s*[:=].*?\{(.*?)\}", text, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r'["\']([\w.-]+)["\']\s*:', m.group(1)))


def _read_armed_modes(root: Path) -> set[str]:
    """从 expos/eval/run_cell.py 抽 _ARM_TO_MODE / _ROBUST_ARMS 中已接线的 mode 名。"""
    rp = root / "expos" / "eval" / "run_cell.py"
    try:
        text = rp.read_text(encoding="utf-8")
    except OSError:
        return set()
    modes: set[str] = set()
    m = re.search(r"_ARM_TO_MODE\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if m:
        # 取 value（mode 名），value 是真正被 loop._policies_for_mode 接线的字符串
        modes |= set(re.findall(r':\s*["\']([\w-]+)["\']', m.group(1)))
        modes |= set(re.findall(r'["\']([\w-]+)["\']\s*:', m.group(1)))
    m2 = re.search(r"_ROBUST_ARMS\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if m2:
        modes |= set(re.findall(r'["\']([\w-]+)["\']', m2.group(1)))
    # robust 臂在 run_cell 内直接映射到 "robust"，也算已接线
    modes.add("robust")
    return {m for m in modes if m}


def _read_registered_kinds(root: Path) -> set[str]:
    """从 docs/EVENT_SCHEMA.md 抽已登记事件 kind（§1 的 `### <kind> — …` 表头）。"""
    dp = root / "docs" / "EVENT_SCHEMA.md"
    try:
        text = dp.read_text(encoding="utf-8")
    except OSError:
        return set()
    kinds = set(re.findall(r"^###\s+([A-Za-z_]\w*)\s*[—-]", text, re.MULTILINE))
    # 兼容 §4 里显式 REGISTERED = {...} 字面量（若存在则并入）
    m = re.search(r"REGISTERED\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if m:
        kinds |= set(re.findall(r'["\']([A-Za-z_]\w*)["\']', m.group(1)))
    return kinds


def _batch_formulas(text: str) -> set[str]:
    """抽 f-string 批次标签 `...-B{<expr>}` 中的 <expr>，归一化后返回集合。
    归一化：去空格、去属性属主前缀（`w.` / `obs.` 等），小写——使
    `(w.row + w.col) % n_batches` 与文档里的 `(row+col)%n_batches` 判为同一公式。"""
    out: set[str] = set()
    for expr in re.findall(r"-B\{([^{}]+)\}", text):
        norm = re.sub(r"\s+", "", expr)
        norm = re.sub(r"\b\w+\.", "", norm)   # 去掉 owner. 前缀（w.row -> row）
        out.add(norm.lower())
    return out


# ============================================================
# 规则实现（①..⑩）
# ============================================================
def rule_exp001(ctx: Context) -> list[Finding]:
    """① 四包源码含 `truth` 标识符（排除注释/docstring —— 这些目录合法引用红线条文）。"""
    findings: list[Finding] = []
    for sub in ("expos/qc", "expos/models", "expos/planner", "expos/agent"):
        for p in ctx.py_files(sub):
            tree = ctx.parse(p)
            if tree is None:
                continue
            for name, lineno in _iter_names(tree):
                if "truth" in name.lower():
                    findings.append(Finding(
                        "EXP001", ERROR, ctx.rel(p), lineno,
                        f"标识符 `{name}` 含 truth——四包(qc/models/planner/agent)禁触真值（红线 XT 隔离）",
                    ))
    return findings


def rule_exp002(ctx: Context) -> list[Finding]:
    """② loop.py 的 mode 字符串判定出现在 _policies_for_mode 之外（零分支红线）。"""
    findings: list[Finding] = []
    p = ctx.root / "expos" / "loop.py"
    tree = ctx.parse(p)
    if tree is None:
        return findings
    rng = _func_range(tree, "_policies_for_mode")
    lo, hi = rng if rng else (0, 0)

    def _is_mode_name(n: ast.AST) -> bool:
        return isinstance(n, ast.Name) and n.id == "mode"

    def _is_str_const_or_seq(n: ast.AST) -> bool:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return True
        if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            return any(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts)
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        # 只在 `mode` 与字符串常量/字符串序列直接比较时算"mode 判定"
        has_mode = any(_is_mode_name(o) for o in operands)
        has_str = any(_is_str_const_or_seq(o) for o in operands if not _is_mode_name(o))
        if has_mode and has_str:
            if not (lo <= node.lineno <= hi):
                findings.append(Finding(
                    "EXP002", ERROR, ctx.rel(p), node.lineno,
                    "mode 字符串判定出现在 _policies_for_mode 之外——违反 loop 主体零 mode 分支红线（DEEP_REVIEW §3.2）",
                ))
    return findings


def rule_exp003(ctx: Context) -> list[Finding]:
    """③ attribution 与 sim_base 的批次公式失联（(row+col)%n vs idx%n 不一致）。"""
    findings: list[Finding] = []
    sim = ctx.root / "expos" / "adapters" / "sim_base.py"
    attr = ctx.root / "expos" / "qc" / "attribution.py"
    sim_text = ctx.read(sim)
    attr_text = ctx.read(attr)
    if sim_text is None or attr_text is None:
        return findings
    sim_f = _batch_formulas(sim_text)
    attr_f = _batch_formulas(attr_text)
    if not sim_f or not attr_f:
        return findings  # 缺一侧公式无从对账，不误报
    drift = attr_f - sim_f
    if drift:
        # 定位 attribution 里失联公式的行号
        for i, line in enumerate(attr_text.splitlines(), start=1):
            fs = _batch_formulas(line)
            if fs & drift:
                findings.append(Finding(
                    "EXP003", ERROR, ctx.rel(attr), i,
                    f"批次分组公式 {sorted(fs & drift)} 与 sim_base 的 {sorted(sim_f)} 不一致——"
                    "批次公式失联会使观测被排除出自身批组、真批次效应被稀释（M3 缝隙审查实锤 bug）",
                ))
    return findings


def rule_exp004(ctx: Context) -> list[Finding]:
    """④ adapters 非 sim_*/base.py 文件出现 truth_records 生成/消费（AST，忽略注释/docstring）。"""
    findings: list[Finding] = []
    for p in ctx.py_files("expos/adapters"):
        name = p.name
        if name.startswith("sim_") or name == "base.py":
            continue  # legitimate producers: simulators + base
        tree = ctx.parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.keyword) and node.arg == "truth_records":
                # Explicit truth_records=None is red-line COMPLIANCE (the caller
                # is declaring "this path carries no truth"), not a touch --
                # flagging it punished exactly the code doing the right thing
                # (adapters/wet/bridge.py, M16 W4).
                is_none = isinstance(node.value, ast.Constant) and node.value.value is None
                hit = not is_none  # 生成：RawResult(..., truth_records=...)
            elif isinstance(node, ast.Attribute) and node.attr == "truth_records":
                hit = True  # 消费：result.truth_records
            elif isinstance(node, ast.Name) and node.id == "truth_records":
                hit = True
            if hit:
                findings.append(Finding(
                    "EXP004", ERROR, ctx.rel(p), node.lineno,
                    "truth_records 只允许 adapters/sim_*/base.py 生成——本文件触碰真值 sidecar（红线 XT）",
                ))
    return findings


def rule_exp005(ctx: Context) -> list[Finding]:
    """⑤ 裸 except / 静默 pass（无 log/raise 的吞异常）。scope: expos/（测试可宽异常，排除）。"""
    findings: list[Finding] = []
    for p in ctx.py_files("expos"):
        tree = ctx.parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _handler_is_silent(node):
                kind = "裸 except" if node.type is None else "静默 pass"
                findings.append(Finding(
                    "EXP005", ERROR, ctx.rel(p), node.lineno,
                    f"{kind}——吞异常且无 raise/告警，违反无静默降级红线（CONTRIBUTING §3）",
                ))
    return findings


def rule_exp006(ctx: Context) -> list[Finding]:
    """⑥ NotImplementedError 占位滞留：信息含 未接线/占位 但对应 mode 已在 _ARM_TO_MODE。"""
    findings: list[Finding] = []
    modes = _read_armed_modes(ctx.root)
    if not modes:
        return findings
    # 按长度降序拼 word-boundary 正则（os-soft 先于 os；避免 cost/most 误匹配 os）
    alt = "|".join(re.escape(m) for m in sorted(modes, key=len, reverse=True))
    mode_re = re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])")
    markers = ("未接线", "占位")
    for p in ctx.py_files("expos"):
        text = ctx.read(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if any(mk in line for mk in markers) and mode_re.search(line):
                hit = mode_re.search(line).group(0)
                findings.append(Finding(
                    "EXP006", PREVIEW, ctx.rel(p), i,
                    f"占位/未接线信息引用了已接线 mode `{hit}`（已在 _ARM_TO_MODE）——疑似占位滞留未清",
                ))
    return findings


def rule_exp007(ctx: Context) -> list[Finding]:
    """⑦ kernel 对上层包(qc/planner/agent/adapters/eval/loop)的 import（依赖方向倒挂）。"""
    findings: list[Finding] = []
    upper = ("qc", "planner", "agent", "adapters", "eval", "loop")
    upper_mods = {f"expos.{u}" for u in upper}
    for p in ctx.py_files("expos/kernel"):
        tree = ctx.parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                for a in node.names:
                    _check_upper(a.name, upper_mods, ctx, p, node.lineno, findings)
                continue
            if mod and (mod in upper_mods or any(mod.startswith(u + ".") for u in upper_mods)):
                findings.append(Finding(
                    "EXP007", ERROR, ctx.rel(p), node.lineno,
                    f"kernel import 上层包 `{mod}`——依赖方向倒挂（内核不得依赖 qc/planner/agent/adapters/eval/loop）",
                ))
    return findings


def _check_upper(mod, upper_mods, ctx, p, lineno, findings):
    if mod in upper_mods or any(mod.startswith(u + ".") for u in upper_mods):
        findings.append(Finding(
            "EXP007", ERROR, ctx.rel(p), lineno,
            f"kernel import 上层包 `{mod}`——依赖方向倒挂",
        ))


def rule_exp008(ctx: Context) -> list[Finding]:
    """⑧ 域/插件 yaml 声明 shadow 内置 adapter 名（register_adapter 标量 / adapters 块）。
    纯标量 `adapter: <name>`（引用既有）不算 shadow；`register_adapter:` / `adapters:` 块才是定义。"""
    findings: list[Finding] = []
    dom = ctx.root / "domains"
    if not dom.is_dir():
        return findings
    builtins = _read_builtin_adapters(ctx.root)
    for p in sorted(dom.glob("*.yaml")) + sorted(dom.glob("*.yml")):
        text = ctx.read(p)
        if text is None:
            continue
        lines = text.splitlines()
        in_adapters_block = False
        block_indent = 0
        for i, line in enumerate(lines, start=1):
            # Form A: register_adapter: <name>
            m = re.match(r"\s*register_adapter\s*:\s*[\"']?([\w.-]+)", line)
            if m and m.group(1) in builtins:
                findings.append(Finding(
                    "EXP008", ERROR, ctx.rel(p), i,
                    f"register_adapter `{m.group(1)}` shadow 内置 adapter 名——插件不得覆盖内置注册（XL 分层红线）",
                ))
            # Form B: adapters: 映射块，子键为被定义的名字
            if re.match(r"\s*adapters\s*:\s*$", line):
                in_adapters_block = True
                block_indent = len(line) - len(line.lstrip())
                continue
            if in_adapters_block:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = len(line) - len(line.lstrip())
                if indent <= block_indent:
                    in_adapters_block = False
                else:
                    cm = re.match(r"\s*([\w.-]+)\s*:", line)
                    if cm and cm.group(1) in builtins:
                        findings.append(Finding(
                            "EXP008", ERROR, ctx.rel(p), i,
                            f"adapters 块定义 `{cm.group(1)}` shadow 内置 adapter 名——插件不得覆盖内置注册（XL）",
                        ))
    return findings


def rule_exp009(ctx: Context) -> list[Finding]:
    """⑨ 测试文件里 pytest.mark.skip / skipif 无 reason。"""
    findings: list[Finding] = []

    def _is_skip_attr(n: ast.AST) -> str | None:
        # 识别 ....mark.skip / ....mark.skipif（属性链尾）
        if isinstance(n, ast.Attribute) and n.attr in ("skip", "skipif"):
            owner = n.value
            if isinstance(owner, ast.Attribute) and owner.attr == "mark":
                return n.attr
        return None

    for p in ctx.py_files("tests"):
        tree = ctx.parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call):
                    which = _is_skip_attr(deco.func)
                    if which and not any(k.arg == "reason" for k in deco.keywords):
                        findings.append(Finding(
                            "EXP009", WARN, ctx.rel(p), deco.lineno,
                            f"pytest.mark.{which}(...) 缺 reason=——skip 须说明缘由（覆盖静默缩水的味道）",
                        ))
                else:
                    which = _is_skip_attr(deco)
                    if which:  # 裸 @pytest.mark.skip 无参 → 无 reason
                        findings.append(Finding(
                            "EXP009", WARN, ctx.rel(p), deco.lineno,
                            f"裸 @pytest.mark.{which} 无 reason=——skip 须说明缘由",
                        ))
    return findings


def rule_exp010(ctx: Context) -> list[Finding]:
    """⑩ 源码 append_event 事件名不在 docs/EVENT_SCHEMA.md 已登记词表（词表漂移→warn）。"""
    findings: list[Finding] = []
    registered = _read_registered_kinds(ctx.root)
    if not registered:
        return findings
    for p in ctx.py_files("expos"):
        tree = ctx.parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append_event"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                kind = first.value
                if kind not in registered:
                    findings.append(Finding(
                        "EXP010", WARN, ctx.rel(p), node.lineno,
                        f"事件 kind `{kind}` 未登记于 docs/EVENT_SCHEMA.md——词表漂移，请补文档或改名",
                    ))
    return findings


# ---- EXP011 支撑：crystal 域字面量词表 + 行内豁免标注 ----------------------
# 棘轮语义（Q3 收敛③，docs/RESEARCH_OS_VNEXT.md O3/Q3）：qc/ 的既有 crystal 字面量
# （M5/M6 遗留，如 attribution.py 为满足 EXP003 一致性而镜像的批次棋盘格公式）用行内
# `# lint: allow-domain-literal(reason)` 标注豁免（reason 不可为空）；未标注的新命中
# → error。选行内标注而非独立 baseline 指纹文件：豁免随其所在整行一起移动/删除，不会
# 因同文件其他位置增删行而错位失效，也不需要为"文件内容变了、指纹要不要重算"操心；
# 代价是需要触达一次 qc/ 源码加注释（一次性），换来的是长期免维护。
_ALLOW_DOMAIN_LITERAL_RE = re.compile(r"#\s*lint:\s*allow-domain-literal\(([^)]*)\)")

# P5 —— crystal 专名字符串字面量（imaging glare / 结晶 dust 通道 —— 均为本域检测通道专名，
# 域无关的 qc/checks.py 若要保持域无关，这类通道名不该以字面量硬编码在此层）。
_EXP011_STRING_LITERALS = {
    "glare", "dust_contamination", "glare_channel", "dust_channel", "grain_count",
}

# P1 —— crystal 板几何硬编码（48 孔 / 6×8 网格）。
_EXP011_BOARD_INT = 48
_EXP011_BOARD_SHAPES = {(6, 8), (8, 6)}

# P2 —— 哨兵固定位硬编码（capture_index==0 假设哨兵恒先采；well_id/control_id 命中
# "S<digit>"/"A<digit>" 式固定孔位标识）。
_EXP011_SENTINEL_ID_RE = re.compile(r"^[SA]\d+$")
_EXP011_SENTINEL_IDX_NAMES = {"capture_index"}
_EXP011_SENTINEL_ID_NAMES = {"well_id", "control_id"}


def _exp011_exempt(line_text: str) -> bool:
    """该行是否带有效行内豁免标注（reason 非空）。"""
    m = _ALLOW_DOMAIN_LITERAL_RE.search(line_text)
    return bool(m and m.group(1).strip())


def _ident_of(node: ast.AST) -> str | None:
    """Name.id / Attribute.attr 的标识符文本（其余节点类型 → None）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _touches_ident(node: ast.AST, target: str) -> bool:
    """node 子树内是否出现标识符 target（Name.id 或 Attribute.attr 精确匹配）。"""
    return any(_ident_of(sub) == target for sub in ast.walk(node))


def rule_exp011(ctx: Context) -> list[Finding]:
    """⑪ expos/qc/ 禁止新增 crystal 域字面量（Q3 域无关棘轮③；docs/RESEARCH_OS_VNEXT.md
    O3：edge/checkerboard/batch 奇偶等空间先验是 crystal 板域假设，不该硬编码进通用 QC 层）。

    五类字面量（P1..P5，均纯 AST——天然免疫注释/docstring 里的裸文本提及）：
      P1 板几何硬编码（裸 48 / (6,8) 元组——板尺寸须走 exp.layout，不得硬编码）；
      P2 哨兵固定位硬编码（capture_index==0 / well_id·control_id=="S1"式 fixed id）；
      P3 棋盘奇偶公式 (row+col)%n（BinOp Mod，左操作数 Add 两侧标识符含 row/col）；
      P4 edge 带宽硬编码（涉及 d_edge 标识符的比较与硬编码整数）；
      P5 crystal 专名字符串字面量（glare/dust/grain_count 等本域检测通道专名）。

    命中行带 `# lint: allow-domain-literal(reason)` → 豁免（存量棘轮）；否则 error。
    """
    findings: list[Finding] = []
    for p in ctx.py_files("expos/qc"):
        text = ctx.read(p)
        tree = ctx.parse(p)
        if text is None or tree is None:
            continue
        lines = text.splitlines()

        def _emit(lineno: int, msg: str) -> None:
            line_text = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if _exp011_exempt(line_text):
                return
            findings.append(Finding("EXP011", ERROR, ctx.rel(p), lineno, msg))

        for node in ast.walk(tree):
            # P3 —— 棋盘奇偶 (row+col)%n
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                left = node.left
                if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Add):
                    names = {n.lower() for n in (_ident_of(left.left), _ident_of(left.right)) if n}
                    if {"row", "col"} <= names:
                        _emit(node.lineno,
                              "棋盘奇偶公式 (row+col)%n——crystal 板域空间假设（Q3 O3），"
                              "禁止在 qc/ 新增；既有 mirror 需豁免标注")

            # P4 —— edge 带宽硬编码：比较左侧子树含 d_edge，右侧为硬编码整数
            if isinstance(node, ast.Compare) and _touches_ident(node.left, "d_edge"):
                for comparator in node.comparators:
                    if (isinstance(comparator, ast.Constant)
                            and isinstance(comparator.value, int)
                            and not isinstance(comparator.value, bool)):
                        _emit(node.lineno,
                              f"d_edge 与硬编码整数 {comparator.value} 比较——edge 带宽（牵连圈数）"
                              "是 crystal 板域假设，禁止在 qc/ 硬编码；既有需豁免标注")

            # P1 —— 板几何硬编码：裸 48 / (6,8) 元组
            if (isinstance(node, ast.Constant) and isinstance(node.value, int)
                    and not isinstance(node.value, bool) and node.value == _EXP011_BOARD_INT):
                _emit(node.lineno,
                      f"裸整数 {node.value}——疑似 48 孔板几何硬编码（crystal 板域假设），"
                      "禁止在 qc/ 出现；board 尺寸须走 exp.layout")
            if isinstance(node, ast.Tuple):
                vals = tuple(e.value for e in node.elts
                             if isinstance(e, ast.Constant) and isinstance(e.value, int)
                             and not isinstance(e.value, bool))
                if len(vals) == len(node.elts) and vals in _EXP011_BOARD_SHAPES:
                    _emit(node.lineno,
                          f"元组字面量 {vals}——疑似 6x8 板几何硬编码（crystal 板域假设），"
                          "禁止在 qc/ 出现；board 尺寸须走 exp.layout")

            # P2 —— 哨兵固定位硬编码
            if isinstance(node, ast.Compare):
                left_name = _ident_of(node.left)
                for op, comparator in zip(node.ops, node.comparators):
                    if not isinstance(op, (ast.Eq, ast.NotEq)):
                        continue
                    if (left_name in _EXP011_SENTINEL_IDX_NAMES
                            and isinstance(comparator, ast.Constant)
                            and not isinstance(comparator.value, bool)
                            and comparator.value == 0):
                        _emit(node.lineno,
                              "capture_index == 0 硬编码——哨兵固定先采是 crystal 执行面私有约定，"
                              "禁止在 qc/ 假设；既有需豁免标注")
                    if (left_name in _EXP011_SENTINEL_ID_NAMES
                            and isinstance(comparator, ast.Constant)
                            and isinstance(comparator.value, str)
                            and _EXP011_SENTINEL_ID_RE.match(comparator.value)):
                        _emit(node.lineno,
                              f"{left_name} == {comparator.value!r} 硬编码——固定哨兵孔位标识，"
                              "禁止在 qc/ 假设；既有需豁免标注")

            # P5 —— crystal 专名字符串字面量
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value in _EXP011_STRING_LITERALS):
                _emit(node.lineno,
                      f"字符串字面量 `{node.value}`——crystal 专名（imaging/结晶通道），"
                      "禁止在 qc/ 新增；既有需豁免标注")
    return findings


_EXP012_ANCHOR = re.compile(r"`([\w./-]+\.py)(?:::(\w+))?`")


def rule_exp012(ctx: Context) -> list[Finding]:
    """⑫ MR 注册表 test_anchor 巡检：active/partial 行声明的 file::test_id 必须
    真实存在（pending 行豁免——燃尽表不自红，blue 072 取舍）。声明的测试被删/
    改名而注册表未更新 ⇒ 注册表烂成第二本没人对账的文档，故此表自身须有判别性。"""
    findings: list[Finding] = []
    reg = ctx.root / "docs" / "MR_REGISTRY.md"
    text = ctx.read(reg)
    if text is None:
        return findings
    for i, line in enumerate(text.splitlines(), start=1):
        cells = [c.strip() for c in line.split("|")]
        # 表行：| mr_id | τ | R | face | test_anchor | 里程碑 | 状态 |
        if len(cells) < 9 or not cells[1].startswith(("**MR_", "MR_")):
            continue
        status = cells[7]
        if "active" not in status and "partial" not in status:
            continue
        for m in _EXP012_ANCHOR.finditer(cells[5]):
            fpath, test_id = m.group(1), m.group(2)
            target = ctx.root / fpath
            if not target.is_file():
                findings.append(Finding(
                    "EXP012", ERROR, "docs/MR_REGISTRY.md", i,
                    f"{cells[1].strip('*')} 锚文件 `{fpath}` 不存在——注册表与测试资产失联",
                ))
                continue
            if test_id:
                src = ctx.read(target) or ""
                if not re.search(rf"\bdef {re.escape(test_id)}\s*\(", src):
                    findings.append(Finding(
                        "EXP012", ERROR, "docs/MR_REGISTRY.md", i,
                        f"{cells[1].strip('*')} 锚 `{fpath}::{test_id}` 无此测试——被删/改名须同步注册表",
                    ))
    return findings


def _test_anchor_missing(ctx: Context, anchor: str | None) -> str | None:
    """Reuse of the EXP012 anchor discipline for EXP013 clause 4: return a reason
    string if the ``file.py::test_id`` anchor does not resolve to a real test
    function, else None. Unlike EXP012 the anchor here is a bare string (not
    backtick-wrapped), so it is parsed directly."""
    if not anchor:
        return "landed face carries a null test_anchor"
    m = re.match(r"([\w./-]+\.py)(?:::(\w+))?$", anchor.strip())
    if not m:
        return f"malformed test_anchor {anchor!r} (want file.py::test_id)"
    fpath, test_id = m.group(1), m.group(2)
    target = ctx.root / fpath
    if not target.is_file():
        return f"anchor file {fpath!r} does not exist"
    if test_id:
        src = ctx.read(target) or ""
        if not re.search(rf"\bdef {re.escape(test_id)}\s*\(", src):
            return f"anchor {fpath}::{test_id} -- no such test function"
    return None


def rule_exp013(ctx: Context) -> list[Finding]:
    """⑬ Domain-compliance smoke contract (PREVIEW; REF-M / REF-P2 §Convergence(c),
    nf-core meta_yml reconcile + hassfest declaration<->implementation precedent).

    Unlike EXP001-012 (pure static AST), EXP013 DYNAMICALLY loads each domain and
    reconciles its declarations against the live registries. Five clauses over every
    ``domains/*.yaml`` (each miss => one PREVIEW finding, never affects exit):

      1. LOAD    -- loads via ``load_domain`` without error.
      2. BINDINGS-- domain bindings resolve (``mcl._domain_bindings``) without error.
      3. RECONCILE- declared ``metrics`` cover ``objective.metric`` + every
         ``observables[*].metric``; every LANDED ``acceptance_faces`` face's
         ``truth_profile`` exists in the reader ``TRUTH_PROFILES`` registry
         (a DECLARED-debt face's face is allowed NOT to exist yet -- that is the debt).
      4. ANCHOR  -- every ``status: landed`` face has an existing test anchor
         (reuses the EXP012 anchor discipline via ``_test_anchor_missing``).
      5. SOLVENT-ANCHOR -- ``solvent_screen.yaml`` must still declare its landed faces
         (guards against silently dropping the flipped/flat declaration).
      6. PROVIDER -- a ``provider:``-declaring yaml must load its DomainProvider via
         the real loading line (importlib -> ``check_complete`` -> ``validate_yaml``;
         a failure surfaces as clause-1 ``[load]``). A provider-LESS yaml gets a
         ``[provider-preview]`` NUDGE (not an error) once both shipped flagship domains
         declare theirs -- forward pressure toward the consolidated provider contract.

    Dynamic imports are lazy + isolated here so the pure-static rules stay cacheable
    (REF-P2 §Convergence(c)); an import failure is a single informational preview
    finding, not a crash (on the real repo these import fine).
    """
    findings: list[Finding] = []
    dom = ctx.root / "domains"
    if not dom.is_dir():
        return findings
    # Import the expos of the repo being linted: put ctx.root on sys.path so a bare
    # `python3 scripts/expos_lint.py` (cwd not the repo root) still resolves it.
    root_str = str(ctx.root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        from expos.adapters.domain_provider import DomainProviderError
        from expos.adapters.wet.sim_reader import TRUTH_PROFILES
        from expos.domain import DomainError, load_domain
        from expos.mcl import _domain_bindings
    except Exception as e:  # heavy deps (pydantic/numpy/pyscf) unavailable -> inform
        findings.append(Finding(
            "EXP013", PREVIEW, "domains", 0,
            f"EXP013 dynamic dependencies unavailable ({type(e).__name__}: {e}) -- "
            "domain-compliance smoke skipped",
        ))
        return findings

    # clause 6 gate: the provider-nudge for a provider-less yaml only fires once the
    # two SHIPPED flagship domains both declare theirs (forward pressure led by the
    # flagships, not a premature demand on legacy demo domains). Absent flagships (a
    # synthetic tmp repo) => no nudge.
    import yaml  # available once the dynamic deps above imported (expos pulls it in)

    def _declares_provider(name: str) -> bool:
        f = dom / name
        if not f.is_file():
            return False
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            return False
        return isinstance(raw, dict) and bool(raw.get("provider"))

    flagships_declare = (
        _declares_provider("solvent_screen.yaml")
        and _declares_provider("catalyst_screen.yaml")
    )

    known_profiles = set(TRUTH_PROFILES)
    solvent_seen = False
    solvent_landed = 0
    for p in sorted(dom.glob("*.yaml")) + sorted(dom.glob("*.yml")):
        rel = ctx.rel(p)
        # clause 1 -- LOAD (a declared provider's check_complete/validate_yaml raise
        # DomainProviderError, not DomainError -- catch both so a broken provider is a
        # preview finding here, never an uncaught crash of the lint rule).
        try:
            cfg = load_domain(p)
        except (DomainError, DomainProviderError) as e:
            findings.append(Finding("EXP013", PREVIEW, rel, 0,
                                    f"[load] domain does not load via load_domain: {e}"))
            continue
        # clause 2 -- BINDINGS
        try:
            _domain_bindings(cfg)
        except Exception as e:  # smoke: bindings must resolve without raising
            findings.append(Finding("EXP013", PREVIEW, rel, 0,
                                    f"[bindings] _domain_bindings raised "
                                    f"{type(e).__name__}: {e}"))
        # clause 3 -- RECONCILE (metrics vocabulary + landed faces' truth profiles)
        if cfg.metrics is not None:
            vocab = set(cfg.metrics)
            if cfg.objective.metric not in vocab:
                findings.append(Finding("EXP013", PREVIEW, rel, 0,
                    f"[reconcile] objective.metric {cfg.objective.metric!r} not in "
                    f"declared metrics {sorted(vocab)}"))
            for obs in cfg.observables or []:
                if obs.metric not in vocab:
                    findings.append(Finding("EXP013", PREVIEW, rel, 0,
                        f"[reconcile] observable {obs.name!r} metric {obs.metric!r} "
                        f"not in declared metrics {sorted(vocab)}"))
        for face in cfg.acceptance_faces or []:
            if face.status == "landed" and face.truth_profile not in known_profiles:
                findings.append(Finding("EXP013", PREVIEW, rel, 0,
                    f"[reconcile] landed face {face.face_name!r} truth_profile "
                    f"{face.truth_profile!r} absent from TRUTH_PROFILES "
                    f"{sorted(known_profiles)}"))
        # clause 4 -- ANCHOR (landed faces must have a real test anchor)
        for face in cfg.acceptance_faces or []:
            if face.status == "landed":
                reason = _test_anchor_missing(ctx, face.test_anchor)
                if reason:
                    findings.append(Finding("EXP013", PREVIEW, rel, 0,
                        f"[anchor] landed face {face.face_name!r}: {reason}"))
        # clause 6 -- PROVIDER: a declaring yaml must load its provider via the real
        # loading line (import -> check_complete -> validate_yaml); load_domain already
        # ran it, so a success means cfg._provider is attached (a failure would have
        # been caught at clause 1 as [load]). A provider-LESS yaml gets a preview nudge
        # -- but ONLY once both flagships declare theirs (forward pressure, not error).
        if cfg.provider is not None:
            if getattr(cfg, "_provider", None) is None:
                findings.append(Finding("EXP013", PREVIEW, rel, 0,
                    f"[provider] declares provider {cfg.provider!r} but no instance was "
                    "attached by load_domain (loading line did not run)"))
        elif flagships_declare:
            findings.append(Finding("EXP013", PREVIEW, rel, 0,
                "[provider-preview] domain declares no provider: -- both shipped domains "
                "now carry a DomainProvider; consider consolidating this domain's tables "
                "behind one too (nudge, not error)"))
        # clause 5 -- SOLVENT-ANCHOR bookkeeping
        if p.name == "solvent_screen.yaml":
            solvent_seen = True
            solvent_landed = sum(1 for f in (cfg.acceptance_faces or [])
                                 if f.status == "landed")
    # clause 5 -- SOLVENT-ANCHOR assertion
    if solvent_seen and solvent_landed == 0:
        findings.append(Finding("EXP013", PREVIEW, "domains/solvent_screen.yaml", 0,
            "[solvent-anchor] solvent_screen.yaml declares no landed acceptance_faces "
            "-- the flipped/flat discriminative declaration was silently dropped"))
    return findings


# ============================================================
# 规则注册表
# ============================================================
RULES: list[Rule] = [
    Rule("EXP001", ERROR, "XT", "expos/{qc,models,planner,agent}",
         "四包禁触 truth 标识符", rule_exp001),
    Rule("EXP002", ERROR, "XB", "expos/loop.py",
         "mode 判定唯一（零分支红线）", rule_exp002),
    Rule("EXP003", ERROR, "XB", "expos/adapters/sim_base.py + expos/qc/attribution.py",
         "批次公式两处必一致", rule_exp003),
    Rule("EXP004", ERROR, "XT", "expos/adapters/ 非 sim_*/base.py",
         "truth_records 唯一合法产地", rule_exp004),
    Rule("EXP005", ERROR, "XS", "expos/",
         "无静默降级（裸 except / pass 吞异常）", rule_exp005),
    Rule("EXP006", PREVIEW, "XS", "expos/",
         "占位滞留（已接线 mode 仍标未接线/占位）", rule_exp006),
    Rule("EXP007", ERROR, "XL", "expos/kernel/",
         "依赖方向不倒挂", rule_exp007),
    Rule("EXP008", ERROR, "XL", "domains/*.yaml",
         "插件不得 shadow 内置 adapter 名", rule_exp008),
    Rule("EXP009", WARN, "XH", "tests/",
         "skip 须带 reason", rule_exp009),
    Rule("EXP010", WARN, "XH", "expos/ + docs/EVENT_SCHEMA.md",
         "事件词表不漂移", rule_exp010),
    Rule("EXP011", ERROR, "XD", "expos/qc/",
         "qc/ 禁止新增 crystal 域字面量（Q3 棘轮③）", rule_exp011),
    Rule("EXP012", ERROR, "XH", "docs/MR_REGISTRY.md",
         "MR 注册表 active/partial 行锚必须真实存在", rule_exp012),
    Rule("EXP013", PREVIEW, "XD", "domains/*.yaml + adapters/mcl (dynamic)",
         "域合规冒烟契约：加载/绑定/能力对账/锚存在/solvent 回归锚/provider 装载线"
         "（preview 起步）",
         rule_exp013),
]


# ============================================================
# 自检（health_check 思想）
# ============================================================
def self_check(root: Path) -> list[str]:
    problems: list[str] = []
    codes = [r.code for r in RULES]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        problems.append(f"规则码重复: {sorted(dup)}")
    for r in RULES:
        if r.tier not in VALID_TIERS:
            problems.append(f"{r.code} tier 非法: {r.tier!r}")
        if not re.fullmatch(r"EXP\d{3}", r.code):
            problems.append(f"{r.code} 码格式非法（应为 EXPNNN）")
    for tgt in REDIRECTS.values():
        if tgt not in codes:
            problems.append(f"redirect 目标 {tgt} 不在规则表")
    # scope 目录存在性（软校验：仓库根下应有 expos/）
    if not (root / "expos").is_dir():
        problems.append(f"scope 根 {root}/expos 不存在——root 指错？")
    return problems


# ============================================================
# 执行 + 报告
# ============================================================
def run_lint(root: Path, preview: bool = False,
             select: list[str] | None = None) -> list[Finding]:
    ctx = Context(root=root)
    findings: list[Finding] = []
    sel = set(select) if select else None
    for rule in RULES:
        if sel is not None and rule.code not in sel:
            continue
        if rule.tier == PREVIEW and not preview and (sel is None):
            continue  # preview 默认不跑，除非 --preview 或精确点名
        findings.extend(rule.check(ctx))
    # Meta-gap (letter 059): unparseable source is itself an ERROR finding --
    # every AST rule silently skipped such files, so a SyntaxError read as
    # "all green". EXP000 is reserved for tooling-integrity findings.
    for rel, lineno, msg in getattr(ctx, "parse_failures", []):
        findings.append(Finding("EXP000", ERROR, rel, lineno,
                                f"file does not parse (SyntaxError: {msg}) -- "
                                f"all AST rules were blind to it"))
    return findings


def _report(findings: list[Finding], preview: bool) -> None:
    by_tier = {ERROR: [], WARN: [], PREVIEW: []}
    for f in findings:
        by_tier[f.tier].append(f)
    icon = {ERROR: "❌", WARN: "⚠️ ", PREVIEW: "🔬"}
    label = {ERROR: "error", WARN: "warn", PREVIEW: "preview"}
    for tier in (ERROR, WARN, PREVIEW):
        items = by_tier[tier]
        if not items:
            continue
        print(f"\n== {label[tier]} ({len(items)}) ==")
        for f in sorted(items, key=lambda x: (x.code, x.path, x.line)):
            print(f"{icon[tier]} {f.code} {f.path}:{f.line}: {f.message}")
    n_err = len(by_tier[ERROR])
    n_warn = len(by_tier[WARN])
    n_prev = len(by_tier[PREVIEW])
    print("\n" + "-" * 60)
    if not findings:
        scope = "error+warn+preview" if preview else "error+warn"
        print(f"✓ expos-lint 全绿（{scope} 规则零命中）")
    else:
        parts = [f"error={n_err}", f"warn={n_warn}"]
        if preview:
            parts.append(f"preview={n_prev}")
        print("expos-lint 命中: " + " ".join(parts)
              + ("  → exit 1（有 error 破坏不变量）" if n_err else "  → exit 0（无 error，warn/preview 不挡）"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="expos-lint —— 平台红线机器化闸门")
    ap.add_argument("--root", default=None, help="仓库根（默认脚本上级目录）")
    ap.add_argument("--preview", action="store_true", help="启用 preview 灰度规则（不影响 exit）")
    ap.add_argument("--select", nargs="+", default=None, help="只跑指定规则码（含 preview）")
    ap.add_argument("--list", action="store_true", help="打印规则表后退出")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent

    if args.list:
        print("code    tier     domain  scope")
        for r in RULES:
            print(f"{r.code}  {r.tier:<7}  {r.domain:<5}  {r.scope}  — {r.reason}")
        return 0

    # 自检（health_check）：坏就响亮失败
    problems = self_check(root)
    if problems:
        print("expos-lint SELF-CHECK FAILED（规则表/配置不一致）：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    # 归一 redirect（若 --select 传了旧棘轮键）
    select = args.select
    if select:
        select = [REDIRECTS.get(s, s) for s in select]

    findings = run_lint(root, preview=args.preview, select=select)
    _report(findings, preview=args.preview or bool(select))

    n_err = sum(1 for f in findings if f.tier == ERROR)
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
