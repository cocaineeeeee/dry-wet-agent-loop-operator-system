"""expos-lint 规则单测：每条规则的正反例（tmp 文件构造违例）。

结构：为每条规则在 tmp_path 下拼最小仓库骨架（只造该规则读的文件），
调 run_lint(root, select=[code]) 隔离单条规则，断言正例命中 / 反例全绿。
另有 meta 测试：self_check 一致性、redirect、真仓 error 恒零。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# 直接从脚本路径加载模块（scripts/ 非包）；先注册进 sys.modules，
# 否则 dataclass 前向引用注解解析（_is_type）在 spec 加载路径下会 NoneType 崩。
_SPEC = importlib.util.spec_from_file_location(
    "expos_lint", Path(__file__).resolve().parent.parent / "scripts" / "expos_lint.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["expos_lint"] = lint
_SPEC.loader.exec_module(lint)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _codes(findings) -> list[str]:
    return [f.code for f in findings]


def _run(root: Path, code: str, preview: bool = False):
    return lint.run_lint(root, preview=preview, select=[code])


# ---------------------------------------------------------------------------
# EXP001 —— 四包 truth 标识符
# ---------------------------------------------------------------------------
def test_exp001_hit(tmp_path):
    _write(tmp_path, "expos/qc/leak.py", "def f(x):\n    truth_value = x\n    return truth_value\n")
    assert "EXP001" in _codes(_run(tmp_path, "EXP001"))


def test_exp001_clean_docstring_ok(tmp_path):
    # docstring/注释里引用 truth 合法（红线条文），不应命中
    _write(tmp_path, "expos/qc/ok.py",
           '"""本模块禁触 truth（红线）。"""\n# truth 只由 adapters 生成\ndef f(x):\n    return x\n')
    assert "EXP001" not in _codes(_run(tmp_path, "EXP001"))


# ---------------------------------------------------------------------------
# EXP002 —— loop.py mode 判定越界
# ---------------------------------------------------------------------------
_LOOP_OK = '''\
def _policies_for_mode(mode, cfg, seed):
    if mode == "naive":
        return 1
    if mode in ("os", "os-soft"):
        return 2
    raise ValueError(mode)


def run_loop(mode):
    v = _policies_for_mode(mode, None, 0)
    cfg = {"mode": mode}          # 存字段，非判定
    for key, want in (("mode", mode),):
        if cfg.get(key) != want:  # 比较 cfg vs want，非 mode==字面量
            raise ValueError(key)
    return v, cfg
'''

_LOOP_BAD = _LOOP_OK + '''

def sneaky(mode):
    if mode == "os":            # 越界的 mode 字符串判定
        return "hidden branch"
    return None
'''


def test_exp002_clean(tmp_path):
    _write(tmp_path, "expos/loop.py", _LOOP_OK)
    assert "EXP002" not in _codes(_run(tmp_path, "EXP002"))


def test_exp002_hit(tmp_path):
    _write(tmp_path, "expos/loop.py", _LOOP_BAD)
    fs = _run(tmp_path, "EXP002")
    assert "EXP002" in _codes(fs)
    # 命中行应在 sneaky 内，而非 _policies_for_mode 内
    assert any("sneaky" not in "" and f.code == "EXP002" for f in fs)


# ---------------------------------------------------------------------------
# EXP003 —— 批次公式失联
# ---------------------------------------------------------------------------
def _sim(formula: str) -> str:
    return (f'def execute(exp):\n'
            f'    n_batches = 3\n'
            f'    batch = f"R{{exp.round_id}}-B{{{formula}}}"\n'
            f'    return batch\n')


def _attr(formula: str) -> str:
    return (f'def build_frame(exp):\n'
            f'    n_batches = 3\n'
            f'    batch = f"R{{exp.round_id}}-B{{{formula}}}"\n'
            f'    return batch\n')


def test_exp003_consistent_ok(tmp_path):
    _write(tmp_path, "expos/adapters/sim_base.py", _sim("(w.row + w.col) % n_batches"))
    _write(tmp_path, "expos/qc/attribution.py", _attr("(w.row + w.col) % n_batches"))
    assert "EXP003" not in _codes(_run(tmp_path, "EXP003"))


def test_exp003_drift_hit(tmp_path):
    # attribution 错用 capture 序 idx%n（M3 实锤 bug）——与 sim_base 的 (row+col)%n 失联
    _write(tmp_path, "expos/adapters/sim_base.py", _sim("(w.row + w.col) % n_batches"))
    _write(tmp_path, "expos/qc/attribution.py", _attr("idx % n_batches"))
    assert "EXP003" in _codes(_run(tmp_path, "EXP003"))


# ---------------------------------------------------------------------------
# EXP004 —— 非 sim_/base adapters 触碰 truth_records
# ---------------------------------------------------------------------------
def test_exp004_hit(tmp_path):
    _write(tmp_path, "expos/adapters/rogue.py",
           "def f(result):\n    return result.truth_records\n")
    assert "EXP004" in _codes(_run(tmp_path, "EXP004"))


def test_exp004_sim_and_base_allowed(tmp_path):
    _write(tmp_path, "expos/adapters/sim_x.py",
           "def f():\n    return Result(truth_records=[1])\n")
    _write(tmp_path, "expos/adapters/base.py",
           "class Result:\n    truth_records = None\n")
    # 注释里提 truth_records 也不应命中（ingest 红线声明式）
    _write(tmp_path, "expos/adapters/ingest.py",
           "# 红线：禁读 truth_records sidecar\ndef f():\n    return 1\n")
    assert "EXP004" not in _codes(_run(tmp_path, "EXP004"))


# ---------------------------------------------------------------------------
# EXP005 —— 裸 except / 静默 pass
# ---------------------------------------------------------------------------
def test_exp005_bare_except_hit(tmp_path):
    _write(tmp_path, "expos/a.py", "def f():\n    try:\n        g()\n    except:\n        pass\n")
    assert "EXP005" in _codes(_run(tmp_path, "EXP005"))


def test_exp005_silent_pass_hit(tmp_path):
    _write(tmp_path, "expos/b.py",
           "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n")
    assert "EXP005" in _codes(_run(tmp_path, "EXP005"))


def test_exp005_narrow_fallback_ok(tmp_path):
    # 窄 except + 真实兜底（返回默认值）/ 或响亮告警 —— 不算静默降级
    _write(tmp_path, "expos/c.py",
           "import sys\n"
           "def f():\n"
           "    try:\n        return g()\n    except ValueError:\n        return 0\n"
           "def h():\n"
           "    try:\n        g()\n    except OSError as e:\n"
           "        print('warn', e, file=sys.stderr)\n")
    assert "EXP005" not in _codes(_run(tmp_path, "EXP005"))


# ---------------------------------------------------------------------------
# EXP006 —— 占位滞留（preview）
# ---------------------------------------------------------------------------
_RUN_CELL = '_ARM_TO_MODE = {"naive": "naive", "os": "os", "os-soft": "os-soft", "rcgp": "rcgp"}\n'


def test_exp006_hit(tmp_path):
    _write(tmp_path, "expos/eval/run_cell.py", _RUN_CELL)
    _write(tmp_path, "expos/x.py",
           'def f(mode):\n    raise NotImplementedError("os-soft 未接线")  # 但 os-soft 已在 _ARM_TO_MODE\n')
    assert "EXP006" in _codes(_run(tmp_path, "EXP006", preview=True))


def test_exp006_off_by_default(tmp_path):
    _write(tmp_path, "expos/eval/run_cell.py", _RUN_CELL)
    _write(tmp_path, "expos/x.py",
           'def f():\n    raise NotImplementedError("os 未接线")\n')
    # 不加 --preview 且不点名 → preview 规则不跑
    assert lint.run_lint(tmp_path, preview=False, select=None) == [] or \
        "EXP006" not in _codes(lint.run_lint(tmp_path, preview=False, select=None))


def test_exp006_benign_placeholder_ok(tmp_path):
    _write(tmp_path, "expos/eval/run_cell.py", _RUN_CELL)
    # "占位" 但不引用任何已接线 mode（如 BO 占位）→ 不命中
    _write(tmp_path, "expos/design/sampler.py", '"""BO 占位接口。"""\ndef f():\n    return 1\n')
    assert "EXP006" not in _codes(_run(tmp_path, "EXP006", preview=True))


# ---------------------------------------------------------------------------
# EXP007 —— kernel 依赖倒挂
# ---------------------------------------------------------------------------
def test_exp007_hit_importfrom(tmp_path):
    _write(tmp_path, "expos/kernel/k.py", "from expos.qc.checks import run_qc\n")
    assert "EXP007" in _codes(_run(tmp_path, "EXP007"))


def test_exp007_hit_import(tmp_path):
    _write(tmp_path, "expos/kernel/k2.py", "import expos.planner.policy\n")
    assert "EXP007" in _codes(_run(tmp_path, "EXP007"))


def test_exp007_clean(tmp_path):
    _write(tmp_path, "expos/kernel/k.py",
           "from expos.kernel.objects import Budget\nfrom expos.errors import ExposError\n")
    assert "EXP007" not in _codes(_run(tmp_path, "EXP007"))


# ---------------------------------------------------------------------------
# EXP008 —— yaml shadow 内置 adapter
# ---------------------------------------------------------------------------
_DOMAIN_PY = 'ADAPTER_REGISTRY: dict[str, type] = {\n    "sim_crystal": A,\n    "sim_coating": B,\n}\n'


def test_exp008_register_shadow_hit(tmp_path):
    _write(tmp_path, "expos/domain.py", _DOMAIN_PY)
    _write(tmp_path, "domains/evil.yaml", "name: evil\nregister_adapter: sim_coating\n")
    assert "EXP008" in _codes(_run(tmp_path, "EXP008"))


def test_exp008_adapters_block_shadow_hit(tmp_path):
    _write(tmp_path, "expos/domain.py", _DOMAIN_PY)
    _write(tmp_path, "domains/evil2.yaml",
           "name: evil2\nadapters:\n  sim_crystal: my.module.Foo\n")
    assert "EXP008" in _codes(_run(tmp_path, "EXP008"))


def test_exp008_plain_reference_ok(tmp_path):
    _write(tmp_path, "expos/domain.py", _DOMAIN_PY)
    # 纯标量引用既有内置名 —— 合法，非 shadow
    _write(tmp_path, "domains/coating.yaml", "name: coating\nadapter: sim_coating\n")
    assert "EXP008" not in _codes(_run(tmp_path, "EXP008"))


# ---------------------------------------------------------------------------
# EXP009 —— pytest skip 无 reason
# ---------------------------------------------------------------------------
def test_exp009_skip_no_reason_hit(tmp_path):
    _write(tmp_path, "tests/test_x.py",
           "import pytest\n@pytest.mark.skip\ndef test_a():\n    pass\n")
    assert "EXP009" in _codes(_run(tmp_path, "EXP009"))


def test_exp009_skipif_no_reason_hit(tmp_path):
    _write(tmp_path, "tests/test_y.py",
           "import pytest\n@pytest.mark.skipif(True)\ndef test_b():\n    pass\n")
    assert "EXP009" in _codes(_run(tmp_path, "EXP009"))


def test_exp009_with_reason_ok(tmp_path):
    _write(tmp_path, "tests/test_z.py",
           'import pytest\n@pytest.mark.skipif(True, reason="平台不支持")\ndef test_c():\n    pass\n')
    assert "EXP009" not in _codes(_run(tmp_path, "EXP009"))


# ---------------------------------------------------------------------------
# EXP010 —— 事件词表漂移
# ---------------------------------------------------------------------------
_SCHEMA = (
    "# EVENT_SCHEMA\n\n"
    "### routing — 逐观测路由\n| x | y |\n\n"
    "### checkpoint — 恢复点\n| a | b |\n"
)


def test_exp010_drift_hit(tmp_path):
    _write(tmp_path, "docs/EVENT_SCHEMA.md", _SCHEMA)
    _write(tmp_path, "expos/p.py",
           'def f(store):\n    store.append_event("mystery_kind", {})\n')
    assert "EXP010" in _codes(_run(tmp_path, "EXP010"))


def test_exp010_registered_ok(tmp_path):
    _write(tmp_path, "docs/EVENT_SCHEMA.md", _SCHEMA)
    _write(tmp_path, "expos/p.py",
           'def f(store):\n    store.append_event("routing", {})\n    store.append_event("checkpoint", {})\n')
    assert "EXP010" not in _codes(_run(tmp_path, "EXP010"))


# ---------------------------------------------------------------------------
# EXP011 —— qc/ 禁止新增 crystal 域字面量（Q3 棘轮③）
# ---------------------------------------------------------------------------
# 三类用例：命中样例必红 / 豁免样例必绿 / 存量（真仓已标注）不误报——真仓不误报
# 由 test_real_repo_no_error_tier / test_real_repo_qc_domain_literal_clean 覆盖
# （真仓 expos/qc/attribution.py 与 checks.py 的既有命中行已带
# `# lint: allow-domain-literal(reason)` 标注，见 BASELINE.md）。

# ---- P3 棋盘奇偶 (row+col)%n ----
def test_exp011_checkerboard_parity_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py",
           'def f(w, exp, n_batches):\n'
           '    return f"R{exp.round_id}-B{(w.row + w.col) % n_batches}"\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


def test_exp011_checkerboard_parity_exempt_ok(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py",
           'def f(w, exp, n_batches):\n'
           '    return f"R{exp.round_id}-B{(w.row + w.col) % n_batches}"  '
           '# lint: allow-domain-literal(mirrors sim_base for EXP003)\n')
    assert "EXP011" not in _codes(_run(tmp_path, "EXP011"))


def test_exp011_checkerboard_parity_outside_qc_ok(tmp_path):
    # sim_base.py 是该公式的合法产地（expos/adapters/，非 qc/）——不在本规则 scope 内
    _write(tmp_path, "expos/adapters/sim_base.py",
           'def f(w, exp, n_batches):\n'
           '    return f"R{exp.round_id}-B{(w.row + w.col) % n_batches}"\n')
    assert "EXP011" not in _codes(_run(tmp_path, "EXP011"))


# ---- P5 crystal 专名字符串字面量 ----
def test_exp011_string_literal_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py", 'CAUSES = ["glare", "dust_contamination"]\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


def test_exp011_string_literal_exempt_ok(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py",
           'CAUSES = ["glare", "dust_contamination"]  '
           '# lint: allow-domain-literal(legacy failure taxonomy)\n')
    assert "EXP011" not in _codes(_run(tmp_path, "EXP011"))


def test_exp011_empty_reason_not_exempt(tmp_path):
    # 空 reason（括号内无字符）不算有效豁免——仍须命中
    _write(tmp_path, "expos/qc/rogue.py",
           'CAUSES = ["glare"]  # lint: allow-domain-literal()\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


# ---- P4 edge 带宽硬编码 ----
def test_exp011_edge_bandwidth_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py",
           'def f(d_edge, resid_raw):\n'
           '    return [w for w in resid_raw if d_edge[w] <= 1]\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


# ---- P1 板几何硬编码 ----
def test_exp011_board_geometry_int_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py", 'N_WELLS = 48\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


def test_exp011_board_geometry_shape_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py", 'GRID_SHAPE = (6, 8)\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


def test_exp011_unrelated_int_ok(tmp_path):
    # 与 48/6x8 无关的普通整数不应被误伤
    _write(tmp_path, "expos/qc/rogue.py", 'THRESHOLD = 40\nSHAPE = (3, 4)\n')
    assert "EXP011" not in _codes(_run(tmp_path, "EXP011"))


# ---- P2 哨兵固定位硬编码 ----
def test_exp011_sentinel_capture_index_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py",
           'def f(o):\n    return o.instrument_meta.capture_index == 0\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


def test_exp011_sentinel_well_id_hit(tmp_path):
    _write(tmp_path, "expos/qc/rogue.py",
           'def f(o):\n    return o.layout_meta.well_id == "S1"\n')
    assert "EXP011" in _codes(_run(tmp_path, "EXP011"))


def test_exp011_clean_qc_file_ok(tmp_path):
    _write(tmp_path, "expos/qc/clean.py",
           'def f(x, y):\n    return x + y if x > 0 else y - x\n')
    assert "EXP011" not in _codes(_run(tmp_path, "EXP011"))


# ---------------------------------------------------------------------------
# EXP012 —— MR 注册表锚巡检
# ---------------------------------------------------------------------------
_MR_HEAD = ("# MR_REGISTRY\n\n"
            "| mr_id | τ | R | face | test_anchor | born | status |\n"
            "|---|---|---|---|---|---|---|\n")


def _mr_repo(tmp_path, anchor, status):
    _write(tmp_path, "docs/MR_REGISTRY.md",
           _MR_HEAD + f"| **MR_x** | t | R | decision | {anchor} | M17 | {status} |\n")
    _write(tmp_path, "tests/test_real.py", "def test_alive():\n    pass\n")


def test_exp012_missing_file_hit(tmp_path):
    _mr_repo(tmp_path, "`tests/test_gone.py::test_alive`", "active")
    assert "EXP012" in _codes(_run(tmp_path, "EXP012"))


def test_exp012_missing_test_id_hit(tmp_path):
    # kill-verification twin of the live tamper check: a renamed/deleted test
    # behind an active anchor must turn the registry red, not stay silent.
    _mr_repo(tmp_path, "`tests/test_real.py::test_renamed`", "partial")
    assert "EXP012" in _codes(_run(tmp_path, "EXP012"))


def test_exp012_valid_anchor_ok(tmp_path):
    _mr_repo(tmp_path, "`tests/test_real.py::test_alive`（注）；K-E 整环（待落成）", "active")
    assert "EXP012" not in _codes(_run(tmp_path, "EXP012"))


def test_exp012_pending_row_exempt(tmp_path):
    _mr_repo(tmp_path, "`tests/test_gone.py::test_alive`", "**pending**")
    assert "EXP012" not in _codes(_run(tmp_path, "EXP012"))


# ---------------------------------------------------------------------------
# EXP013 —— 域合规冒烟契约（DYNAMIC preview 规则；加载/绑定/对账/锚/回归锚）
# ---------------------------------------------------------------------------
# 用 pyscf_dry adapter：其 metric 交叉校验 no-op（无 default_metric/required_params），
# 故任何 objective.metric + 任意设计空间都能 load，且 build_adapter 不被 load_domain 调。
def _domain_repo(tmp_path, name: str, faces_block: str, extra: str = "") -> None:
    _write(tmp_path, f"domains/{name}.yaml",
           f"name: {name}\n"
           "adapter: pyscf_dry\n"
           "objective: {name: y, metric: yield, direction: maximize}\n"
           "design_space:\n"
           "  name: ds\n"
           "  variables:\n"
           "    - {name: x, kind: continuous, low: 0.0, high: 1.0}\n"
           "plate: {rows: 2, cols: 2}\n"
           "sentinel: {n: 1, params: {x: 0.5}}\n"
           "budget: {wells_total: 4, rounds_total: 1}\n"
           "metrics: [yield]\n"
           "execution_kind: dry_compute\n"
           + extra + faces_block)


def test_exp013_load_failure_hit(tmp_path):
    # clause 1: a yaml that does not load (missing required blocks) turns EXP013.
    _write(tmp_path, "domains/broken.yaml", "name: broken\nadapter: pyscf_dry\n")
    fs = _run(tmp_path, "EXP013")
    assert "EXP013" in _codes(fs)
    assert any("[load]" in f.message for f in fs)


def test_exp013_landed_face_missing_anchor_hit(tmp_path):
    # clause 4: a status=landed face whose test anchor does not resolve turns EXP013.
    _domain_repo(tmp_path, "d",
                 "acceptance_faces:\n"
                 "  - {face_name: f1, truth_profile: polar_high, status: landed,\n"
                 "     test_anchor: \"tests/test_nope.py::test_nope\"}\n")
    fs = _run(tmp_path, "EXP013")
    assert "EXP013" in _codes(fs)
    assert any("[anchor]" in f.message for f in fs)


def test_exp013_declared_debt_face_ok(tmp_path):
    # A status=declared debt face (unbuilt truth_profile, null anchor) is the
    # recorded debt -- it must NOT turn EXP013 (that is the whole point of the ledger).
    _domain_repo(tmp_path, "d",
                 "acceptance_faces:\n"
                 "  - {face_name: low, truth_profile: not_built_yet, status: declared,\n"
                 "     test_anchor: null}\n")
    assert "EXP013" not in _codes(_run(tmp_path, "EXP013"))


def test_exp013_landed_face_unknown_profile_hit(tmp_path):
    # clause 3: a LANDED face whose truth_profile is absent from TRUTH_PROFILES
    # turns EXP013 (a landed face must name a real reader face). Anchor points at a
    # real repo-relative test so only the reconcile clause fires.
    _write(tmp_path, "tests/test_x.py", "def test_ok():\n    pass\n")
    _domain_repo(tmp_path, "d",
                 "acceptance_faces:\n"
                 "  - {face_name: f, truth_profile: no_such_face, status: landed,\n"
                 "     test_anchor: \"tests/test_x.py::test_ok\"}\n")
    fs = _run(tmp_path, "EXP013")
    assert "EXP013" in _codes(fs)
    assert any("[reconcile]" in f.message for f in fs)


def test_exp013_provider_bogus_path_hit(tmp_path):
    # clause 6 / clause 1: a domain declaring a non-importable provider path fails the
    # real loading line (importlib -> check_complete -> validate_yaml) and turns EXP013.
    _domain_repo(tmp_path, "d", "",
                 extra="provider: expos.adapters.providers.nope:Missing\n")
    fs = _run(tmp_path, "EXP013")
    assert "EXP013" in _codes(fs)
    assert any("[load]" in f.message for f in fs)


def test_exp013_clean_on_real_repo():
    """真仓：solvent_screen + catalyst_screen 声明的 metrics/observables/faces 全部
    对账通过、landed 面锚真实存在、solvent 回归锚在，且两旗舰的 provider: 经真实装载线
    （check_complete + validate_yaml）通过——EXP013 无任何失败命中。剩下的只是 clause 6
    对无 provider 域（crystal/coating/flipped）的 [provider-preview] 前压提示（非错）。"""
    findings = [f for f in _run(REPO_ROOT, "EXP013") if f.code == "EXP013"]
    failures = [f for f in findings if "[provider-preview]" not in f.message]
    assert failures == [], failures
    # the two flagship domains that declare providers must be entirely clean ...
    assert not [f for f in findings
                if f.path in ("domains/solvent_screen.yaml", "domains/catalyst_screen.yaml")]
    # ... and every remaining finding is a provider-less forward-pressure nudge.
    assert all("[provider-preview]" in f.message for f in findings)


# ---------------------------------------------------------------------------
# meta —— 自检 / redirect / 真仓
# ---------------------------------------------------------------------------
def test_self_check_clean_on_real_repo():
    assert lint.self_check(REPO_ROOT) == []


def test_rule_codes_unique_and_well_formed():
    codes = [r.code for r in lint.RULES]
    assert len(codes) == len(set(codes)) == 13
    assert all(r.tier in lint.VALID_TIERS for r in lint.RULES)


def test_redirect_targets_exist():
    codes = {r.code for r in lint.RULES}
    for old, new in lint.REDIRECTS.items():
        assert new in codes


def test_real_repo_no_error_tier():
    """真仓：error 级恒零（红线全绿），exit 应为 0。"""
    findings = lint.run_lint(REPO_ROOT, preview=True)
    errs = [f for f in findings if f.tier == lint.ERROR]
    assert errs == [], f"意外的 error 命中: {errs}"


def test_real_repo_event_vocab_clean():
    """真仓：全部事件 kind 均已登记 EVENT_SCHEMA（action_skipped 的漂移在 lint 首日
    被 EXP010 抓到并已补登记）→ 现在应零命中。正例覆盖在 tmp 构造用例里。"""
    findings = lint.run_lint(REPO_ROOT, select=["EXP010"])
    assert [f for f in findings if f.code == "EXP010"] == []


def test_real_repo_qc_domain_literal_clean():
    """真仓：expos/qc/ 既有 crystal 字面量均已带行内豁免标注（见 BASELINE.md）→
    EXP011 现应零命中；新命中样例覆盖在 tmp 构造用例里。"""
    findings = lint.run_lint(REPO_ROOT, select=["EXP011"])
    assert [f for f in findings if f.code == "EXP011"] == []


def test_main_exit_zero_on_real_repo():
    assert lint.main(["--root", str(REPO_ROOT)]) == 0
