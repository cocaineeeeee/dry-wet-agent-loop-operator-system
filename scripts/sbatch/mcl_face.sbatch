#!/bin/bash
#SBATCH --job-name=expos_mcl_face
#SBATCH --partition=DefPar
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --output=runs/sbatch_logs/%x_%j.out

# expos 四条件共跑单条件提交模板（M17 收官件；用户裁定：重活一律 sbatch）。
# 参数已按信 093(B 裁定) 定稿：replicates=8（yaml 已改，功效只在证据源侧购买）、
# 四条件铺满裁决空间（flat=无信号负控 / consistent-zero=零效应冻结对照，诚实恒
# insufficient / consistent-strong=SUPPORTED 路径（新真值面 polar_high_strong，
# mu=0.70）/ flipped=REJECTED 路径）。
# 用法（每条件一发，四发并行）：
#   mkdir -p runs/sbatch_logs
#   sbatch --export=ALL,FACE=flat,OUT=runs/corun_flat                        scripts/sbatch/mcl_face.sbatch
#   sbatch --export=ALL,FACE=polar_high,OUT=runs/corun_consistent_zero      scripts/sbatch/mcl_face.sbatch
#   sbatch --export=ALL,FACE=polar_high_strong,OUT=runs/corun_consistent_strong scripts/sbatch/mcl_face.sbatch
#   sbatch --export=ALL,FACE=nonpolar_high,OUT=runs/corun_flipped           scripts/sbatch/mcl_face.sbatch
# 可覆写：DOMAIN（默认 solvent_screen.yaml）、ROUNDS（默认 4）、SEED（默认 7）。
# 仍余一个发车前置：B 侧 resume 等式红裁定通过（信 090 共识——K5 replay 门
# 站在 resume 等式上）。裁定通过前勿正式起跑。

set -euo pipefail
cd /Data1/ericyang/dry_wet_agent_os

FACE="${FACE:?need FACE=polar_high|nonpolar_high|flat}"
OUT="${OUT:?need OUT=runs/<name>}"
# 相对 OUT 会让 dry worker（cwd=作业目录）解析不到 argv 路径 → 全员 dry_failed
# （4670043-46 一役的根因）；一律转绝对。
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT";; esac
DOMAIN="${DOMAIN:-domains/solvent_screen.yaml}"
ROUNDS="${ROUNDS:-4}"
SEED="${SEED:-7}"

echo "[mcl_face] face=${FACE} domain=${DOMAIN} rounds=${ROUNDS} seed=${SEED} out=${OUT} node=$(hostname)"

python - <<PY
from pathlib import Path
from expos.mcl import run_mcl_loop
from expos.planner.certification import AggregatedCertification
from expos.qc.certification_stats import AggregationConfig, ClaimHead

# K-E 落地参数（信 093/095）：单极性 head，eth vs acn 两臂（活环晋升面），
# replicates=8 基底（yaml），默认 AggregationConfig 阈值（守卫关、r_min=2）。
head = ClaimHead(
    claim_id="c_polar_responds_higher",
    statement="polar solvents give a higher plate-reader response",
    favorable_direction="higher",
    focal_group=("cand_ethanol",),
    reference_group=("cand_acetonitrile",),
)
cert = AggregatedCertification(
    [head], config=AggregationConfig(run_fingerprint="corun_${FACE}")
)
result = run_mcl_loop(
    Path("${DOMAIN}"),
    rounds=${ROUNDS},
    seed=${SEED},
    out_dir=Path("${OUT}"),
    truth_profile="${FACE}",
    certification=cert,
)
print("[mcl_face] run finished:", result)
PY

echo "[mcl_face] done."
