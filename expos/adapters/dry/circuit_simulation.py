"""Deterministic ODE + optional Langevin stochastic-proxy simulation of a typed genetic
circuit graph (M26 v0.1). PURE numpy, NO scipy, NO heavy library (BIOLOGY_PROGRAM_2026
§1.5: "简单 ODE 或 stochastic proxy（纯 Python，不引重库）").

Model (dimensionless Hill production/degradation; docs/bio_refs/02 §3 aleatoric/epistemic
split): each transcription unit's product species x evolves as

    dx/dt = basal + beta * PROD_over_regulators( hill(kind, regulator_level) ) - gamma * x

    repression hill = 1 / (1 + (r/K)^n)      activation hill = (r/K)^n / (1 + (r/K)^n)

An unregulated (constitutive) unit has an empty product -> production = basal + beta.

Integrator: fixed-step RK4 (deterministic). The optional stochastic proxy adds a
chemical-Langevin diffusion term ``sqrt(|production| + |gamma*x|) * sqrt(dt) * eta`` with
``eta ~ N(0,1)`` from a SEEDED numpy Generator, so a stochastic run is reproducible given
its seed. INTRINSIC NOISE is the aleatoric / within-replicate variance source (docs/bio_refs/02
§3): it is a technical-replicate axis, NOT independent biological evidence -- the domain / QC
layer owns that distinction, this module only PRODUCES the trace.

Truth-semantics: this is a DRY simulation proxy. It carries model error and is NOT truth; it
never certifies a claim. The hidden wet dynamic truth surface lives in timeseries_reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

from domains.genetic_circuit.graph import (
    INT_ACTIVATES,
    INT_REPRESSES,
    CircuitGraph,
)


@dataclass(frozen=True)
class TimeSeries:
    """A simulated time-series: shared time grid ``t`` (n,) + per-species trajectories.

    ``series[name]`` is an (n,) numpy array. ``stochastic``/``seed`` record how it was
    produced (provenance for the machine report)."""

    t: np.ndarray
    series: Mapping[str, np.ndarray]
    stochastic: bool
    seed: int | None
    dt: float

    def final(self, species: str) -> float:
        return float(self.series[species][-1])

    def as_summary_payload(self) -> dict:
        """Compact JSON-able summary (endpoints only) for content-store / report. The full
        trace stays in the domain layer; the kernel only ever sees derived scalars."""
        return {
            "t_end": float(self.t[-1]),
            "n_steps": int(self.t.size),
            "dt": self.dt,
            "stochastic": self.stochastic,
            "seed": self.seed,
            "final": {k: float(v[-1]) for k, v in self.series.items()},
        }


def _hill(kind: str, level: float, K: float, n: float) -> float:
    ratio = (max(level, 0.0) / K) ** n
    if kind == INT_REPRESSES:
        return 1.0 / (1.0 + ratio)
    if kind == INT_ACTIVATES:
        return ratio / (1.0 + ratio)
    raise ValueError(f"unknown interaction kind {kind!r}")


def _build_derivative(graph: CircuitGraph) -> tuple[list[str], Callable[[np.ndarray], np.ndarray]]:
    """Return (species order, f(state)->dstate) built from the graph wiring. Species are
    the unit products, in unit declaration order."""
    species = [u.product for u in graph.units]
    idx = {s: i for i, s in enumerate(species)}
    units = list(graph.units)
    # regulators grouped by target unit.
    regs_by_tu: dict[str, list] = {u.tu_id: [] for u in units}
    for i in graph.interactions:
        regs_by_tu.setdefault(i.target_tu, []).append(i)

    def f(state: np.ndarray) -> np.ndarray:
        d = np.zeros_like(state)
        for u in units:
            k = u.kinetics
            prod = 1.0
            for inter in regs_by_tu.get(u.tu_id, ()):
                r_level = state[idx[inter.regulator]] if inter.regulator in idx else 0.0
                prod *= _hill(inter.kind, r_level, k.K, k.n)
            production = k.basal + k.beta * prod
            d[idx[u.product]] = production - k.gamma * state[idx[u.product]]
        return d

    return species, f


def simulate(
    graph: CircuitGraph,
    *,
    t_end: float = 20.0,
    dt: float = 0.02,
    initial: Mapping[str, float] | None = None,
    stochastic: bool = False,
    noise_scale: float = 0.05,
    seed: int | None = None,
) -> TimeSeries:
    """Integrate the circuit ODE (RK4) from ``initial`` (default: each product at its own
    basal level) to ``t_end``. If ``stochastic`` add a seeded chemical-Langevin diffusion
    term (intrinsic noise). Deterministic for a fixed (graph, initial, seed). Species are
    clamped to >= 0 (a concentration cannot go negative)."""
    species, f = _build_derivative(graph)
    idx = {s: i for i, s in enumerate(species)}
    n_steps = int(round(t_end / dt))
    x = np.zeros(len(species), dtype=float)
    for u in graph.units:
        x[idx[u.product]] = u.kinetics.basal
    if initial:
        for s, v in initial.items():
            if s in idx:
                x[idx[s]] = float(v)

    rng = np.random.default_rng(seed) if stochastic else None
    traj = np.zeros((n_steps + 1, len(species)), dtype=float)
    traj[0] = x
    for step in range(n_steps):
        k1 = f(x)
        k2 = f(x + 0.5 * dt * k1)
        k3 = f(x + 0.5 * dt * k2)
        k4 = f(x + dt * k3)
        x = x + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        if stochastic and rng is not None:
            # chemical-Langevin diffusion: magnitude ~ sqrt(total flux) per species.
            flux = np.abs(f(x)) + np.abs(x)
            x = x + noise_scale * np.sqrt(flux) * np.sqrt(dt) * rng.standard_normal(len(species))
        x = np.maximum(x, 0.0)
        traj[step + 1] = x

    t = np.linspace(0.0, n_steps * dt, n_steps + 1)
    return TimeSeries(
        t=t,
        series={s: traj[:, idx[s]].copy() for s in species},
        stochastic=stochastic,
        seed=seed,
        dt=dt,
    )
