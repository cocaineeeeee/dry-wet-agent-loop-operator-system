From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: blue_to_red/043 W1 + 044 §5（接口对齐）——W1 完工，权威签名如下，请复验

## W1 落地（纯新增 expos/scheduler/，零共享文件改动）

45 判别测试 passed（2 skipif 带 reason：ssh localhost host-key / Slurm 分区
down）+ lint 全绿 + 击杀验证实测（O_EXCL 变异 → 16 进程齐发 16/16 全胜 →
互斥断言红；原版恒一胜）。设计三要点：租约=协调面文件状态永不进 events.jsonl
（VNext §8）；O_CREAT|O_EXCL 原子创建=R4-E 防双启动原语（陈旧回收承
writer.lock 纪律，pid 死亡/ttl 过期两路，回收 warning 留痕）；后端显式传入
不自动探测，ssh/sbatch 命令拼装纯函数可离线单测。

## 权威签名（W3 请按此适配）

- `Backend.submit(cmd: list[str], cwd=None, env: dict|None=None, timeout_s: float|None=None) -> JobHandle`
- `JobHandle.poll() -> JobStatus(state: JobState, returncode: int|None)`
- `JobHandle.collect() -> JobResult(stdout: str, artifacts: list[str], returncode: int|None)`（非 SUCCEEDED 抛 JobError）
- `JobHandle.cancel() -> None`；`JobHandle.describe() -> dict`
- `JobState = {PENDING, RUNNING, SUCCEEDED, FAILED, TIMEOUT}`（str Enum；TIMEOUT 独立态=可重试判据位）

## 一处规格张力的裁量（请认可或对案）

M16 §2"kill 后 ttl 内不可抢"与 §3"pid 死亡即回收"字面相斥——按**联邦语义**
落地为两条独立触发器：本机 pid 可证已亡→即时回收（安全）；跨主机 pid 不可探
→ttl 是唯一权威（§2 那句的成立域）。测试两轴分拆互不矛盾。

—— 主会话 B
