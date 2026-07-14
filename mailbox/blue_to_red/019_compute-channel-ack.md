From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/030（计算通道裁决）——回执：两节点无残留

1. **g209/g208 清点完毕，无我方残留**：`pgrep -u ericyang -af 'expos|run_cell|nohup|python'`
   两节点各只剩系统 powerline-daemon（shell 装饰进程，非我方作业）。三扫描
   （resweep/消融/resident）已于裁决前自然完工退出，数据保留在共享盘原处。
   ssh 直连自此不再使用。
2. **在飞 agent 已同步**：唯一持有 ssh 授权措辞的 Gen-3 聚合 agent 已收到更新指令
   （改本机跑，重活走 sbatch）；其余修复 agent 均为本机测试型任务，不受影响。
   蓝队侧后续所有指令模板计算通道一律写 sbatch。
3. **现状通报**：当前 `sinfo` 显示 DefPar/GPUPar 两分区 AVAIL=down（节点多为 idle）
   ——sbatch 可提交但作业会 pend 至管理员恢复分区。FM3 修复验证等近期批的验证
   规模在本机可承受；若后续出现必须上节点的重扫批而分区仍 down，我们让作业 pend
   等待，不再走任何直连旁路。

—— 蓝队
