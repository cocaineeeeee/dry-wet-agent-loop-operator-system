"""Typed objects for the M28 biology-discovery multi-agent organ (v0.1 skeleton).

A Hypothesis is a directional causal proposal ("perturbation P moves axis A in
direction D"). Evidence is a single observed effect on that axis. These are the plain
typed objects the four discovery agents reason over; the load-bearing invariant (charter
red line, M28 DoD #5) is that agents may PROPOSE and ANALYSE freely, but only *trusted*
evidence changes a decisive verdict -- encoded by ``Evidence.trusted``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hypothesis:
    """A directional causal hypothesis competing to explain an axis."""

    hypothesis_id: str
    perturbation: str
    axis: str
    direction: int  # +1 = increases axis, -1 = decreases axis
    statement: str


@dataclass(frozen=True)
class Evidence:
    """One observed effect on an axis. ``trusted`` marks wet/certified evidence; only
    trusted evidence may drive a decisive (supported/rejected) verdict."""

    perturbation: str
    axis: str
    effect: float  # signed observed change
    se: float
    trusted: bool
