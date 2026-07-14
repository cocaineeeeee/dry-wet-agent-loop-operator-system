"""Wet-side time-series observation with a hidden DYNAMIC truth surface (M26 v0.1).

The dynamic-phenotype analogue of ``sim_reader``'s scalar plate-reader truth surface. Where
``sim_reader.TruthSurface`` maps a design coordinate to a scalar Gaussian response, this maps
a design coordinate to a TIME-SERIES TRACE whose DERIVED dynamic phenotype (steady state /
response amplitude / switching time, via ``circuit_dynamics``) carries the sign of the face.
It is the wet leg the behaviour claim is certified against (only trusted observation certifies;
docs/bio_refs/02 §3).

The three DYNAMIC acceptance faces (high / flipped / flat), obeying the only-mu-differs K-D law
so the face flips the SIGN of the coord->phenotype relation WITHOUT changing any other
statistical property:
  * ``dynamic_high``    (mu=0.85, positive): stronger-design circuits reach a HIGHER settled
                        dynamic phenotype -- the expected/positive dynamic. The reporter rises
                        further and faster with the design coordinate.
  * ``dynamic_flipped`` (mu=0.20, negative): the relation is INVERTED -- weaker-design circuits
                        reach the higher phenotype (contradicts the seeded "stronger-wins").
  * ``dynamic_flat``    (NULL, amplitude 0): NO dynamic -- the trace is flat at baseline for
                        every coordinate (no rise, no switching, ~zero response amplitude); a
                        correct aggregator must return "insufficient", never fabricate a claim.

Truth isolation: the OS-visible observation is the DERIVED dynamic-phenotype summary (value +
secondary); the true steady-state target / applied noise live only in the returned truth
record (server-only sidecar; never handed to qc/planner/agent).

Honesty / limits (BIOLOGY_PROGRAM_2026 §5): validation level is ``simulation``. This v0.1 reader
is an IN-PROCESS function surface, NOT the full socket ``sim_reader`` server. The SEAM to (a)
register these dynamic faces + a time-series observable schema in the shared truth registry and
(b) drive the trace through the real socket reader is a B (integration-owner) item
(docs/bio_seams/M26.md). Faces are proven domain-locally here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from expos.adapters.dry.circuit_dynamics import DynamicPhenotype, derive_phenotype
from expos.adapters.dry.circuit_simulation import TimeSeries

# Realised-coordinate window (design coord in [0,1] -> physical coordinate). Chosen so the
# whole window sits on ONE flank of each signal face's Gaussian -> strict monotonicity, the
# same construction the chemistry/M24 wet legs use.
WINDOW_LO = 0.30
WINDOW_HI = 0.75

#: face -> mu (peak coordinate). Signal faces differ ONLY in mu; amplitude/sigma/baseline
#: are identical (the K-D "only mu differs" law, now over a DYNAMIC phenotype).
DYNAMIC_TRUTH_PROFILES: dict[str, float] = {
    "dynamic_high": 0.85,
    "dynamic_flipped": 0.20,
    "dynamic_flat": 0.85,
}
_NULL_PROFILES: frozenset[str] = frozenset({"dynamic_flat"})
DEFAULT_DYNAMIC_PROFILE = "dynamic_high"

_AMPLITUDE = 1.0
_SIGMA = 0.15
_BASELINE = 0.05
_TAU = 3.0  # first-order rise time constant of the reporter trace


def realised_coord(coord: float) -> float:
    """Map a design coordinate in [0,1] into the realised physical window."""
    return WINDOW_LO + coord * (WINDOW_HI - WINDOW_LO)


@dataclass(frozen=True)
class DynamicTruthSurface:
    """Hidden dynamic truth surface: a coordinate -> settled-level Gaussian (only-mu-differs),
    realised as a first-order rise in time. Never leaves the reader process."""

    amplitude: float = _AMPLITUDE
    mu: float = 0.85
    sigma: float = _SIGMA
    baseline: float = _BASELINE
    tau: float = _TAU

    @classmethod
    def from_profile(cls, profile: str = DEFAULT_DYNAMIC_PROFILE) -> "DynamicTruthSurface":
        if profile not in DYNAMIC_TRUTH_PROFILES:
            raise ValueError(
                f"unknown dynamic truth_profile {profile!r}; known: {sorted(DYNAMIC_TRUTH_PROFILES)}"
            )
        if profile in _NULL_PROFILES:
            return cls(amplitude=0.0, mu=DYNAMIC_TRUTH_PROFILES[profile])
        return cls(mu=DYNAMIC_TRUTH_PROFILES[profile])

    def settled_level(self, coord: float) -> float:
        """The true steady-state level the reporter rises to for this design coordinate."""
        p = realised_coord(coord)
        z = (p - self.mu) / self.sigma
        return self.amplitude * math.exp(-0.5 * z * z) + self.baseline

    def trace(
        self,
        coord: float,
        *,
        t_end: float = 20.0,
        dt: float = 0.1,
        noise_sd: float = 0.0,
        seed: int | None = None,
    ) -> TimeSeries:
        """Generate the reporter time-series for this coordinate: a first-order rise from 0
        to ``settled_level(coord)`` with time constant ``tau``, plus optional measurement
        noise (seeded, reproducible). A null face rises to baseline only (flat, no dynamic)."""
        n = int(round(t_end / dt))
        t = np.linspace(0.0, n * dt, n + 1)
        target = self.settled_level(coord)
        if self.amplitude == 0.0:
            # NULL (flat) face: genuinely NO dynamic -- constant at baseline, so response
            # amplitude is 0 and there is no switching, for every coordinate alike.
            y = np.full_like(t, target)
        else:
            y = target * (1.0 - np.exp(-t / self.tau))
        if noise_sd > 0.0:
            rng = np.random.default_rng(seed)
            y = np.maximum(y + rng.normal(0.0, noise_sd, size=y.shape), 0.0)
        return TimeSeries(t=t, series={"reporter": y}, stochastic=noise_sd > 0.0,
                          seed=seed, dt=dt)


def read_dynamic(
    coord: float,
    *,
    profile: str = DEFAULT_DYNAMIC_PROFILE,
    t_end: float = 20.0,
    dt: float = 0.1,
    noise_sd: float = 0.02,
    seed: int | None = None,
) -> tuple[DynamicPhenotype, dict]:
    """Take ONE wet dynamic reading: generate the hidden-truth reporter trace, derive its
    dynamic phenotype (the OS-visible observation), and return it paired with a truth-sidecar
    record (server-only). The phenotype summary is what enters the claim lifecycle; the true
    settled level / noise stay in the sidecar (truth isolation)."""
    surface = DynamicTruthSurface.from_profile(profile)
    ts = surface.trace(coord, t_end=t_end, dt=dt, noise_sd=noise_sd, seed=seed)
    pheno = derive_phenotype(ts.t, ts.series["reporter"], value_key="steady_state")
    truth_record = {
        "profile": profile,
        "coord": float(coord),
        "realised_coord": realised_coord(coord),
        "settled_level_true": surface.settled_level(coord),
        "noise_sd": noise_sd,
        "seed": seed,
    }
    return pheno, truth_record
