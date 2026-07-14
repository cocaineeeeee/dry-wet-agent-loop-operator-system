"""内核鲁棒性三件套的崩溃注入测试（族1/族3 深读实锤缺口的验收）。

覆盖：
1. torn-tail 容错（read_events 仅跳末行半写并告警；中间行损坏响亮抛）；
   append_event 单行原子写 + 首写前愈合 torn tail。
2. 改判路径 log-before-data（reclassify 崩溃窗口：事件已落、视图未改 → 重读不崩）。
3. 显式 seq 单调校验（回退/跳跃响亮抛；旧无 seq run 兼容读）。

依据 docs/EVENT_SCHEMA.md §0.1、docs/RUN_MANIFEST_SPEC.md §6、docs/CONTROLLER_MODEL.md 不变量⑤⑦。
"""

import json

import pytest

from expos.kernel.lifecycle import reclassify, route_observation
from expos.kernel.objects import Actor, Routing, TrustLevel
from expos.kernel.store import RunStore, StoreError

from tests.test_kernel import make_experiment, make_observation


def _events_path(root):
    return root / "run" / "events.jsonl"


def _seed_events(tmp_path, n=3):
    """写 n 条完整事件，返回 (store, events_path)。"""
    store = RunStore(tmp_path / "run")
    for i in range(n):
        store.append_event("routing", {"obs_id": f"obs_{i}", "round_id": 0,
                                        "trust": "TRUSTED", "routing": "TO_RESPONSE_MODEL",
                                        "confidence": 0.9})
    return store, _events_path(tmp_path)


# ---------------------------------------------------------------- ① torn tail 容错

def test_torn_tail_last_line_tolerated_and_warns(tmp_path, caplog):
    """崩溃在 append 中途：末行留半个 JSON。read_events 跳过它、返回其余，并 logging 告警。"""
    _store, p = _seed_events(tmp_path, 3)
    # 追加一行"半写"记录（无结尾换行、JSON 截断）——模拟单写者崩溃于 write 中途
    with p.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "ts": "2026-07-10T00:00:00Z", "kind": "routing", "payl')

    store2 = RunStore(tmp_path / "run", create=False)
    with caplog.at_level("WARNING"):
        events = store2.read_events()
    assert len(events) == 3  # 半写尾被丢弃，前 3 条完整返回
    assert [e["seq"] for e in events] == [0, 1, 2]
    # 不静默：有一条含文件与行号的 torn_tail 告警
    assert any("torn_tail" in r.message and "4" in r.getMessage() for r in caplog.records)


def test_torn_tail_healed_before_next_append(tmp_path):
    """恢复后再 append：首写前愈合 torn tail，追加后仍连续可读、seq 不断裂。"""
    _store, p = _seed_events(tmp_path, 3)
    with p.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "kind": "routing", "payl')  # 半写尾

    store2 = RunStore(tmp_path / "run", create=False)
    ev = store2.append_event("checkpoint", {"round_id": 1})
    assert ev["seq"] == 3  # 从末条有效 seq(2)+1 恢复，半写尾不占号
    reread = store2.read_events()
    assert [e["seq"] for e in reread] == [0, 1, 2, 3]  # 无中间坏行、连续可读


def test_middle_line_corruption_raises(tmp_path):
    """②的对照：损坏行不是物理最后一行（真损坏，非崩溃尾）→ 响亮抛 StoreError。"""
    _store, p = _seed_events(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = '{"seq": 1, "kind": "routing", broken'  # 第 2 行损坏，后面还有完整行
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store2 = RunStore(tmp_path / "run", create=False)
    with pytest.raises(StoreError):
        store2.read_events()


# ---------------------------------------------------------------- ③ seq 回退 / 跳跃

def test_seq_regression_raises(tmp_path):
    """seq 回退（并发写/篡改）→ 响亮抛。"""
    _store, p = _seed_events(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[2])
    rec["seq"] = 0  # 末行 seq 从 2 回退到 0
    lines[2] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(StoreError):
        RunStore(tmp_path / "run", create=False).read_events()


def test_seq_jump_raises(tmp_path):
    """seq 跳跃（丢记录/篡改）→ 响亮抛。"""
    _store, p = _seed_events(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[2])
    rec["seq"] = 9  # 2 → 9，跳跃
    lines[2] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(StoreError):
        RunStore(tmp_path / "run", create=False).read_events()


def test_legacy_events_without_seq_readable(tmp_path):
    """向后兼容：旧 run 无 seq 字段的事件行按行序补虚拟 seq、不抛。"""
    p = _events_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = [
        {"ts": "2026-07-01T00:00:00Z", "kind": "routing", "payload": {"obs_id": "a"}},
        {"ts": "2026-07-01T00:00:01Z", "kind": "routing", "payload": {"obs_id": "b"}},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in old), encoding="utf-8")

    store = RunStore(tmp_path / "run", create=False)
    events = store.read_events()
    assert [e["seq"] for e in events] == [0, 1]  # 按行序补虚拟值
    # 续接 append：新事件带真实 seq，从有效条数续号，仍连续可读
    ev = store.append_event("checkpoint", {"round_id": 0})
    assert ev["seq"] == 2
    assert [e["seq"] for e in store.read_events()] == [0, 1, 2]


# ---------------------------------------------------------------- ④ reclassify 崩溃窗口（log-before-data）

def test_reclassify_crash_between_log_and_view(tmp_path, monkeypatch):
    """模拟改判在"日志已落、视图未写"之间崩溃：save_observation 抛异常。

    log-before-data 保证：reclassification 事件 + OVERRIDE 决策已在日志中，
    观测视图仍是旧 trust（未被改写）。重读 store 不崩，且可据日志重放修复——
    对齐 CONTROLLER_MODEL 不变量⑤（WAL 先行、日志领先视图=安全偏斜）。"""
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = route_observation(store, make_observation(exp, suspicion=0.45))  # → SUSPECT/QUARANTINE
    assert obs.trust == TrustLevel.SUSPECT

    # 注入崩溃：改判物化视图那一步抛异常
    def boom(_obs):
        raise OSError("disk full at save_observation")

    monkeypatch.setattr(store, "save_observation", boom)
    with pytest.raises(OSError):
        reclassify(store, obs.obs_id, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL,
                   actor=Actor.HUMAN, reason="翻案证据")
    monkeypatch.undo()

    # 崩溃后新句柄重开：不崩，日志领先视图
    store2 = RunStore(tmp_path / "run", create=False)
    rc = store2.read_events("reclassification")
    assert len(rc) == 1 and rc[0]["payload"]["to_trust"] == "TRUSTED"  # 事件已落
    from expos.kernel.objects import DecisionKind
    assert len(store2.list_decisions(kind=DecisionKind.OVERRIDE)) == 1  # 审计已落
    # 视图未改（save 被崩），仍是旧 trust —— 日志领先视图，可重放修复，非审计丢失
    assert store2.load_observation(obs.obs_id).trust == TrustLevel.SUSPECT
    reread = store2.read_events()  # 全量重读不崩、seq 连续
    assert [e["seq"] for e in reread] == list(range(len(reread)))


# ---------------------------------------------------------------- M-4b：跨进程写锁

def test_writer_lock_blocks_concurrent_writer(tmp_path):
    """M-4b：两个 --resume 同目录并发 = 两个 lock=True 句柄。后到者取锁失败、响亮 StoreError；
    持锁者释放后可再取——覆盖单写者护栏（无锁则事件日志/checkpoint 会被交错写坏）。"""
    root = tmp_path / "run"
    s1 = RunStore(root, lock=True)
    try:
        with pytest.raises(StoreError):
            RunStore(root, create=False, lock=True)  # 并发写者被拒
    finally:
        s1.release_writer_lock()
    # 释放后可再取
    s2 = RunStore(root, create=False, lock=True)
    s2.release_writer_lock()


def test_writer_lock_optional_readers_unaffected(tmp_path):
    """默认 lock=False：只读/多句柄读路径不受锁影响（可与写锁并存、互不阻塞）。"""
    root = tmp_path / "run"
    writer = RunStore(root, lock=True)
    try:
        r1 = RunStore(root, create=False)  # 无锁读句柄
        r2 = RunStore(root, create=False)
        assert r1.read_events() == [] == r2.read_events()
    finally:
        writer.release_writer_lock()


# ---------------------------------------------------------------- ⑤ 尾损诊断与自愈（expos check）
#
# scan_events_tail 三类尾损分治（交接建议 3+收紧 3）：半行截断 / 末行坏 JSON → truncated（可愈）；
# 中段坏行 → corrupt（结构性拒修，水位后未直达 EOF）。判据复用 _parse_line（不重复实现）。

def test_scan_tail_clean(tmp_path):
    """全部有效 → status=clean、水位=EOF。"""
    _store, p = _seed_events(tmp_path, 3)
    scan = RunStore(tmp_path / "run", create=False).scan_events_tail()
    assert scan["status"] == "clean"
    assert scan["valid_up_to_byte"] == scan["size"]
    assert scan["valid_up_to_line"] == 3 and scan["first_bad_line"] is None


def test_scan_tail_half_line_truncated(tmp_path):
    """崩溃半写尾（无换行、JSON 截断）→ truncated、水位在末条完整行后。"""
    _store, p = _seed_events(tmp_path, 3)
    watermark = p.stat().st_size
    with p.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "kind": "routing", "payl')
    scan = RunStore(tmp_path / "run", create=False).scan_events_tail()
    assert scan["status"] == "truncated"
    assert scan["valid_up_to_byte"] == watermark and scan["valid_up_to_line"] == 3
    assert scan["first_bad_line"] == 4


def test_scan_tail_last_line_bad_json_truncated(tmp_path):
    """末行坏 JSON（完整成行、有换行，但其后直达 EOF）→ truncated（可愈）。"""
    _store, p = _seed_events(tmp_path, 3)
    watermark = p.stat().st_size
    with p.open("a", encoding="utf-8") as f:
        f.write("{not valid json}\n")
    scan = RunStore(tmp_path / "run", create=False).scan_events_tail()
    assert scan["status"] == "truncated"
    assert scan["valid_up_to_byte"] == watermark and scan["first_bad_line"] == 4


def test_scan_tail_mid_corruption_is_corrupt(tmp_path):
    """中段坏行（其后仍有非空行）→ corrupt：水位后未直达 EOF，结构性拒修。"""
    _store, p = _seed_events(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = "{broken middle}"  # 第 2 行坏，后仍有第 3 行
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    scan = RunStore(tmp_path / "run", create=False).scan_events_tail()
    assert scan["status"] == "corrupt"
    assert scan["first_bad_line"] == 2


def test_truncate_events_tail_heals_and_backs_up(tmp_path):
    """truncate_events_tail 截到水位 + 备份 .pre_fix；愈合后 read_events 干净返回前缀。"""
    store, p = _seed_events(tmp_path, 3)
    with p.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "kind": "routing", "payl')
    store2 = RunStore(tmp_path / "run", create=False)
    scan = store2.scan_events_tail()
    backup = store2.truncate_events_tail(scan)
    assert backup.exists() and backup.read_bytes()  # 原文件已备份
    assert [e["seq"] for e in store2.read_events()] == [0, 1, 2]  # 尾损已去、连续可读


def test_truncate_refuses_corrupt(tmp_path):
    """corrupt 报告传入 truncate → 响亮 StoreError（防误截中段损坏）。"""
    _store, p = _seed_events(tmp_path, 3)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = "{broken middle}"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store2 = RunStore(tmp_path / "run", create=False)
    scan = store2.scan_events_tail()
    with pytest.raises(StoreError):
        store2.truncate_events_tail(scan)
