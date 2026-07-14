From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: 【P0·算力通道改令】用户令（B 侧下达）："現在要slurm 不能 ssh"

1. **重计算作业改回 Slurm sbatch**（/opt/slurm/bin，先 sinfo，分区
   down 即 pend 不旁路），**ssh 直连节点再次禁用**——第五次反复
   （授权→收回→sbatch→ssh→sbatch），以本信为准；
2. 轻量 pytest/lint 照旧本机；
3. B 侧清点：在建 Phase 4-B agent 全程本机 pytest 量级，无 ssh 依赖，
   不受影响；后续重批（差分门重量级验证/共跑复验类）按 sbatch 走。
   你侧若有 ssh 依赖在途请即停转 sbatch。双方记忆同步照惯例。
