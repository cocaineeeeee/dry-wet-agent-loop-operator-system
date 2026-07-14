"""环路级机制活性断言（ARCH_V2 §2 机制活性注册表的最小先行版）。

背景（docs/STRESS_TEST_R2.md §2.1，红队 F-1/F-2/F-3）：M7/M9 三个决策机制在**单元层**
已有守护（变异 A/B/C 被杀），但**生产接线层**无守护——三个环路级变异全绿存活：

- **E**：``TrustAwarePlanner.plan_round`` 返回 ``risk_map=None``（断开风险图生产接线）→
  63 测全绿。既有 ``test_stage_and_risk_map_active`` 不断言 risk_map 一个字；新加单元
  测试直调 ``_plate_risk_map`` 绕过接线。
- **D**：``response_gp+ucb+risk_discount`` 生成分支首行 ``raise`` → 环路测试全绿。单轮
  高嫌疑 streak=1<2，**没有任何测试把闭环真正驱动进 failure_aware 阶段**。
- **F-3**：``SoftTrustAggregation._weight`` 改恒 1（降权失效）→ test_loop_soft 六测全绿
  （只断计数账目，对权重值不敏感）。

本模块用**真实 run_loop 主路径**的只读活性观测面（loop.py 发射的 ``risk_map_applied`` /
``aggregation_alpha`` 事件，均已登记 EVENT_SCHEMA testing 档，纯派生不改行为）逐一击杀
这三个变异。红线遵守：不读真值 sidecar、零 mode 分支（观测面对所有 mode 统一发射）。

三个场景都是**闭环**（run_loop 全程），非直调；风险图证据取自 LayoutPlanner 的喂入点。
运行较重（多轮 os / os-soft），module 级共享。
"""

import statistics
from pathlib import Path

import pytest
import yaml

from expos.kernel.objects import Routing, TrustLevel
from expos.kernel.store import RunStore
from expos.loop import run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"

RISK_DISCOUNT_GEN = "response_gp+ucb+risk_discount"


def _crystal_variant(tmp_dir, *, edge_rounds=(), glare_prob=None, trust=None):
    """crystal 域变体：把 edge_evaporation 换成指定轮的强边缘事件；可选调 glare 概率与
    信任阈值。其余（漂移/眩光常驻、板尺寸、预算）不动——闭环真实，只改场景强度。"""
    cfg = yaml.safe_load(CRYSTAL.read_text(encoding="utf-8"))
    scen = [s for s in cfg["simulator"]["artifact_scenario"]
            if s.get("injector") != "edge_evaporation"]
    for r in edge_rounds:
        scen.append({"round": r, "injector": "edge_evaporation",
                     "params": {"strength": 0.5, "decay_wells": 1.0}})
    if glare_prob is not None:
        for item in scen:
            if item.get("injector") == "glare":
                item["params"]["prob"] = glare_prob
    cfg["simulator"]["artifact_scenario"] = scen
    if trust is not None:
        cfg["trust"] = dict(trust)
    p = Path(tmp_dir) / "crystal_variant.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


# ================================================================ 变异 E：风险图接线

@pytest.fixture(scope="module")
def risk_map_run(tmp_path_factory):
    """os 4 轮，强边缘只在**第 2 轮**（偶）→ 第 3 轮（奇，同 round_band r2-r3）规划时
    失败模型层级回退接住第 2 轮边缘失败史 → risk_map 非常数。单轮高嫌疑 streak=1<2，
    **不进 failure_aware**（默认规则）——故本 run 绝不触碰 risk_discount 分支，变异 D
    动不了它（E 与 D 的击杀互不串味）。"""
    dom = _crystal_variant(tmp_path_factory.mktemp("dom_e"), edge_rounds=(2,))
    out = tmp_path_factory.mktemp("runs") / "mech_e"
    summary = run_loop(dom, mode="os", rounds=4, seed=7, out_dir=out)
    return out, summary


def test_E_risk_map_nonconstant_consumed_by_layout(risk_map_run):
    """击杀变异 E（plan_round 返回 risk_map=None，断开生产接线）。

    经 run_loop 主路径断言：至少一轮的 risk_map **非 None 且非常数**，且它正是
    ``build_experiment`` 交给 ``LayoutPlanner.assign`` 消费的那张图——``risk_map_applied``
    事件在 build_experiment 喂入点从 ``plan.risk_map`` 派生（build_experiment 原样转手、
    无变换、无 mode 分支），n_wells 与该轮实际布局的板孔全集逐一对齐，证明是被消费的图
    而非孤立计算。变异 E 下每轮 risk_map=None → is_none 恒 True → 本测试转红。"""
    out, _ = risk_map_run
    store = RunStore(out, create=False)
    applied = store.read_events("risk_map_applied")
    assert applied, "risk_map_applied 观测面缺席——生产接线未发射风险图证据"

    # os 臂：TrustAwarePlanner 每轮都产字典风险图，绝不 None（变异 E 会把它打成 None）
    assert all(not e["payload"]["is_none"] for e in applied), \
        "存在 is_none 风险图——生产接线被断开（变异 E）"

    # 至少一轮非常数（n_distinct>=2）——恒常数空转（变异 B 的环路对偶）在此也显形
    nonconst = [e["payload"] for e in applied if e["payload"]["n_distinct"] >= 2]
    assert nonconst, (
        "所有轮风险图都是常数——风险避让机制在环路层空转："
        f"{[(e['payload']['round_id'], e['payload']['n_distinct']) for e in applied]}"
    )

    # 消费证据（O3DV C2 修订——旧断言 n_wells==板容量是从事件自身铸键的恒真式，
    # 红队实测转手变异 C2 下全绿，不构成消费佐证）：事件取证源已改为**消费侧**
    # exp.provenance.risk_map_summary（build_experiment 从实收参数计算）；此处断言
    # 事件摘要与落盘 provenance 逐字段一致——事件若从产出侧派发而 layout 实收 None
    # （C2 转手构造），provenance 侧为 is_none=True，两者失配即红。
    exps = {e.exp_id: e for e in store.list_experiments()}
    for pl in nonconst:
        exp = exps[pl["exp_id"]]
        prov = exp.provenance.risk_map_summary
        assert prov is not None, "provenance 缺消费侧取证——build_experiment 未计算实收摘要"
        for k in ("is_none", "n_wells", "n_distinct", "min", "max"):
            assert pl[k] == prov[k], (
                f"事件摘要与消费侧 provenance 失配（{k}: 事件={pl[k]} 实收={prov[k]}）"
                "——存在转手断线（C2 类表演性构造）"
            )
        assert not prov["is_none"], "非常数轮的实收图不可能是 None"
        assert exp.layout.wells, "该轮无布局——无法证明风险图被消费"
        assert pl["min"] != pl["max"], "非常数轮的 min==max 自相矛盾"


# ================================================================ grade 三态差分（建议 1+收紧 1）
#
# grade 是**派生事实**（loop 发射端纯函数判档，发射/裁决解耦）；红/黄的 CI 判档收在此消费端。
# 差分：干净轮 os run → risk_map 至少一轮 active、绝无 absent；aggregation grade=warning 非
# absent（os 无软并入，恒等合法）。变异 E 同形的消融臂 → risk_map grade=absent 非 warning。


def test_grade_risk_map_clean_os_active_never_absent(risk_map_run):
    """干净 os run 的 risk_map_applied.grade：至少一轮 active（非常数图被消费），
    且绝无 absent（生产接线未断）——与变异 E 的 absent 反差。"""
    out, _ = risk_map_run
    store = RunStore(out, create=False)
    grades = [e["payload"]["grade"] for e in store.read_events("risk_map_applied")]
    assert grades, "risk_map_applied 缺席"
    assert "absent" not in grades, f"干净 os 出现 absent（接线断开征兆）：{grades}"
    assert "active" in grades, f"干净 os 无一轮 active（风险图空转）：{grades}"


def test_grade_aggregation_clean_os_is_warning_not_absent(risk_map_run):
    """差分（交接 §四）：干净轮 os run → aggregation_alpha grade=warning **非 absent**
    （os=ReplicateVariance，alpha 非 None 但无软并入降权条目 → 恒等合法黄牌）。"""
    out, _ = risk_map_run
    store = RunStore(out, create=False)
    grades = [e["payload"]["grade"] for e in store.read_events("aggregation_alpha")]
    assert grades, "aggregation_alpha 缺席"
    assert all(g == "warning" for g in grades), (
        f"干净 os 的聚合 grade 应恒 warning（无软降权），实得：{grades}"
    )


def test_grade_aggregation_soft_active(soft_run):
    """os-soft 真实降权 → 末轮 aggregation_alpha grade=active（软副本 alpha 中位显著超
    TRUSTED 中位）。变异 F-3(_weight≡1) 会使比值坍到 ≈1 → grade 退为 warning（假活性显形，
    与 test_F3_* 的比值断言同源）。"""
    out, _ = soft_run
    store = RunStore(out, create=False)
    alpha_events = store.read_events("aggregation_alpha")
    assert alpha_events, "aggregation_alpha 缺席"
    assert alpha_events[-1]["payload"]["grade"] == "active", (
        f"os-soft 末轮聚合 grade 未 active：{[e['payload']['grade'] for e in alpha_events]}"
    )


def test_grade_ablation_riskmap_absent(minus_riskmap_run):
    """变异 E 同形消融臂 os-minus-riskmap → risk_map_applied.grade **恒 absent 非 warning**
    （生产接线被消融，is_none 恒 True）。"""
    store = RunStore(minus_riskmap_run, create=False)
    grades = [e["payload"]["grade"] for e in store.read_events("risk_map_applied")]
    assert grades and all(g == "absent" for g in grades), (
        f"os-minus-riskmap 的 risk_map grade 应恒 absent，实得：{grades}"
    )


# ================================================================ 失活预算熔断（建议 2；FB3 重构）
#
# 语义（红队 FB3）：sweep 级事后门 = 去抖长度 k 的**连续-k 游程**判据。budget_breached 纯函数
# 用合成 grade 序列（已知答案）测；k* 从 F3 重放数据反解（derive_k）；合法侧族误报=0 用真实
# F3 重放序列（SET A 纯净集，warning<=>n_distinct<=1）测；死机制侧用真实消融臂正向击杀。

import random  # noqa: E402
from collections import deque  # noqa: E402

from expos.eval.activity_budget import (  # noqa: E402
    DEFAULT_K,
    ActivityBudget,
    budget_breached,
    derive_k,
    expected_active,
    grade_stream,
    scan_run,
)

# F3 重放数据（runs/r1_resweep os 臂逐轮 warning 指示，1=warning/n_distinct<=1=失活，0=active）。
# SET A "纯净集"（红队 FB3 §四）= should-activate 的高信号空间边缘蒸发档 ee0.20+ee0.35，N=40。
# 用于合法侧族误报断言：k* 下 40 合法格零红（原 (3,5) 判据在此全红）。
_F3_EE20_LEGIT = [
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 1, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 1, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 1, 1, 0, 1, 0], [1, 0, 1, 1, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 1],
    [1, 1, 1, 0, 1, 1, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
]
_F3_EE35_LEGIT = [
    [1, 0, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 0, 1, 1, 1, 0, 1, 0],
    [1, 0, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
    [1, 0, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0], [1, 0, 1, 1, 1, 0, 1, 0],
    [1, 1, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
    [1, 0, 1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 1, 0, 1, 0],
]
_F3_SETA_LEGIT = _F3_EE20_LEGIT + _F3_EE35_LEGIT  # N=40


def _grades(seq):
    """1→warning（失活）、0→active。"""
    return ["warning" if x else "active" for x in seq]


# ---- 判据形态：连续-k 游程 ------------------------------------------------------

def test_budget_all_active_no_breach():
    assert budget_breached(["active"] * 8) is None


def test_budget_k_consecutive_breaches_at_k_th_round():
    """默认去抖 k（=7）：连续 k 轮失活在第 k 轮（idx k−1）走满 → 熔断。"""
    b = budget_breached(["warning"] * DEFAULT_K)
    assert b is not None
    assert b["at_round_index"] == DEFAULT_K - 1
    assert b["k"] == DEFAULT_K
    assert b["inactive_round_indices"] == list(range(DEFAULT_K))


def test_budget_below_k_no_breach():
    """连续 k−1 轮失活（合法静默上限）→ 不熔断（容忍 ≤k−1）。"""
    assert budget_breached(["warning"] * (DEFAULT_K - 1)) is None


def test_budget_active_resets_run_no_breach():
    """任一合法 active 轮清零游程：即便总失活轮很多，只要无 k 连续段就不熔断。"""
    grades = (["warning"] * (DEFAULT_K - 1) + ["active"]) * 3
    assert budget_breached(grades) is None


def test_budget_absent_counts_as_inactive():
    b = budget_breached(["absent"] * 3, ActivityBudget(k=3))
    assert b is not None and b["at_round_index"] == 2


def test_budget_custom_k():
    assert budget_breached(["warning", "warning"], ActivityBudget(k=2)) is not None
    assert budget_breached(["warning", "active", "warning"], ActivityBudget(k=2)) is None


def test_budget_consecutive_equiv_to_kinw_period_eq_intensity():
    """红队等价性证明收编：period=intensity 时 k-in-w 滑窗 ≡ 连续-k（本模块判据）。
    10⁵ 随机序列逐位比对，零失配——故"换形"是判据等价的换形、非语义漂移。"""
    def kinw(s, k, w):
        dq = deque()
        for i, x in enumerate(s):
            if x:
                dq.append(i)
            while dq and dq[0] <= i - w:
                dq.popleft()
            if len(dq) >= k:
                return True
        return False
    rng = random.Random(1)
    mism = 0
    for _ in range(20000):
        s = [1 if rng.random() < 0.6 else 0 for _ in range(8)]
        for k in range(2, 7):
            breach = budget_breached(_grades(s), ActivityBudget(k=k)) is not None
            if breach != kinw(s, k, k):
                mism += 1
    assert mism == 0, f"连续-k 与 k-in-w(period=intensity) 失配 {mism} 次"


# ---- 参数反解：k 不拍定，抽验对 dimfb3 可行域表一致 ----------------------------

def test_derive_k_matches_dimfb3_feasibility_table():
    """derive_k 反解对 /tmp/claude-1128/dimfb3 可行域表抽验 3 点一致（验收项）：
    R=8,N=40,α=0.05 下 a=0.28→k*=7（保守）、a=0.35→k*=8、a=0.45→无可行 k（None）。"""
    assert derive_k(8, 40, 0.05, 0.28)[0] == 7
    assert derive_k(8, 40, 0.05, 0.35)[0] == 8
    assert derive_k(8, 40, 0.05, 0.45)[0] is None


def test_default_k_is_derived_conservative_seven():
    """现役默认去抖 k 是 derive_k 反解值（保守 7）而非拍定（原 (3,5) 借自 VS Code 已否决）。"""
    assert DEFAULT_K == 7


# ---- scope：(场景族×机制) 准入 --------------------------------------------------

def test_expected_active_scope_by_family_and_mechanism():
    """红队 FB3 scope 修正：risk_map 是空间机制。
    - 高信号空间档 edge_evaporation.0.2 → risk_map 入准（should-activate）；
    - batch_shift（非空间）静默是正确行为 → 不入准；
    - 低档 edge_evaporation.0.05（信号不足）→ 不入准；
    - scenario 未知 → 空集（保守不判红，杜绝一刀切误红）。"""
    assert expected_active("os", "S2.edge_evaporation.0.2") == {"risk_map"}
    assert expected_active("os", "S2.batch_shift.-0.18") == set()
    assert expected_active("os", "S2.edge_evaporation.0.05") == set()
    assert expected_active("os", None) == set()


def test_soft_trust_not_admitted_uncalibrated():
    """soft_trust_reweight 的 p_w 未标定（F3 是 risk_map 重放）→ 任何场景都不入 should-activate
    集（须单独标定 a_max 后套同式）。"""
    assert "soft_trust_reweight" not in expected_active("os-soft", "S2.edge_evaporation.0.35")


# ---- 合法侧族误报（红队点名缺失的测试）----------------------------------------

def test_budget_family_fpr_zero_on_f3_legit():
    """红队点名补测：用 F3 重放的 SET A 纯净集（40 合法 should-activate 格）断言 **k* 下
    族误报=0**（零红）——保守 k=7 与经验 k=6 皆零红。原 (3,5) k-in-w 在此 40/40 全红
    （p_w 高、active 从不连续），本判据换形+参数反解后守门恢复有效。"""
    for k in (6, DEFAULT_K):  # 经验 6 与保守 7
        reds = sum(
            1 for seq in _F3_SETA_LEGIT
            if budget_breached(_grades(seq), ActivityBudget(k=k)) is not None
        )
        assert reds == 0, f"k={k} 下 SET A 合法格误红 {reds}/{len(_F3_SETA_LEGIT)}（应=0）"

    # 反证：原 (3,5) k-in-w 语义（period=5,intensity=3）在同一合法集 40/40 全红
    def kinw35(s):
        dq = deque()
        for i, x in enumerate(s):
            if x:
                dq.append(i)
            while dq and dq[0] <= i - 5:
                dq.popleft()
            if len(dq) >= 3:
                return True
        return False
    old_reds = sum(1 for seq in _F3_SETA_LEGIT if kinw35(seq))
    assert old_reds == len(_F3_SETA_LEGIT), (
        f"原 (3,5) 应对合法集全红以佐证换形动机，实得 {old_reds}/{len(_F3_SETA_LEGIT)}"
    )


def test_grade_stream_orders_by_round_and_filters_kind():
    events = [
        {"kind": "risk_map_applied", "payload": {"round_id": 2, "grade": "active"}},
        {"kind": "risk_map_applied", "payload": {"round_id": 0, "grade": "warning"}},
        {"kind": "aggregation_alpha", "payload": {"round_id": 1, "grade": "active"}},
    ]
    assert grade_stream(events, "risk_map_applied") == ["warning", "active"]


# ---- 死机制侧：真实消融臂正向击杀（保留）--------------------------------------

def test_budget_scan_run_reds_ablated_mechanism(minus_riskmap_run):
    """真实消融臂（risk_map 全 absent，3 轮）在 expect_active={risk_map} 下被失活预算判红。
    去抖 k=3（该 run 仅 3 轮；死机制=全 absent 游程走满即红）——死机制侧守门保留。"""
    viol = scan_run(minus_riskmap_run, expect_active={"risk_map"},
                    budget=ActivityBudget(k=3))
    assert viol and viol[0]["mechanism"] == "risk_map", (
        "os-minus-riskmap 的 risk_map 全 absent 未被失活预算判红"
    )


# ================================================================ 变异 D：failure_aware 闭环

@pytest.fixture(scope="module")
def failure_aware_run(tmp_path_factory):
    """os 5 轮，强边缘在**连续第 2、3 两轮** → 第 3 轮规划 streak=1、第 4 轮规划 streak=2
    → 默认规则 high_suspect_streak(2) 触发 gp→failure_aware，第 4 轮真正走
    ``response_gp+ucb+risk_discount`` 生成器。这是把闭环驱动进 FSM 第三状态的真实路径
    （非直调、非注入规则——纯默认规则 + 场景驱动）。"""
    dom = _crystal_variant(tmp_path_factory.mktemp("dom_d"), edge_rounds=(2, 3))
    out = tmp_path_factory.mktemp("runs") / "mech_d"
    summary = run_loop(dom, mode="os", rounds=5, seed=7, out_dir=out)
    return out, summary


def test_D_failure_aware_reached_with_risk_discount_generator(failure_aware_run):
    """击杀变异 D（risk_discount 生成分支首行 raise，因 streak 从未达 2 而无测试驱动）。

    断言 stage_changed 事件真的到达 failure_aware，且**该轮 exp 的 provenance.generator
    == 'response_gp+ucb+risk_discount'**——即闭环确实执行了风险贴现生成分支。变异 D 下
    该分支一进就 raise，run_loop 在此轮崩溃 → fixture 直接报错 → 本测试转红。"""
    out, _ = failure_aware_run
    store = RunStore(out, create=False)

    stage_events = store.read_events("stage_changed")
    to_fa = [e["payload"] for e in stage_events
             if e["payload"].get("to") == "failure_aware"]
    assert to_fa, (
        "闭环从未进入 failure_aware 阶段——FSM 第三状态是端到端盲区："
        f"stage_changed={[e['payload'] for e in stage_events]}"
    )
    # streak 驱动的真实迁移（连续两轮高嫌疑），非规则注入
    assert any(p.get("criterion", "").startswith("high_suspect_streak") for p in to_fa), \
        f"failure_aware 非 streak 驱动：{to_fa}"

    fa_round = to_fa[0]["round_id"]
    exp = next(e for e in store.list_experiments() if e.round_id == fa_round)
    assert exp.provenance.generator == RISK_DISCOUNT_GEN, (
        f"第 {fa_round} 轮进 failure_aware 却未用风险贴现生成器："
        f"generator={exp.provenance.generator!r}——风险贴现分支未被闭环执行"
    )


# ================================================================ 变异 F-3：软信任降权

@pytest.fixture(scope="module")
def soft_run(tmp_path_factory):
    """os-soft 5 轮，眩光 band 变体（glare prob→0.2、suspect_high→0.95）——与
    test_loop_soft 同款场景，产生确定性非空 QUARANTINE 带（软信任降权复归的入料）。"""
    dom = _crystal_variant(
        tmp_path_factory.mktemp("dom_f"),
        glare_prob=0.2, trust={"suspect_high": 0.95, "quarantine_low": 0.3},
    )
    out = tmp_path_factory.mktemp("runs") / "mech_f"
    summary = run_loop(dom, mode="os-soft", rounds=5, seed=7, out_dir=out)
    return out, summary


def test_F3_soft_trust_downweights_quarantined_alpha(soft_run):
    """击杀变异 F-3（SoftTrustAggregation._weight 恒 1，降权失效）。

    经 os-soft 真实 run 断言：软并入观测（QUARANTINE 合成副本）的 per-point alpha
    **显著大于**同批 TRUSTED 观测的 alpha——降权=alpha 乘性膨胀（alpha=base/w, w<1）
    真的发生。取数自 ``aggregation_alpha`` 观测面（entries 与训练观测同序），软副本 obs_id
    与落盘 QUARANTINE 观测同 id → 按落盘 trust/routing 交叉分类。

    正常码 w≈0.077（眩光 s=0.90）→ 膨胀 ≈13×；变异 F-3 下 w≡1 → alpha==base ≈ 同批
    TRUSTED alpha → 比值坍到 ≈1 → 本测试转红。"""
    out, _ = soft_run
    store = RunStore(out, create=False)

    quarantine_ids = {
        o.obs_id for o in store.list_observations(trust=TrustLevel.SUSPECT)
        if o.routing == Routing.QUARANTINE
    }
    trusted_ids = {o.obs_id for o in store.list_observations(trust=TrustLevel.TRUSTED)}
    assert quarantine_ids, "眩光 band 场景下 QUARANTINE 带必须非空（软信任无入料则测试无意义）"

    alpha_events = store.read_events("aggregation_alpha")
    assert alpha_events, "aggregation_alpha 观测面缺席——聚合 alpha 未发射"
    last = alpha_events[-1]["payload"]
    assert last["aggregation"] == "soft_trust"

    soft_alpha, trust_alpha = [], []
    for ent in last["entries"]:
        if ent["alpha"] is None:
            continue
        if ent["obs_id"] in quarantine_ids:
            soft_alpha.append(ent["alpha"])
        elif ent["obs_id"] in trusted_ids:
            trust_alpha.append(ent["alpha"])

    assert soft_alpha, "训练集里没有 QUARANTINE 软副本——软并入未发生"
    assert trust_alpha, "训练集里没有 TRUSTED 观测——无法对照"

    soft_med = statistics.median(soft_alpha)
    trust_med = statistics.median(trust_alpha)
    assert trust_med > 0.0, "TRUSTED alpha 中位数为 0，无法构成有效对照基线"
    # 正常 ≈13×；阈值 3× 稳落在 正常(13) 与 变异(≈1) 之间——变异 F-3 下必然跌破
    assert soft_med > 3.0 * trust_med, (
        f"软信任降权未发生：soft alpha 中位={soft_med:.6g} 未显著超过 "
        f"TRUSTED 中位={trust_med:.6g}（比值 {soft_med / trust_med:.2f}，"
        "变异 F-3(_weight≡1) 会使比值坍到 ≈1）"
    )
    # 每条软副本都被降权（alpha 严格大于 0，且整体最小软 alpha 高于 TRUSTED 中位）
    assert min(soft_alpha) > 0.0


# ================================================================ M13 消融臂 · 机制活性反向断言
#
# 机制活性观测面（risk_map_applied / attribution / action_consumed）的第一个消融用途
# （R2 §2.1 / §1.2）：消融臂关掉某机制后，对应活性事件应**反向坍缩**——而 os 家族公共
# 栈（三级 QC 检出/路由）在同一场景上照常，证明只消掉了目标机制、没连累检出。

@pytest.fixture(scope="module")
def minus_riskmap_run(tmp_path_factory):
    """os-minus-riskmap 3 轮，第 2 轮强边缘（与变异 E 同场景，只是臂换成消融臂）。"""
    dom = _crystal_variant(tmp_path_factory.mktemp("dom_mr"), edge_rounds=(2,))
    out = tmp_path_factory.mktemp("runs") / "abl_riskmap"
    run_loop(dom, mode="os-minus-riskmap", rounds=3, seed=7, out_dir=out)
    return out


def test_ablation_riskmap_is_none(minus_riskmap_run):
    """os-minus-riskmap：risk_map_applied 观测面 **is_none 恒 True**（生产接线断开，
    与变异 E 同形——机制活性观测面的第一个消融用途）。对比 test_E_*（os 全栈）：那里
    is_none 恒 False 且至少一轮非常数。检出照常（每轮 qc_report）。"""
    store = RunStore(minus_riskmap_run, create=False)
    applied = store.read_events("risk_map_applied")
    assert applied, "risk_map_applied 观测面缺席"
    assert all(e["payload"]["is_none"] for e in applied), (
        "os-minus-riskmap 却有非 None 风险图——风险图生产接线未被消融："
        f"{[(e['payload']['round_id'], e['payload']['is_none']) for e in applied]}"
    )
    assert all(e["payload"]["n_distinct"] == 0 for e in applied)
    # os 家族公共栈：三级 QC 检出照常
    assert len(store.read_events("qc_report")) == 3


@pytest.fixture(scope="module")
def minus_arbiter_run(tmp_path_factory):
    """os-minus-arbiter 3 轮，第 2 轮强边缘 → 归因照常产 next_action，但仲裁空转。"""
    dom = _crystal_variant(tmp_path_factory.mktemp("dom_ma"), edge_rounds=(2,))
    out = tmp_path_factory.mktemp("runs") / "abl_arbiter"
    run_loop(dom, mode="os-minus-arbiter", rounds=3, seed=7, out_dir=out)
    return out


def test_ablation_arbiter_consumes_nothing(minus_arbiter_run):
    """os-minus-arbiter：归因**照常**产 next_action（上游动作可用），但 action_consumed
    观测面**全程缺席**（仲裁空转，零动作物化进候选）——回答"闭环动作贡献"。"""
    store = RunStore(minus_arbiter_run, create=False)
    has_next_action = any(
        o.next_action is not None
        for t in (TrustLevel.SUSPECT, TrustLevel.FAILED)
        for o in store.list_observations(trust=t)
    )
    assert has_next_action, "场景未产 next_action——反向断言失去意义（需先有可仲裁的动作）"
    assert store.read_events("action_consumed") == [], (
        "os-minus-arbiter 却消费了动作——仲裁未被消融"
    )
    assert len(store.read_events("qc_report")) == 3  # 检出照常


@pytest.fixture(scope="module")
def minus_attribution_run(tmp_path_factory):
    """os-minus-attribution 3 轮，第 2 轮强边缘 → QC 检出照常但不做归因。"""
    dom = _crystal_variant(tmp_path_factory.mktemp("dom_at"), edge_rounds=(2,))
    out = tmp_path_factory.mktemp("runs") / "abl_attr"
    summary = run_loop(dom, mode="os-minus-attribution", rounds=3, seed=7, out_dir=out)
    return out, summary


def test_ablation_attribution_detects_but_no_attribution(minus_attribution_run):
    """os-minus-attribution：三级 QC **检出照常**（有 SUSPECT），但 attribution 观测面
    **全程缺席**、无观测带 failure_attr/next_action——归因-动作链被从源头切断。"""
    out, summary = minus_attribution_run
    store = RunStore(out, create=False)
    assert summary["n_suspect"] > 0, "边缘场景应有 SUSPECT——检出面必须照常"
    assert store.read_events("attribution") == [], (
        "os-minus-attribution 却发了 attribution 事件——归因未被消融"
    )
    for t in (TrustLevel.SUSPECT, TrustLevel.FAILED):
        for o in store.list_observations(trust=t):
            assert o.failure_attr is None and o.next_action is None, (
                f"obs {o.obs_id} 带归因/动作——attributor 未真正关闭"
            )
    assert len(store.read_events("qc_report")) == 3


# ================================================================ R4 I-F1: NO_COVERAGE tri-state gate

def test_scan_no_coverage_when_zero_telemetry(tmp_path):
    """R4 I-F1 discriminative test, state 1/3: a should-activate mechanism with
    ZERO grade events (pre-observation-surface data) must yield a NO_COVERAGE
    violation, not an empty (all-green) result. Guards against the hollow-green
    gate: should-activate ∩ has-telemetry = ∅ previously scanned as pass."""
    from expos.kernel.store import RunStore
    from expos.eval.activity_budget import scan_run
    root = tmp_path / "S2.edge_evaporation.0.35__os__s1000"
    store = RunStore(root, create=True)
    store.append_event("run_start", {"seed": 1})  # no risk_map_applied at all
    store.write_checkpoint({"completed_rounds": 0, "mode": "os"})
    v = scan_run(root)
    assert any(x.get("status") == "NO_COVERAGE" for x in v), \
        "zero telemetry on should-activate mechanism must not scan green"


def test_scan_breach_on_dead_stream(tmp_path):
    """State 2/3: a synthetic dead stream (k consecutive non-active grades) must
    still breach -- the NO_COVERAGE branch must not swallow real breaches."""
    from expos.kernel.store import RunStore
    from expos.eval.activity_budget import scan_run, DEFAULT_K
    root = tmp_path / "S2.edge_evaporation.0.35__os__s1001"
    store = RunStore(root, create=True)
    for r in range(DEFAULT_K + 1):
        store.append_event("risk_map_applied", {
            "round_id": r, "grade": "warning",
            "summary": {"is_none": False, "n_distinct": 1},
        })
    store.write_checkpoint({"completed_rounds": DEFAULT_K + 1, "mode": "os"})
    v = scan_run(root)
    assert any(x.get("status") == "BREACH" for x in v)


def test_scan_green_on_live_stream(tmp_path):
    """State 3/3: a live stream (active grades present, no k-run of inactivity)
    must produce zero violations -- NO_COVERAGE must not fire when telemetry exists."""
    from expos.kernel.store import RunStore
    from expos.eval.activity_budget import scan_run
    root = tmp_path / "S2.edge_evaporation.0.35__os__s1002"
    store = RunStore(root, create=True)
    for r in range(8):
        store.append_event("risk_map_applied", {
            "round_id": r, "grade": "active" if r % 2 == 0 else "warning",
            "summary": {"is_none": False, "n_distinct": 2},
        })
    store.write_checkpoint({"completed_rounds": 8, "mode": "os"})
    assert scan_run(root) == []
