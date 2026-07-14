#!/bin/bash
#SBATCH --job-name=expos_readiness
#SBATCH --partition=DefPar
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --output=/Data1/ericyang/dry_wet_agent_os/runs/sbatch_logs/%x_%j.out

# M23 Phase 5 A-段 取证跑（重批一律 sbatch；用户纪律）。
# 一发跑全 18 场景（heavy full-loop + crash 矩阵 + 编排/差分/loud），产
# runs/readiness_evidence/ 全集 + 顶层 evidence_index.json（含本作业号实录）。
# 幂等：脚本对每场景先 rmtree 再重建，重复提交安全。
# 路径纪律：脚本内部一律从 repo 根解析绝对路径（runs/readiness_evidence），
# 无 4670043 相对路径虫的暴露面；OUT/日志亦绝对。
#
# 用法：
#   mkdir -p /Data1/ericyang/dry_wet_agent_os/runs/sbatch_logs
#   sbatch scripts/sbatch/readiness_evidence.sbatch
# 单场景补跑（本机轻量即可，无需 sbatch）：
#   python scripts/generate_readiness_evidence.py gen --only <scenario_id>

set -euo pipefail
cd /Data1/ericyang/dry_wet_agent_os

echo "[readiness] node=$(hostname) job=${SLURM_JOB_ID:-local} start=$(date -Is)"

python scripts/generate_readiness_evidence.py gen \
    --sbatch-job-ids "${SLURM_JOB_ID:-local}"

echo "[readiness] gen done, running structural verify ..."
python scripts/generate_readiness_evidence.py verify

echo "[readiness] end=$(date -Is)"
