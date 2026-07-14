"""变异语料击杀：kernel/lifecycle.py（MU 第一波 L4/L5 存活变异）。

- L4 [P2]：adjudicate TRUSTED 置信 = 1.0 − suspicion（连续证据）。恒定成 1.0 后"无论多嫌疑
  都满分信任"，旧 adjudication_table 只查 0≤conf≤1 不查具体值。钉：TRUSTED conf==1−suspicion。
- L5 [P2]：高危翻案 reason 非空校验 `not reason.strip()`。弱化成 `not reason` 后纯空白理由
  蒙混过关（审计漏洞）。钉：高危翻案传纯空白 reason 抛 LifecycleError。
"""

import pytest

from expos.kernel.lifecycle import (
    LifecycleError,
    TrustPolicy,
    adjudicate,
    reclassify,
    route_observation,
)
from expos.kernel.objects import Actor, Routing, TrustLevel
from expos.kernel.store import RunStore

from tests.test_kernel import make_experiment, make_observation


def test_trusted_confidence_is_one_minus_suspicion(tmp_path):
    """suspicion=0.10（< quarantine_low）→ TRUSTED，置信 = 0.90（连续证据）。
    恒定成 1.0 的变异 → 断言必红。"""
    exp = make_experiment()
    obs = make_observation(exp, suspicion=0.10)
    trust, routing, conf = adjudicate(obs.qc, TrustPolicy())
    assert trust == TrustLevel.TRUSTED
    assert conf == pytest.approx(0.90)  # 1.0 − 0.10，非恒 1.0


def test_high_risk_reclassify_rejects_whitespace_reason(tmp_path):
    """高危翻案（SUSPECT→TRUSTED）传纯空白 reason 必须响亮抛。
    `not reason.strip()` → `not reason` 后纯空白蒙混过关 → 断言必红。"""
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = route_observation(store, make_observation(exp, suspicion=0.45))  # SUSPECT/QUARANTINE
    with pytest.raises(LifecycleError):
        reclassify(
            store, obs.obs_id, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL,
            actor=Actor.HUMAN, reason="   ",  # 纯空白
        )
