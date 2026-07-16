"""Derive DYNAMIC phenotype scalars from a simulated circuit time-series (M26 v0.1).

docs/bio_refs/02 §A/§4: M26's novel step vs the scalar-phenotype domains is that the
observable is a TIME-SERIES, and the phenotype is DERIVED from it -- steady state, response
amplitude, switching time, bistable separation, and (for oscillators) oscillation frequency;
plus a cross-candidate EC50 for a dose-response curve. This module is the shared derivation used by BOTH legs: the dry
circuit adapter (over the ODE proxy) and the wet timeseries reader (over the hidden-truth
trace). Keeping one derivation guarantees dry and wet summarise a trace identically.

All functions are pure and deterministic given a trace. They are DOMAIN-NEUTRAL in shape
(a t-array + a value-array in, a float out) -- no promoter/toggle literal -- which is why
the same summary scalars ride in as ``RawResult.value``/``secondary`` and the kernel never
sees "switching time" as anything but a number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def steady_state(t: np.ndarray, y: np.ndarray, tail_frac: float = 0.2) -> float:
    """Mean of the final ``tail_frac`` of the trajectory (the settled level)."""
    k = max(1, int(round(len(y) * tail_frac)))
    return float(np.mean(y[-k:]))


def response_amplitude(t: np.ndarray, y: np.ndarray) -> float:
    """Peak-to-initial response: ``max(y) - y[0]`` (the dynamic excursion from the start).
    Zero for a flat trace, positive for a rising reporter."""
    return float(np.max(y) - y[0])


def switching_time(t: np.ndarray, y: np.ndarray, frac: float = 0.5) -> float:
    """Time at which the trajectory first crosses ``frac`` of the way from its initial to
    its steady-state level (a half-rise / switching time). Returns ``t[-1]`` if it never
    crosses (e.g. a flat trace) -- an honest "did not switch within the window" sentinel."""
    ss = steady_state(t, y)
    y0 = float(y[0])
    if abs(ss - y0) < 1e-9:
        return float(t[-1])
    threshold = y0 + frac * (ss - y0)
    rising = ss > y0
    for i in range(len(y)):
        if (rising and y[i] >= threshold) or (not rising and y[i] <= threshold):
            return float(t[i])
    return float(t[-1])


def oscillation_frequency(
    t: np.ndarray,
    y: np.ndarray,
    *,
    tail_frac: float = 0.5,
    min_rel_amplitude: float = 0.05,
    min_cycles: int = 2,
) -> float:
    """Dominant oscillation frequency (cycles per unit time) of the SETTLED tail, derived by
    upward mean-crossing counting -- the fourth derived DYNAMIC phase (docs/bio_refs/02 §A:
    oscillation frequency), the one a monotone-rise / flat trace does NOT have. Robust and
    library-free (no FFT dependency): de-mean the last ``tail_frac`` of the trace, find upward
    zero (mean) crossings, take the reciprocal of the mean inter-crossing period.

    Returns ``0.0`` (an honest "no oscillation" sentinel) when the tail excursion is below
    ``min_rel_amplitude`` of the tail mean (a flat / monotone-settled trace) or fewer than
    ``min_cycles`` crossings are seen -- so a cassette rise and a flat null both read 0.0."""
    k = max(4, int(round(len(y) * tail_frac)))
    tail = np.asarray(y[-k:], dtype=float)
    ttail = np.asarray(t[-k:], dtype=float)
    mean = float(np.mean(tail))
    span = float(np.max(tail) - np.min(tail))
    ref = max(abs(mean), 1e-9)
    if span / ref < min_rel_amplitude:
        return 0.0
    de = tail - mean
    crossings = [
        ttail[i] for i in range(1, len(de)) if de[i - 1] < 0.0 <= de[i]
    ]
    if len(crossings) < min_cycles:
        return 0.0
    period = float(np.mean(np.diff(crossings)))
    if period <= 0.0:
        return 0.0
    return 1.0 / period


def ec50_from_curve(doses: np.ndarray, responses: np.ndarray) -> float:
    """Estimate the EC50 (half-maximal dose) of a monotone dose-response CURVE by linear
    interpolation to the half-maximal response -- the derived phase of a ``dose_response``
    circuit (a phase that lives ACROSS candidates, not within one trace). ``doses`` must be
    ascending. Returns the smallest dose at which the response first reaches
    ``min + 0.5*(max-min)``; ``nan`` if the curve is flat (no half-max to cross)."""
    d = np.asarray(doses, dtype=float)
    r = np.asarray(responses, dtype=float)
    lo, hi = float(np.min(r)), float(np.max(r))
    if hi - lo < 1e-12:
        return float("nan")
    half = lo + 0.5 * (hi - lo)
    for i in range(1, len(r)):
        if (r[i - 1] < half <= r[i]) or (r[i - 1] >= half > r[i]):
            # linear interpolation between the bracketing dose points.
            frac = (half - r[i - 1]) / (r[i] - r[i - 1])
            return float(d[i - 1] + frac * (d[i] - d[i - 1]))
    return float(d[int(np.argmin(np.abs(r - half)))])


@dataclass(frozen=True)
class DynamicPhenotype:
    """The derived dynamic phenotype of one time-series (one reporter species).

    ``value`` is the load-bearing summary scalar (rides in as ``RawResult.value``); the
    others ride in ``secondary``. ``separation`` is populated for two-node circuits (the
    bistable arm difference); 0.0 for single-species circuits."""

    steady_state: float
    response_amplitude: float
    switching_time: float
    separation: float
    oscillation_frequency: float
    value: float

    def secondary(self) -> dict[str, float]:
        return {
            "steady_state": self.steady_state,
            "response_amplitude": self.response_amplitude,
            "switching_time": self.switching_time,
            "separation": self.separation,
            "oscillation_frequency": self.oscillation_frequency,
        }


def derive_phenotype(
    t: np.ndarray,
    reporter: np.ndarray,
    *,
    antagonist: np.ndarray | None = None,
    value_key: str = "steady_state",
) -> DynamicPhenotype:
    """Derive the dynamic phenotype from a reporter trajectory. If ``antagonist`` is given
    (the opposing arm of a two-node latch), ``separation`` = reporter_ss - antagonist_ss
    (the bistable depth). ``value_key`` picks which scalar is the load-bearing ``value``
    ('steady_state' for a cassette dose response, 'separation' for a toggle latch,
    'oscillation_frequency' for an oscillator)."""
    ss = steady_state(t, reporter)
    amp = response_amplitude(t, reporter)
    sw = switching_time(t, reporter)
    freq = oscillation_frequency(t, reporter)
    sep = 0.0
    if antagonist is not None:
        sep = ss - steady_state(t, antagonist)
    scalars = {"steady_state": ss, "response_amplitude": amp,
               "switching_time": sw, "separation": sep,
               "oscillation_frequency": freq}
    if value_key not in scalars:
        raise ValueError(f"unknown value_key {value_key!r}; known: {sorted(scalars)}")
    return DynamicPhenotype(
        steady_state=ss, response_amplitude=amp, switching_time=sw,
        separation=sep, oscillation_frequency=freq, value=scalars[value_key],
    )
