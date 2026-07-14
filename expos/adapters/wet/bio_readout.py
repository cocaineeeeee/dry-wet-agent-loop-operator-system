"""Biological plate-reader readout normalization (M24, ``cell_free_expression_screen``).

Percent-of-control normalization for cell-free expression fluorescence, performed in the
DOMAIN / READOUT layer -- BEFORE ingest and certification, NEVER inside the evidence
compiler. This is the biological analogue of a per-plate normalization step: raw
fluorescence a.u. depend on the reader gain, the batch, and the background of the lysate,
so a raw number is not comparable across plates. Expressing each well as a percent of a
known-strong POSITIVE control after subtracting the no-template NEGATIVE control
background yields a batch-robust, comparable readout (pycytominer ``percent-of-control``
/ standard CFPS "percent of positive control" semantics).

RED-LINE PLACEMENT (charter ruling ③): normalization is a READOUT-LAYER transform that
runs on raw values + control baselines *before* the observation is ingested/certified.
It MUST NOT enter the evidence compiler (the ``certification_stats`` aggregator), which
is a structurally domain-neutral consumer that reads only ``result.value`` on TRUSTED
observations. This module therefore:
  * imports NO kernel evidence/certification/claim symbol (pure functions on floats +
    an optional convenience over plain records);
  * carries the control baselines as EXPLICIT parameters (they are experimental
    calibration inputs, not truth) -- so nothing here can leak the hidden truth surface.

The controls it consumes are the charter-required trio (declared on the wet leg, see the
domain yaml note): ``negative`` (no-template / no-construct background fluorescence),
``positive`` (a known strong-expression reference), and ``reference`` (a calibration
sentinel). Only negative + positive set the percent-of-control scale; ``reference`` is a
drift sentinel checked elsewhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The smallest positive dynamic range (positive_baseline - negative_baseline) we will
#: divide by; a range at/under this is a degenerate calibration (positive ~= negative)
#: and is refused LOUDLY rather than producing a divide-by-~0 explosion.
_MIN_DYNAMIC_RANGE = 1e-9


class ReadoutError(ValueError):
    """Loud rejection from the readout-normalization layer (degenerate control
    baselines, non-finite input). A calibration that cannot define a scale must fail
    here, never silently pass a garbage normalized value downstream."""


@dataclass(frozen=True)
class ControlBaselines:
    """The percent-of-control calibration pair: the negative-control background and the
    positive-control reference level (raw fluorescence a.u.). Experimental calibration
    inputs, NOT truth. Derived from control wells by :func:`baselines_from_controls`."""

    negative: float
    positive: float

    def dynamic_range(self) -> float:
        return self.positive - self.negative


def _finite(value: float, what: str) -> float:
    v = float(value)
    if not math.isfinite(v):
        raise ReadoutError(f"{what} must be finite, got {value!r}")
    return v


def baseline_subtract(value: float, negative_baseline: float) -> float:
    """Subtract the no-template negative-control background from a raw reading. The
    first, always-valid step of readout correction (a bare background subtraction)."""
    return _finite(value, "value") - _finite(negative_baseline, "negative_baseline")


def percent_of_control(
    value: float, baselines: ControlBaselines, *, clip_negative: bool = False
) -> float:
    """Percent-of-control normalized readout:
    ``100 * (value - negative) / (positive - negative)`` (pycytominer semantics).

    The positive control maps to ~100, the negative-control background to 0. A degenerate
    calibration (dynamic range <= :data:`_MIN_DYNAMIC_RANGE`) is refused LOUDLY. When
    ``clip_negative`` is set, sub-background readings floor at 0.0 (a construct cannot
    express below the no-template background in a real sense); default keeps the signed
    value so the transform is invertible and honest about below-background noise."""
    rng = baselines.dynamic_range()
    if rng <= _MIN_DYNAMIC_RANGE:
        raise ReadoutError(
            "degenerate control calibration: positive baseline "
            f"({baselines.positive!r}) is not meaningfully above negative "
            f"({baselines.negative!r}); dynamic range {rng!r} <= {_MIN_DYNAMIC_RANGE}"
        )
    pct = 100.0 * (_finite(value, "value") - baselines.negative) / rng
    if clip_negative and pct < 0.0:
        return 0.0
    return pct


def log_expression(value: float, *, pseudo_count: float = 1.0) -> float:
    """Optional ``log10(value + pseudo_count)`` compression for the heavy-tailed
    fluorescence range (the pycytominer ``spherize`` / log-normalize option). The
    pseudo-count keeps sub-1 and zero readings finite. Applied AFTER background
    subtraction when used together."""
    v = _finite(value, "value") + _finite(pseudo_count, "pseudo_count")
    if v <= 0.0:
        raise ReadoutError(
            f"log_expression needs value + pseudo_count > 0, got {v!r}"
        )
    return math.log10(v)


def baselines_from_controls(
    negative_values: list[float], positive_values: list[float]
) -> ControlBaselines:
    """Derive the percent-of-control calibration from the plate's control wells: the mean
    of the negative (no-template background) wells and of the positive (strong-reference)
    wells. Empty control sets are a LOUD failure -- a normalization that silently invents
    a baseline is worse than no normalization (the charter requires real controls)."""
    if not negative_values:
        raise ReadoutError(
            "no negative-control wells: cannot define the background baseline "
            "(the charter-required negative control is missing from the plate)"
        )
    if not positive_values:
        raise ReadoutError(
            "no positive-control wells: cannot define the percent-of-control scale "
            "(the charter-required positive control is missing from the plate)"
        )
    neg = sum(_finite(v, "negative value") for v in negative_values) / len(negative_values)
    pos = sum(_finite(v, "positive value") for v in positive_values) / len(positive_values)
    return ControlBaselines(negative=neg, positive=pos)


def normalize_readings(
    values: list[float],
    baselines: ControlBaselines,
    *,
    log_first: bool = False,
    clip_negative: bool = False,
) -> list[float]:
    """Percent-of-control normalize a whole plate's raw readings against one calibration.
    Optionally log-compress each raw reading first (then the baselines must also be in
    log space -- the caller passes log-space baselines). Pure and deterministic."""
    if log_first:
        return [
            percent_of_control(log_expression(v), baselines, clip_negative=clip_negative)
            for v in values
        ]
    return [
        percent_of_control(v, baselines, clip_negative=clip_negative) for v in values
    ]
