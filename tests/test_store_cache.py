"""RunStore 观测内存缓存验收（M-2 热径修复）。

红线：cache_observations=True 时 list_observations 走内存态、免全量磁盘重扫，
但缓存必须是**落盘的严格镜像**——逐字段与磁盘读回值一致，且写路径全覆盖
（save_observation / route_observation / reclassify）、redo 对账后强制重建。
默认关闭时行为零变化。性能冒烟：缓存路径下 list_observations 的 glob 次数不随调用数增长。
"""

import pathlib

import pytest

from expos.kernel.lifecycle import reclassify, route_observation
from expos.kernel.objects import Actor, Routing, TrustLevel
from expos.kernel.store import RunStore
from tests.test_kernel import make_experiment, make_observation


def _dump_all(store: RunStore):
    """按内容序列化全体观测，供逐字段比对（隔离对象身份、只看落盘等价内容）。"""
    return [o.model_dump(mode="json") for o in store.list_observations()]


def _seed_multi(store: RunStore, n_rounds: int = 3, per_round: int = 4):
    """跨轮多观测入盘（不同 round_id / well_id / trust），经 route_observation 落定信任。"""
    wells = ["A1", "B2", "C4", "D7"]
    for rid in range(n_rounds):
        exp = make_experiment(round_id=rid)
        store.save_experiment(exp)
        for k in range(per_round):
            susp = 0.7 if k % 2 else 0.0  # 交替 SUSPECT / TRUSTED
            obs = make_observation(exp, suspicion=susp)
            obs.layout_meta = obs.layout_meta.model_copy(update={"well_id": wells[k % len(wells)]})
            route_observation(store, obs)


# ---------------------------------------------------------------- 缓存 vs 磁盘逐字段一致

def test_cache_matches_disk_field_by_field(tmp_path):
    cached = RunStore(tmp_path / "run", cache_observations=True)
    _seed_multi(cached)
    # 同目录另开一个**缓存关闭**的只读句柄 → 纯磁盘真相
    disk = RunStore(tmp_path / "run", create=False, cache_observations=False)

    assert _dump_all(cached) == _dump_all(disk)
    # 过滤档同样一致（round_id / trust 两轴）
    for rid in range(3):
        assert (_dump(cached, round_id=rid) == _dump(disk, round_id=rid))
    for tr in (TrustLevel.TRUSTED, TrustLevel.SUSPECT, TrustLevel.FAILED):
        assert _dump(cached, trust=tr) == _dump(disk, trust=tr)


def _dump(store, **kw):
    return [o.model_dump(mode="json") for o in store.list_observations(**kw)]


def test_cache_reflects_new_save_after_build(tmp_path):
    """缓存已建后再落盘的观测：不 glob 也能命中，且与磁盘一致。"""
    cached = RunStore(tmp_path / "run", cache_observations=True)
    exp = make_experiment(round_id=0)
    cached.save_experiment(exp)
    route_observation(cached, make_observation(exp, suspicion=0.0))
    cached.list_observations()  # 首建缓存
    route_observation(cached, make_observation(exp, suspicion=0.7))  # 建后再落盘
    disk = RunStore(tmp_path / "run", create=False)
    assert _dump_all(cached) == _dump_all(disk)
    assert len(cached.list_observations()) == 2


# ---------------------------------------------------------------- reclassify 后一致

def test_cache_consistent_after_reclassify(tmp_path):
    cached = RunStore(tmp_path / "run", cache_observations=True)
    exp = make_experiment(round_id=0)
    cached.save_experiment(exp)
    obs = route_observation(cached, make_observation(exp, suspicion=0.7))  # → SUSPECT
    cached.list_observations()  # 建缓存（含旧 SUSPECT 态）
    # 高危翻案 SUSPECT→TRUSTED（仅 HUMAN，reason 非空）——内部走 save_observation
    reclassify(cached, obs.obs_id, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL,
               actor=Actor.HUMAN, reason="人工复核确认可信")
    got = cached.list_observations()[0]
    assert got.trust == TrustLevel.TRUSTED  # 缓存反映改判
    assert got.trust_confidence == 1.0
    disk = RunStore(tmp_path / "run", create=False)
    assert _dump_all(cached) == _dump_all(disk)


# ---------------------------------------------------------------- redo 对账后强制重建

def test_cache_rebuilt_after_reconcile_redo(tmp_path):
    cached = RunStore(tmp_path / "run", cache_observations=True)
    _seed_multi(cached, n_rounds=2, per_round=3)
    before = cached.list_observations()  # 建缓存
    assert any(o.round_id == 1 for o in before)
    payload = cached.reconcile_redo_rounds(from_round=1)  # 直接 unlink round≥1 → 应失效缓存
    assert payload is not None and payload["n_observations_removed"] > 0

    after = cached.list_observations()  # 强制重建
    assert all(o.round_id < 1 for o in after)  # round1 孤儿不再出现
    disk = RunStore(tmp_path / "run", create=False)
    assert _dump_all(cached) == _dump_all(disk)  # 与磁盘真相一致


# ---------------------------------------------------------------- 默认关闭：零变化

def test_default_off_never_builds_cache(tmp_path):
    store = RunStore(tmp_path / "run")  # 默认 cache_observations=False
    assert store._cache_observations is False
    _seed_multi(store, n_rounds=2, per_round=2)
    store.list_observations()
    route_observation(store, make_observation(make_experiment(0)))
    assert store._obs_cache is None  # 从不建缓存

    # 与显式缓存句柄输出逐字段一致（默认关闭不改任何可观测行为）
    cached = RunStore(tmp_path / "run", create=False, cache_observations=True)
    assert _dump_all(store) == _dump_all(cached)


# ---------------------------------------------------------------- 性能冒烟：glob 次数

def test_cache_reduces_glob_calls(tmp_path, monkeypatch):
    """缓存开启后，重复 list_observations 的观测目录 glob 次数不随调用数增长（O(1) 命中）；
    关闭时每次 list 都 glob（O(N) 重扫）。计 Path.glob 调用数为代理。"""
    counter = {"n": 0}
    orig = pathlib.Path.glob

    def counting_glob(self, pattern):
        counter["n"] += 1
        return orig(self, pattern)

    # 先播种（用独立无监控句柄，避免播种期 glob 计入）
    seed_store = RunStore(tmp_path / "run", cache_observations=True)
    _seed_multi(seed_store, n_rounds=2, per_round=3)

    monkeypatch.setattr(pathlib.Path, "glob", counting_glob)

    # 关闭缓存：5 次 list → 5 次观测目录 glob
    off = RunStore(tmp_path / "run", create=False, cache_observations=False)
    counter["n"] = 0
    for _ in range(5):
        off.list_observations()
    globs_off = counter["n"]

    # 开启缓存：5 次 list → 仅首建 1 次 glob
    on = RunStore(tmp_path / "run", create=False, cache_observations=True)
    counter["n"] = 0
    for _ in range(5):
        on.list_observations()
    globs_on = counter["n"]

    assert globs_off == 5, f"关闭态应每次 glob，实测 {globs_off}"
    assert globs_on == 1, f"开启态仅首建 glob 一次，实测 {globs_on}"


# ================================================================ OS3 §一：物化视图故障隔离

import logging
from pathlib import Path

from expos.loop import run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"


def _inject_bad_obs(run_dir) -> tuple[Path, Path]:
    """注入两个坏观测文件：非 UTF-8 字节 ×1 + 坏 JSON ×1。返回二者路径。"""
    d = Path(run_dir) / "observations"
    d.mkdir(parents=True, exist_ok=True)
    bad_utf8 = d / "obs_bad_utf8.json"
    bad_utf8.write_bytes(b"\xff\xfe not valid utf-8 \x80\x00")
    bad_json = d / "obs_bad_json.json"
    bad_json.write_text('{"obs_id": "x", "trust": ', encoding="utf-8")  # 截断坏 JSON
    return bad_utf8, bad_json


@pytest.mark.parametrize("cache", [False, True])
def test_bad_obs_file_isolated_not_dos(tmp_path, caplog, cache):
    """单坏 obs 文件（非 UTF-8 + 坏 JSON）不使 list_observations 裸 traceback DoS：
    隔离坏文件 + 登记 quarantined_files + logging.error 留痕 + 其余观测照常可读。"""
    store = RunStore(tmp_path / "run", cache_observations=cache)
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))  # 一个好观测
    bad_utf8, bad_json = _inject_bad_obs(tmp_path / "run")

    with caplog.at_level(logging.ERROR):
        good = store.list_observations()  # 不炸
    assert len(good) == 1  # 坏文件被隔离，好观测照常返回
    assert len(store.quarantined_files) == 2
    assert str(bad_utf8) in store.quarantined_files
    assert str(bad_json) in store.quarantined_files
    assert any("view_quarantine" in r.getMessage() for r in caplog.records)


def test_export_view_survives_bad_obs(tmp_path):
    """export_view（agent 只读快照）走 list_observations，同样受隔离庇护、不 DoS。"""
    store = RunStore(tmp_path / "run")
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))
    _inject_bad_obs(tmp_path / "run")
    view = store.export_view()  # 不抛
    assert len(view.observations) == 1


def test_resume_survives_bad_obs_and_logs_event(tmp_path):
    """写者路径（--resume）遇坏 obs 文件不炸，且 loop 首轮前落 view_quarantine 事件防静默。"""
    out = tmp_path / "run"
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=11, out_dir=out)
    _inject_bad_obs(out)
    # resume 再跑一轮：reconcile/模型重建都过 list_observations——不炸
    run_loop(CRYSTAL, mode="naive", rounds=3, seed=11, out_dir=out, resume=True)
    s = RunStore(out, create=False)
    vq = s.read_events("view_quarantine")
    assert len(vq) >= 1
    assert vq[0]["payload"]["n_quarantined"] == 2


# ---------------------------------------------------------------- OS3 §一(b)：check 扫视图

def test_scan_view_files_flags_bad(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))
    _inject_bad_obs(tmp_path / "run")
    rep = store.scan_view_files()
    assert rep["n_bad"] == 2


def test_cli_check_bad_view_not_clean(tmp_path, capsys):
    """expos check：events 尾部 clean 但物化视图有坏文件 → 非 clean（退出码 1）。"""
    from expos import cli
    out = tmp_path / "run"
    run_loop(CRYSTAL, mode="naive", rounds=1, seed=11, out_dir=out)
    _inject_bad_obs(out)
    rc = cli.main(["check", str(out)])
    assert rc == 1  # _CHECK_TRUNCATED_DIAGNOSED：可诊断问题非 clean


def test_cli_check_fix_refused_when_locked(tmp_path):
    """check --fix 取 writer.lock：另一写者持锁时截断被拒（与 loop 同协议）。"""
    from expos import cli
    from expos.kernel.store import RunStore as RS
    out = tmp_path / "run"
    run_loop(CRYSTAL, mode="naive", rounds=1, seed=11, out_dir=out)
    # 制造一个可修尾损（events.jsonl 追加半行坏 JSON）
    ev = out / "events.jsonl"
    with ev.open("a", encoding="utf-8") as f:
        f.write('{"seq": 999, "half')  # 无换行的坏尾
    holder = RS(out, create=False, lock=True)  # 另一写者持锁
    try:
        rc = cli.main(["check", str(out), "--fix", "--yes"])
    finally:
        holder.release_writer_lock()
    assert rc == 1  # 取锁失败 → 拒绝修复（未截断）


# ---------------------------------------------------------------- OS3/裁决 P0：视图健康分区

def _healthy_store(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))  # 落 events + obs
    (store.root / "models").mkdir(exist_ok=True)
    (store.root / "models" / "snapshot_r0.json").write_text('{"round_id": 0}', encoding="utf-8")
    return store


def test_view_health_six_sections_healthy(tmp_path):
    """六项分区齐全；健康 run：events/obs/exp/snapshot healthy，score/lineage missing（advisory）→ overall healthy。"""
    h = _healthy_store(tmp_path).scan_view_health()
    assert set(h["sections"]) == {"events", "observations", "experiments",
                                  "score", "lineage", "snapshot"}
    assert h["sections"]["events"]["status"] == "healthy"
    assert h["sections"]["observations"]["status"] == "healthy"
    assert h["sections"]["snapshot"]["status"] == "healthy"
    assert h["sections"]["score"]["status"] == "missing"    # 评测未跑
    assert h["sections"]["lineage"]["status"] == "missing"  # lineage 未物化
    assert h["overall"] == "healthy"  # missing 是 advisory，不置 degraded


def test_view_health_stale_score_degraded(tmp_path):
    """score.json 旧于 events.jsonl → stale（不装正常）→ overall degraded。"""
    import os as _os
    store = _healthy_store(tmp_path)
    (store.root / "report").mkdir(exist_ok=True)
    score = store.root / "report" / "score.json"
    score.write_text('{"final_regret": 0.1}', encoding="utf-8")
    ev_mtime = (store.root / "events.jsonl").stat().st_mtime
    _os.utime(score, (ev_mtime - 100, ev_mtime - 100))  # 令 score 旧于 events
    h = store.scan_view_health()
    assert h["sections"]["score"]["status"] == "stale"
    assert h["overall"] == "degraded"


def test_view_health_snapshot_corrupt_but_events_replayable(tmp_path):
    """坏 model snapshot → quarantined（model 降级），但 raw event replay 不受影响。"""
    store = _healthy_store(tmp_path)
    (store.root / "models" / "snapshot_r0.json").write_text("{corrupt json", encoding="utf-8")
    h = store.scan_view_health()
    assert h["sections"]["snapshot"]["status"] == "quarantined"
    assert h["overall"] == "degraded"
    assert len(store.read_events()) >= 1  # 事件日志照常可读（model 坏不阻 replay）


# ================================================================ G1: cache-hit isolation

def test_cache_hit_returns_copy_not_shared_reference(tmp_path):
    """CACHE3 red-team gap G1: an in-place mutation on an object returned by a cache hit
    (without calling save_observation) must NOT silently corrupt the cache, and must behave
    identically to the cache-off path (which always hands back a freshly deserialized,
    unrelated object)."""
    cached = RunStore(tmp_path / "run", cache_observations=True)
    exp = make_experiment(round_id=0)
    cached.save_experiment(exp)
    route_observation(cached, make_observation(exp, suspicion=0.0))  # -> TRUSTED

    got = cached.list_observations()[0]
    assert got.trust == TrustLevel.TRUSTED
    got.trust = TrustLevel.FAILED  # in-place mutation, no save_observation call

    again = cached.list_observations()[0]
    assert again is not got  # each call returns a fresh copy, not the same instance
    assert again.trust == TrustLevel.TRUSTED  # cache unaffected by the caller's mutation

    disk = RunStore(tmp_path / "run", create=False, cache_observations=False)
    disk_obs = disk.list_observations()[0]
    assert disk_obs.trust == TrustLevel.TRUSTED
    assert again.model_dump(mode="json") == disk_obs.model_dump(mode="json")

    # cache-off path for the same mutate-without-save pattern, for direct comparison
    off = RunStore(tmp_path / "run", create=False, cache_observations=False)
    g2 = off.list_observations()[0]
    g2.trust = TrustLevel.FAILED
    again_off = off.list_observations()[0]
    assert again_off.trust == TrustLevel.TRUSTED  # same outcome as the cache-on path above


def test_cache_hit_copy_isolated_across_repeated_calls(tmp_path):
    """Repeated list_observations() calls after a cache hit must each return independent
    copies — mutating one returned list must not leak into a later returned list."""
    cached = RunStore(tmp_path / "run", cache_observations=True)
    _seed_multi(cached, n_rounds=2, per_round=2)
    first = cached.list_observations()
    for o in first:
        o.trust = TrustLevel.FAILED
    second = cached.list_observations()
    assert all(o.trust != TrustLevel.FAILED for o in second)


# ================================================================ G2: transient OSError retry

def test_transient_oserror_retried_then_recovers(tmp_path, monkeypatch):
    """CACHE3 red-team gap G2: a transient per-file OSError (e.g. EIO/ESTALE on a flaky NFS
    mount) that clears within the retry budget must NOT quarantine the file or crash the run."""
    store = RunStore(tmp_path / "run")
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))
    target = next((tmp_path / "run" / "observations").glob("*.json"))

    monkeypatch.setattr(RunStore, "_OBS_READ_RETRY_BACKOFF_S", 0.0)  # no real sleep in tests
    orig_read_text = pathlib.Path.read_text
    fail_count = {"n": 0}

    def flaky_read_text(self, *a, **k):
        if self == target and fail_count["n"] < 2:  # fails twice, succeeds on 3rd attempt
            fail_count["n"] += 1
            raise OSError(5, "Input/output error")
        return orig_read_text(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", flaky_read_text)
    result = store.list_observations()

    assert len(result) == 1  # recovered, nothing lost
    assert fail_count["n"] == 2  # retried exactly the transient failures, then succeeded
    assert store.quarantined_files == {}  # transient error must not quarantine


def test_transient_oserror_exhausted_quarantines_single_file(tmp_path, monkeypatch):
    """When a per-file OSError persists beyond the retry budget, the file is quarantined
    (not a bare crash of the whole run) and other observations are still returned."""
    store = RunStore(tmp_path / "run")
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))
    route_observation(store, make_observation(exp, suspicion=0.0))
    files = sorted((tmp_path / "run" / "observations").glob("*.json"))
    bad_path = files[0]  # one of the two observation files; reads on it are made to always fail

    monkeypatch.setattr(RunStore, "_OBS_READ_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(RunStore, "_OBS_READ_RETRY_BACKOFF_S", 0.0)
    orig_read_text = pathlib.Path.read_text
    attempts = {"n": 0}

    def always_fails(self, *a, **k):
        if self == bad_path:
            attempts["n"] += 1
            raise OSError(5, "Input/output error")  # persistent, never recovers
        return orig_read_text(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", always_fails)
    result = store.list_observations()  # must not raise

    assert len(result) == 1  # the other observation is still returned
    assert attempts["n"] == 3  # exhausted the full retry budget before giving up
    assert str(bad_path) in store.quarantined_files
    assert "OSError" in store.quarantined_files[str(bad_path)]


def test_directory_level_oserror_still_propagates(tmp_path, monkeypatch):
    """Directory-level OSError (observations/ itself unreadable) is an environment-level
    fault and must NOT be swallowed by the per-file quarantine mechanism — it should still
    raise loudly, unlike a single bad/flaky file."""
    store = RunStore(tmp_path / "run")
    exp = make_experiment(0)
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.0))

    def boom_glob(self, pattern):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "glob", boom_glob)
    with pytest.raises(OSError):
        store.list_observations()
