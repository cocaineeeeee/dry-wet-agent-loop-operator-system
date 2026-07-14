#!/usr/bin/env bash
# preflight_e2e.sh —— 投稿/发版前门禁：干净环境整体 E2E（红队 WHO3 风险#3 落地）。
#
# 背景：`mailbox/red_to_blue/014_who3-holistic-verdict.md` 风险#3 指出「干净环境整体 E2E
# 从未跑过」；本脚本把 fresh-clone → install → 全量 pytest → 小扫描冒烟 → lint/docs
# 串成一道**投稿/发版前必须全 PASS 的门**。不依赖当前解释器已装的包（venv 全新安装），
# 专门用来暴露「本机 site-packages 掩盖、干净 runner 会炸」的坑（如 W3-1 hypothesis 未声明）。
#
# 用法：
#   bash scripts/preflight_e2e.sh                # 完整五段；任一段失败即停
#   bash scripts/preflight_e2e.sh --keep-workdir  # 失败/成功都保留临时目录（默认失败保留、成功清理）
#   bash scripts/preflight_e2e.sh --workdir DIR   # 指定临时目录（默认 mktemp）
#
# 五段：
#   1) fresh-clone-sim  —— rsync 仓库到临时目录，排除 runs/ references/ __pycache__ .hypothesis
#   2) venv-install      —— 全新 venv + `pip install -e ".[dev]"`
#   3) pytest-full       —— 全量 `pytest -q`（PYTHONDONTWRITEBYTECODE=1，线程钉 1）
#   4) smoke-run-cell    —— gen_sweep 出 S0.demo 场景 + run_cell(naive, seed=3, rounds=2) 出 score.json
#   5) lint-docs         —— expos_lint（error+warn 全仓）+ `mkdocs build --strict`
#
# 任一段失败：立即停止（不跑后续段），打印明确的失败段落名与日志路径，
# 结尾仍打印五段 PASS/FAIL/SKIP 汇总表，非零退出。
#
# 门禁纪律见 docs/ENGINEERING.md「投稿前门禁」小节：投稿/发版前必须本脚本全 PASS。

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_WORKDIR=0
WORKDIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-workdir) KEEP_WORKDIR=1; shift ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,25p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$WORKDIR" ]]; then
    WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/expos_preflight_e2e.XXXXXX")"
fi
LOGDIR="$WORKDIR/_logs"
mkdir -p "$LOGDIR"

CLONE_DIR="$WORKDIR/clone"
VENV_DIR="$WORKDIR/venv"
SMOKE_DIR="$WORKDIR/smoke"

SEGMENT_NAMES=(
    "1-fresh-clone-sim"
    "2-venv-install"
    "3-pytest-full"
    "4-smoke-run-cell"
    "5-lint-docs"
)
declare -A SEGMENT_STATUS
declare -A SEGMENT_NOTE
for n in "${SEGMENT_NAMES[@]}"; do
    SEGMENT_STATUS["$n"]="SKIP"
    SEGMENT_NOTE["$n"]="未运行（前序段落已失败或未到达）"
done

FAILED_SEGMENT=""

print_summary() {
    echo
    echo "================ preflight_e2e 五段汇总 ================"
    printf "%-20s %-6s %s\n" "段落" "结果" "备注"
    for n in "${SEGMENT_NAMES[@]}"; do
        printf "%-20s %-6s %s\n" "$n" "${SEGMENT_STATUS[$n]}" "${SEGMENT_NOTE[$n]}"
    done
    echo "=========================================================="
    echo "临时工作目录: $WORKDIR"
    if [[ -n "$FAILED_SEGMENT" ]]; then
        echo "首个失败段落: $FAILED_SEGMENT（日志见 $LOGDIR）"
    fi
}

fail_and_exit() {
    local seg="$1" reason="$2"
    SEGMENT_STATUS["$seg"]="FAIL"
    SEGMENT_NOTE["$seg"]="$reason"
    FAILED_SEGMENT="$seg"
    echo
    echo ">>> [FAIL] 段落 $seg：$reason" >&2
    echo ">>> 详细日志：$LOGDIR/${seg}.log" >&2
    print_summary
    if [[ "$KEEP_WORKDIR" -eq 0 ]]; then
        echo "（失败默认保留临时目录以便排查：$WORKDIR）"
    fi
    exit 1
}

pass_segment() {
    local seg="$1" note="$2"
    SEGMENT_STATUS["$seg"]="PASS"
    SEGMENT_NOTE["$seg"]="$note"
    echo ">>> [PASS] 段落 $seg：$note"
}

echo "=== expos preflight_e2e：投稿/发版前门禁（干净环境整体 E2E） ==="
echo "仓库根: $REPO_ROOT"
echo "工作目录: $WORKDIR"
echo

# ---------------------------------------------------------------- 1) fresh-clone-sim
seg="1-fresh-clone-sim"
echo "--- 段落 $seg: rsync 仓库到临时目录（排除 runs/ references/ __pycache__ .hypothesis）---"
mkdir -p "$CLONE_DIR"
rsync -a \
    --exclude 'runs/' \
    --exclude 'references/' \
    --exclude '__pycache__/' \
    --exclude '.hypothesis/' \
    "$REPO_ROOT/" "$CLONE_DIR/" > "$LOGDIR/${seg}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    fail_and_exit "$seg" "rsync 退出码 $rc"
fi
if [[ ! -f "$CLONE_DIR/pyproject.toml" ]]; then
    fail_and_exit "$seg" "克隆目录缺 pyproject.toml，rsync 结果不完整"
fi
pass_segment "$seg" "克隆到 $CLONE_DIR（$(du -sh "$CLONE_DIR" 2>/dev/null | cut -f1) ）"

# ---------------------------------------------------------------- 2) venv-install
seg="2-venv-install"
echo "--- 段落 $seg: 全新 venv + pip install -e \".[dev]\" ---"
python3 -m venv "$VENV_DIR" >> "$LOGDIR/${seg}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    fail_and_exit "$seg" "python3 -m venv 退出码 $rc"
fi
PYBIN="$VENV_DIR/bin/python3"
PIPBIN="$VENV_DIR/bin/pip"
"$PIPBIN" install --upgrade pip >> "$LOGDIR/${seg}.log" 2>&1
( cd "$CLONE_DIR" && "$PIPBIN" install -e ".[dev]" ) >> "$LOGDIR/${seg}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    fail_and_exit "$seg" "pip install -e \".[dev]\" 退出码 $rc（常见根因：dev extra 缺依赖，见日志）"
fi
pass_segment "$seg" "venv=$VENV_DIR，pip install -e \".[dev]\" 成功"

# ---------------------------------------------------------------- 3) pytest-full
seg="3-pytest-full"
echo "--- 段落 $seg: 全量 pytest -q（PYTHONDONTWRITEBYTECODE=1，线程钉 1）---"
(
    cd "$CLONE_DIR"
    export PYTHONDONTWRITEBYTECODE=1
    export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
    "$PYBIN" -m pytest -q
) > "$LOGDIR/${seg}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    tail -n 30 "$LOGDIR/${seg}.log" >&2
    fail_and_exit "$seg" "pytest 退出码 $rc（见上方尾部日志 / $LOGDIR/${seg}.log 全文）"
fi
summary_line="$(grep -E '^[0-9]+ (passed|failed|error)' "$LOGDIR/${seg}.log" | tail -1)"
pass_segment "$seg" "${summary_line:-pytest 退出码 0}"

# ---------------------------------------------------------------- 4) smoke-run-cell
seg="4-smoke-run-cell"
echo "--- 段落 $seg: 小扫描冒烟（gen_sweep 出 S0.demo + run_cell naive seed=3 rounds=2）---"
mkdir -p "$SMOKE_DIR"
(
    cd "$CLONE_DIR"
    export PYTHONDONTWRITEBYTECODE=1
    "$PYBIN" scripts/gen_sweep.py --out "$SMOKE_DIR" --arms naive
) > "$LOGDIR/${seg}.log" 2>&1
rc=$?
DOMAIN_YAML="$SMOKE_DIR/scenarios/S0.demo.yaml"
if [[ $rc -ne 0 || ! -f "$DOMAIN_YAML" ]]; then
    fail_and_exit "$seg" "gen_sweep 未产出 $DOMAIN_YAML（退出码 $rc）"
fi
(
    cd "$CLONE_DIR"
    export PYTHONDONTWRITEBYTECODE=1
    "$PYBIN" -m expos.eval.run_cell \
        --domain "$DOMAIN_YAML" --arm naive --scenario S0.demo \
        --seed 3 --rounds 2 --out-root "$SMOKE_DIR/runs"
) >> "$LOGDIR/${seg}.log" 2>&1
rc=$?
SCORE_JSON="$SMOKE_DIR/runs/S0.demo__naive__s3/report/score.json"
if [[ $rc -ne 0 ]]; then
    tail -n 30 "$LOGDIR/${seg}.log" >&2
    fail_and_exit "$seg" "run_cell 退出码 $rc"
fi
if [[ ! -f "$SCORE_JSON" ]]; then
    fail_and_exit "$seg" "run_cell 退出 0 但未见 $SCORE_JSON"
fi
pass_segment "$seg" "score.json 已生成：$SCORE_JSON"

# ---------------------------------------------------------------- 5) lint-docs
seg="5-lint-docs"
echo "--- 段落 $seg: expos_lint（全仓 error+warn）+ mkdocs build --strict ---"
(
    cd "$CLONE_DIR"
    "$PYBIN" scripts/expos_lint.py
) > "$LOGDIR/${seg}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    tail -n 30 "$LOGDIR/${seg}.log" >&2
    fail_and_exit "$seg" "expos_lint 命中红线（退出码 $rc）"
fi
# docs 依赖：若 pyproject 已声明 docs extra（W3 路计划补）就装它，否则退回显式装
# mkdocs + mkdocs-material（当前已知口径缺口，见 mailbox W3 审计「mkdocs/mkdocs-material
# 无 extra 声明」——本脚本临时兜底，不代表口径已补齐）。
if grep -q '^\s*docs\s*=' "$CLONE_DIR/pyproject.toml"; then
    ( cd "$CLONE_DIR" && "$PIPBIN" install -e ".[docs]" ) >> "$LOGDIR/${seg}.log" 2>&1
    docs_install_rc=$?
else
    "$PIPBIN" install mkdocs mkdocs-material >> "$LOGDIR/${seg}.log" 2>&1
    docs_install_rc=$?
fi
if [[ $docs_install_rc -ne 0 ]]; then
    fail_and_exit "$seg" "docs 依赖安装失败（退出码 $docs_install_rc）"
fi
(
    cd "$CLONE_DIR"
    "$PYBIN" -m mkdocs build --strict
) >> "$LOGDIR/${seg}.log" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
    tail -n 30 "$LOGDIR/${seg}.log" >&2
    fail_and_exit "$seg" "mkdocs build --strict 退出码 $rc"
fi
pass_segment "$seg" "expos_lint 全绿 + mkdocs --strict 无 warning/error"

# ---------------------------------------------------------------- 全 PASS
print_summary
if [[ "$KEEP_WORKDIR" -eq 0 ]]; then
    rm -rf "$WORKDIR"
    echo "（全 PASS，已清理临时目录；加 --keep-workdir 可保留）"
fi
exit 0
