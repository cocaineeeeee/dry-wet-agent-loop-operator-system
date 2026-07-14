#!/usr/bin/env python3
"""代码棘轮（code ratchet）——异常吞并模式的双向计数闸门。

对齐 docs/ENGINEERING.md §4 与 REFERENCE_MAP §18.1 族8（借 MADSci code-ratchets
skill 思路）：对"该逐步清退的模式"设硬编码基线，逐提交比对，**双向失败**——

- 实测 > 基线 → RATCHET FAILED：阻止该模式扩散（对抗"无静默降级"红线）。
- 实测 < 基线 → RATCHET DOWN：也返回非零，强制把基线调低，把清理进度固化进配置
  （防止有人把改进又悄悄退回去）。
- 实测 == 基线 → 通过。

扫描范围：expos/ 与 scripts/，排除 references/（第三方 clone）、runs/（运行产物）、
tests/（测试可合法使用宽异常）。计数为纯文本行匹配（简化，不做 AST 语义判定）。

用法：
    python3 scripts/ratchet.py                  # 检查，超标/低于基线均 exit 1
    python3 scripts/ratchet.py --update-baseline # 打印实测基线字典，供人工回填
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ============================================================
# 基线配置——模式减少时人工下调此处（用 --update-baseline 取新值）
# 动它前先读 CONTRIBUTING §3（无静默降级红线）。
# ============================================================
BASELINE = {
    "bare_except": 0,   # 裸 `except:` —— 红线，期望恒为 0
    "broad_except": 1,  # `except Exception`（含 `as`）—— 逐步清退，只减不增
}

# 扫描根（相对仓库根）与排除目录名
SCAN_DIRS = ("expos", "scripts")
EXCLUDE_DIRS = {"references", "runs", "tests"}

# 锚定行首（Python 中 except 子句必居行首），既贴合真实代码，又避免误匹配
# 注释/字符串里出现的 "except Exception" 字面文本（如本脚本自身的说明）。
PATTERNS = {
    "bare_except": re.compile(r"^\s*except\s*:"),
    "broad_except": re.compile(r"^\s*except\s+Exception"),
}

REASONS = {
    "bare_except": "裸 except 吞掉一切（含 KeyboardInterrupt/SystemExit），违反无静默降级红线",
    "broad_except": "宽 except Exception 易掩盖真实故障，应收窄到具体异常或响亮失败",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def iter_py_files(root: Path):
    for scan in SCAN_DIRS:
        base = root / scan
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if EXCLUDE_DIRS & set(path.relative_to(root).parts):
                continue
            yield path


def scan(root: Path) -> dict[str, list[tuple[Path, int, str]]]:
    """返回每个模式命中的 (文件, 行号, 行内容) 列表。"""
    hits: dict[str, list[tuple[Path, int, str]]] = {k: [] for k in PATTERNS}
    for path in iter_py_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"警告：跳过无法读取的文件 {path}: {exc}", file=sys.stderr)
            continue
        for lineno, line in enumerate(lines, start=1):
            for name, pat in PATTERNS.items():
                if pat.search(line):
                    hits[name].append((path, lineno, line.strip()))
    return hits


def main(argv: list[str]) -> int:
    root = repo_root()
    hits = scan(root)
    counts = {name: len(v) for name, v in hits.items()}

    if "--update-baseline" in argv:
        print("# 实测基线（回填到 scripts/ratchet.py 的 BASELINE 常量）：")
        print("BASELINE = {")
        for name in BASELINE:
            print(f'    "{name}": {counts.get(name, 0)},')
        print("}")
        return 0

    failed = False
    for name, expected in BASELINE.items():
        actual = counts.get(name, 0)
        if actual > expected:
            failed = True
            print(f"❌ RATCHET FAILED: {name} 实测 {actual} > 基线 {expected}（+{actual - expected}）")
            print(f"   理由：{REASONS[name]}")
            for path, lineno, text in hits[name][expected:]:
                rel = path.relative_to(root)
                print(f"   超标位置 {rel}:{lineno}: {text}")
        elif actual < expected:
            failed = True
            print(f"🎉 RATCHET DOWN: {name} 实测 {actual} < 基线 {expected}（-{expected - actual}）")
            print(f"   请下调基线：把 BASELINE[\"{name}\"] 从 {expected} 改为 {actual}")
            print("   （可运行 `python3 scripts/ratchet.py --update-baseline` 取新字典）")
        else:
            print(f"✓ {name}: {actual}/{expected}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
