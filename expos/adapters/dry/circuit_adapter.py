"""CircuitTopologyAdapter (M26 v0.1) -- synchronous, deterministic, in-process DRY adapter
for the ``genetic_circuit`` domain.

The DYNAMIC-phenotype analogue of ``SequenceProxyAdapter``: instead of reducing a construct
sequence to a scalar expression proxy, it takes a TYPED CIRCUIT GRAPH (riding in
``candidate.params['circuit_topology']`` per the M20 zero-kernel-change contract), runs the
cheap deterministic five-level VERIFY gate, then -- only if the topology is legal -- runs the
ODE/stochastic simulation and derives the dynamic phenotype (steady state / response amplitude
/ switching time / bistable separation). The load-bearing summary scalar rides in as
``RawResult.value``, the rest in ``secondary`` -- so the discriminator / QC / ledger stay
bit-for-bit domain-neutral (they never see "switching time" as anything but a number).

Truth-semantics (identical to ``SequenceProxyAdapter``): this is a DRY leg. It ignores
measurement noise (default deterministic), emits only ``RawResult`` material, NEVER produces
``truth_records``, and does NOT mutate the ExperimentObject. The verify verdict is DRY
evidence -- it never certifies a behaviour claim (only trusted wet observation certifies).

Seam note (docs/bio_seams/M26.md): the ``input_kind='circuit_topology'`` capability constant
and this adapter's registration in mcl's dry dispatch are B (integration-owner) items. Until
then the adapter is exercised domain-locally (its ``compute`` / ``execute`` run standalone).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from expos.adapters.base import AdapterError, ExecutionResult, RawResult
from expos.adapters.dry.circuit_dynamics import DynamicPhenotype, derive_phenotype
from expos.adapters.dry.circuit_simulation import simulate
from expos.kernel.objects import ExperimentObject

# Local input-kind constant (SEAM: B folds this into domain_provider.INPUT_KIND_* vocab).
INPUT_KIND_CIRCUIT_TOPOLOGY = "circuit_topology"

_DEFAULT_METRIC = "dynamic_proxy"

# Simulation defaults per behaviour (deterministic; asymmetric IC breaks toggle symmetry so
# the latch settles to a definite state and the bistable separation is well-defined).
_SIM_DEFAULTS: dict[str, dict[str, Any]] = {
    "expression_cassette": {"t_end": 20.0, "dt": 0.02, "value_key": "steady_state"},
    "toggle_switch": {
        "t_end": 40.0, "dt": 0.02, "value_key": "separation",
        "initial": {"TetR": 5.0, "LacI": 0.0},
    },
}


def circuit_params(
    payload: dict[str, Any],
    *,
    coord: float | None = None,
    circuit_id: str | None = None,
) -> dict[str, Any]:
    """Candidate/well ``params`` carrying a serialised circuit graph (from
    ``CircuitGraph.to_payload()``) via the explicit ``circuit_topology`` key -- the M20
    zero-kernel-change contract. ``coord`` (public design coordinate) and ``circuit`` (the
    screening-dim key, biological analogue of ``construct``) are stamped when given."""
    params: dict[str, Any] = {"circuit_topology": payload}
    cid = circuit_id or payload.get("circuit_id")
    if cid is not None:
        params["circuit"] = cid
        params["circuit_id"] = cid
    if coord is not None:
        params["coord"] = float(coord)
    return params


class CircuitTopologyAdapter:
    """Synchronous deterministic dry adapter: typed circuit graph -> dynamic phenotype."""

    name = "circuit_topology"

    #: Capability declaration (docs/bio_refs/02 §C). SEAM: converge to an imported
    #: domain_provider constant once B lands ``INPUT_KIND_CIRCUIT_TOPOLOGY``.
    ACCEPTS_INPUT_KINDS: tuple[str, ...] = (INPUT_KIND_CIRCUIT_TOPOLOGY,)

    default_metric = _DEFAULT_METRIC

    #: whether to reject a topology that fails the verify gate (True = the propose->dry gate
    #: is enforced INSIDE the dry leg too, so an illegal candidate never reaches simulation).
    enforce_verify: bool = True

    # ---- pure compute (deterministic; reusable by loop/tests) ---------------

    def compute(self, params: dict[str, Any]) -> DynamicPhenotype:
        """Verify -> simulate -> derive the dynamic phenotype for one candidate's params.
        Deterministic (same params -> bit-identical phenotype). Loud on a missing topology
        or (when ``enforce_verify``) an illegal one."""
        # imported here so expos.adapters does not hard-require the domain package at import
        from domains.genetic_circuit.graph import circuit_from_payload
        from domains.genetic_circuit.verify import verify

        payload = params.get("circuit_topology")
        if not isinstance(payload, dict):
            raise AdapterError(
                f"{self.name}: params carry no dict `circuit_topology` (got "
                f"{type(payload).__name__}); the typed circuit graph is the dry input"
            )
        graph = circuit_from_payload(payload)

        report = verify(graph)
        if self.enforce_verify and not report.ok:
            raise AdapterError(
                f"{self.name}: circuit {graph.circuit_id!r} failed verify at level "
                f"{report.failed_level!r} -- illegal topology rejected BEFORE simulation "
                f"(highest_passed={report.highest_passed})"
            )

        defaults = _SIM_DEFAULTS.get(graph.behaviour, _SIM_DEFAULTS["expression_cassette"])
        sim_kwargs = {
            k: v for k, v in defaults.items() if k in ("t_end", "dt", "initial")
        }
        # per-candidate stochastic override (intrinsic-noise proxy; seed for determinism).
        if params.get("stochastic"):
            sim_kwargs["stochastic"] = True
            sim_kwargs["seed"] = int(params.get("sim_seed", 0))
            sim_kwargs["noise_scale"] = float(params.get("noise_scale", 0.05))
        ts = simulate(graph, **sim_kwargs)

        reporters = graph.reporters()
        if not reporters:
            raise AdapterError(f"{self.name}: circuit {graph.circuit_id!r} has no reporter unit")
        reporter_species = reporters[0].product
        antagonist = None
        if graph.behaviour == "toggle_switch":
            others = [u.product for u in graph.units if u.product != reporter_species]
            if others:
                antagonist = ts.series[others[0]]
        return derive_phenotype(
            ts.t, ts.series[reporter_species],
            antagonist=antagonist, value_key=defaults["value_key"],
        )

    # ---- synchronous execute (ExecutionAdapter protocol; SimulatorBase face) --

    def _params_for(self, exp: ExperimentObject) -> dict[str, dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for c in exp.candidates:
            by_id[c.cand_id] = c.params
        for c in exp.controls:
            by_id[c.control_id] = c.params
        return by_id

    def execute(self, exp: ExperimentObject, rng: np.random.Generator) -> ExecutionResult:
        """Run the dry circuit proxy over every layout well, in-process and
        deterministically. ``rng`` is accepted for protocol conformity but UNUSED (a dry
        observation carries no measurement noise; per-candidate stochastic intrinsic noise,
        when requested, is seeded from the candidate params, not this rng). Returns
        ``truth_records=None``: this leg never produces a truth sidecar."""
        if exp.layout is None:
            raise AdapterError(f"{self.name}: ExperimentObject has no layout, cannot execute")
        params_by_id = self._params_for(exp)
        metric = exp.objective.metric
        raws: list[RawResult] = []
        for w in exp.layout.wells:
            entry_id = w.cand_id if w.cand_id is not None else w.control_id
            if entry_id not in params_by_id:
                raise AdapterError(f"{self.name}: layout references unknown entry {entry_id!r}")
            pheno = self.compute(params_by_id[entry_id])
            raws.append(
                RawResult(
                    well_id=w.well_id, cand_id=w.cand_id, control_id=w.control_id,
                    metric=metric, value=pheno.value, unit="",
                    secondary=pheno.secondary(), engine=self.name,
                )
            )
        return ExecutionResult(raw_results=raws, truth_records=None)
