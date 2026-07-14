"""R5 REF-4 F3/F4: property hardening for qc/stats primitives.

The mutmut run showed a 36-mutant surviving cluster in cusum, root cause being
that every caller and every test pins sd=1.0, target=0.0 -- so the entire
standardization core (division vs multiplication, self-starting defaults,
alarm boundary) was invisible to the suite. These tests make each blind spot
individually lethal to its mutation class.
"""
import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from expos.qc.stats import cusum, StatsError


def test_cusum_nontrivial_sd_kills_division_flip():
    """sd=2.0 (non-trivial): z=(x-mu)/s vs (x-mu)*s now differ 4x. A step of
    +4 raw units == +2 sigma; with k=0.5 the positive cusum after 3 post-step
    points must sit near 3*(2-0.5)=4.5 -- the *s mutant would give ~ 3*(8-0.5)."""
    x = np.array([0.0, 0.0, 0.0, 4.0, 4.0, 4.0])
    out = cusum(x, k=0.5, h=100.0, target=0.0, sd=2.0)
    assert out["pos"][-1] == pytest.approx(4.5)


def test_cusum_self_starting_defaults_are_sample_moments():
    """Zero-coverage default path: target None -> sample mean, sd None -> ddof=1
    std. Pin the exact semantics so the defaults can never silently change."""
    rng = np.random.default_rng(7)
    x = rng.normal(3.0, 1.5, size=40)
    out_default = cusum(x, k=0.5, h=5.0)
    out_explicit = cusum(x, k=0.5, h=5.0,
                         target=float(np.mean(x)), sd=float(np.std(x, ddof=1)))
    np.testing.assert_allclose(out_default["pos"], out_explicit["pos"])
    np.testing.assert_allclose(out_default["neg"], out_explicit["neg"])
    assert out_default["alarm_idx"] == out_explicit["alarm_idx"]


def test_cusum_constant_series_raises():
    with pytest.raises(StatsError):
        cusum(np.ones(10), k=0.5, h=5.0)


def test_cusum_alarm_boundary_strictly_greater():
    """Alarm fires on cp > h, not >=: construct a sequence whose cumulative sum
    lands exactly on h (no alarm), then exceeds it by epsilon (alarm)."""
    # z_t = 1.5 each step with k=0.5 -> cp += 1.0 per step; h=3.0 exact at t=2.
    x_exact = np.array([1.5, 1.5, 1.5])
    out = cusum(x_exact, k=0.5, h=3.0, target=0.0, sd=1.0)
    assert out["pos"][-1] == pytest.approx(3.0)
    assert out["alarm_idx"] == []          # == h must NOT alarm
    x_over = np.array([1.5, 1.5, 1.6])
    out2 = cusum(x_over, k=0.5, h=3.0, target=0.0, sd=1.0)
    assert out2["alarm_idx"] == [2]        # > h must alarm


@settings(max_examples=200, deadline=None)
@given(
    data=st.lists(st.floats(-50, 50), min_size=3, max_size=40),
    scale=st.floats(0.1, 30.0),
    target=st.floats(-10, 10),
    sd=st.floats(0.2, 8.0),
)
def test_cusum_scale_invariance_property(data, scale, target, sd):
    """REF-4 F3 step 3: cusum(a*x, target=a*mu, sd=a*s) === cusum(x, mu, s).
    This is the property that structurally forbids the /s -> *s mutation and any
    unit-dependent drift in the standardization core."""
    x = np.asarray(data)
    base = cusum(x, k=0.5, h=4.0, target=target, sd=sd)
    scaled = cusum(scale * x, k=0.5, h=4.0, target=scale * target, sd=scale * sd)
    np.testing.assert_allclose(base["pos"], scaled["pos"], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(base["neg"], scaled["neg"], rtol=1e-9, atol=1e-9)
    assert base["alarm_idx"] == scaled["alarm_idx"]
