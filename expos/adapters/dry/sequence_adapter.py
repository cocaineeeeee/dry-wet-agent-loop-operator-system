"""SequenceProxyAdapter -- synchronous, deterministic, in-process dry adapter for the
biological ``cell_free_expression_screen`` domain (M24).

The biological analogue of ``PySCFDryAdapter``, but SYNCHRONOUS/in-process: the four
sequence-feature proxies (``sequences.py``) are cheap pure-Python functions, so there
is NO PySCF, NO subprocess, NO sbatch -- it walks the same synchronous ``execute``
contract face the wet ``SimulatorBase`` uses (``ExecutionAdapter`` protocol,
``execute(exp, rng) -> ExecutionResult``). Being a DRY leg it is DETERMINISTIC: it
ignores ``rng`` and adds NO measurement noise (the noise/truth live on the wet side).

Truth-semantics note (identical to ``PySCFDryAdapter``): the sequence proxy is an
OBSERVATION carrying method error, NOT truth. This adapter emits only ``RawResult``
material (value + secondary) and NEVER produces ``truth_records`` -- the wet plate-reader
truth surface owns the hidden expression truth. It does NOT mutate the ExperimentObject.

ComputeResult-shape mapping (``compute.py`` :129 ``value`` + ``secondary``): the
synthesised ``expression_proxy`` rides in as ``RawResult.value`` and the four raw
features + transcript length as ``RawResult.secondary`` -- so the discriminator face /
QC / ledger stay bit-for-bit domain-neutral.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from expos.adapters.base import (
    AdapterError,
    ExecutionResult,
    RawResult,
)
from expos.adapters.dry.sequences import SequenceFeatures, expression_features
from expos.kernel.objects import ExperimentObject

_DEFAULT_METRIC = "expression_proxy"


def sequence_params(
    sequence: str,
    promoter: str | None = None,
    rbs: str | None = None,
    cds: str | None = None,
    construct_id: str | None = None,
) -> dict[str, Any]:
    """Candidate/well ``params`` for a construct, feeding SequenceProxyAdapter via the
    explicit ``sequence`` key (the M20 zero-kernel-change contract: the dry input rides
    in ``candidate.params``, the adapter reads it -- kernel/planner/ledger untouched).

    Returns ``{"sequence": .., "promoter": .., "rbs": .., "cds": .., "construct_id": ..}``
    with ``None`` component keys omitted."""
    params: dict[str, Any] = {"sequence": sequence}
    if promoter is not None:
        params["promoter"] = promoter
    if rbs is not None:
        params["rbs"] = rbs
    if cds is not None:
        params["cds"] = cds
    if construct_id is not None:
        params["construct_id"] = construct_id
    return params


class SequenceProxyAdapter:
    """Synchronous deterministic dry adapter: construct sequence -> expression proxy."""

    name = "sequence_proxy"

    #: Capability declaration -- which ComputeTarget ``input_kind``s this adapter
    #: consumes (docs/M24_CONTRACT_V3.md §"adapter declares consuming capability").
    #: TODO(contract-v3): converge these string literals to an imported ComputeTarget
    #: input-kind constant once session-A's v3 lands; fixed literals per the doc for now.
    ACCEPTS_INPUT_KINDS: tuple[str, ...] = ("sequence_construct", "sequence_features")

    default_metric = _DEFAULT_METRIC

    # ---- pure compute (deterministic; reusable by loop/tests) ---------------

    def compute(self, params: dict[str, Any]) -> SequenceFeatures:
        """The four-proxy compute for one construct's ``params``. Deterministic: the
        same params always yield bit-identical features. Loud-fails on a missing
        ``sequence`` (never a silent empty result)."""
        sequence = params.get("sequence")
        if not sequence or not isinstance(sequence, str):
            raise AdapterError(
                f"{self.name}: params carry no non-empty string `sequence` "
                f"(got {sequence!r}); a construct sequence is the load-bearing dry input"
            )
        return expression_features(
            sequence=sequence,
            promoter=params.get("promoter"),
            rbs=params.get("rbs"),
            cds=params.get("cds"),
        )

    # ---- synchronous execute (ExecutionAdapter protocol; SimulatorBase face) --

    def _params_for(self, exp: ExperimentObject) -> dict[str, dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for c in exp.candidates:
            by_id[c.cand_id] = c.params
        for c in exp.controls:
            by_id[c.control_id] = c.params
        return by_id

    def execute(
        self, exp: ExperimentObject, rng: np.random.Generator
    ) -> ExecutionResult:
        """Run the dry proxy over every layout well, in-process and deterministically.

        ``rng`` is accepted for ExecutionAdapter-protocol conformity but UNUSED (a dry
        observation carries no measurement noise). Returns ``truth_records=None``: this
        leg never produces a truth sidecar. Reads (never mutates) the exp."""
        if exp.layout is None:
            raise AdapterError(f"{self.name}: ExperimentObject has no layout, cannot execute")
        params_by_id = self._params_for(exp)
        metric = exp.objective.metric
        raws: list[RawResult] = []
        for w in exp.layout.wells:
            entry_id = w.cand_id if w.cand_id is not None else w.control_id
            if entry_id not in params_by_id:
                raise AdapterError(f"{self.name}: layout references unknown entry {entry_id!r}")
            feats = self.compute(params_by_id[entry_id])
            raws.append(
                RawResult(
                    well_id=w.well_id,
                    cand_id=w.cand_id,
                    control_id=w.control_id,
                    metric=metric,
                    value=feats.expression_proxy,
                    unit="",
                    secondary=feats.secondary(),
                    engine=self.name,
                )
            )
        return ExecutionResult(raw_results=raws, truth_records=None)
