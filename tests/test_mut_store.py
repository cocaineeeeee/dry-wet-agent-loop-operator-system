"""变异语料击杀：kernel/store.py read_events 的两条边界守门（MU2 K1/K3）。

两条变异都仍抛 StoreError（被 seq 跳跃校验兜底），故只查 pytest.raises(StoreError)
的旧断言无法区分——必须钉**专属报文**：
- K1 [P2]：seq 回退/重复守门 `seq <= last`。改成 < 后，重复 seq 不再由回退分支捕获，
  改由跳跃分支报错（"跳跃"而非"回退/重复"）。
- K3 [P2]：中间行损坏 `lineno == last_lineno`。改成 <= 后，中间坏行被当崩溃尾**静默跳过**，
  再由 seq 跳跃兜底报错（报文变"跳跃"而非"中间行损坏"）。
"""

import json

import pytest

from expos.kernel.store import RunStore, StoreError


def _seed(tmp_path, n=3):
    store = RunStore(tmp_path / "run")
    for i in range(n):
        store.append_event("checkpoint", {"round_id": i})
    return tmp_path / "run" / "events.jsonl"


def test_duplicate_seq_reported_as_regression_not_jump(tmp_path):
    """重复 seq（0,1,1）：回退/重复守门必须以"回退/重复"响亮抛。
    守门 <= → < 后重复 seq 改由跳跃分支报错 → 报文匹配必红。"""
    p = _seed(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[2])
    rec["seq"] = 1  # 制造 seq 回退/重复（前一条 last_seq=1）
    lines[2] = json.dumps(rec, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(StoreError, match="回退|重复"):
        RunStore(tmp_path / "run", create=False).read_events()


def test_middle_line_corruption_reported_as_corruption_not_jump(tmp_path):
    """中间行损坏（非物理末行）：必须以"中间行损坏"响亮抛，绝不当崩溃尾静默跳过。
    边界 == → <= 后中间坏行被跳过，仅由 seq 跳跃兜底（报文变"跳跃"）→ 匹配必红。"""
    p = _seed(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = '{"seq": 1, "kind": "checkpoint", broken'  # 中间行坏 JSON
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(StoreError, match="中间行损坏"):
        RunStore(tmp_path / "run", create=False).read_events()
