"""M29 ``protocol -> ExperimentObject`` compiler + MEASURE -> fluorescence binding (v0.1).

This is the seam the integration owner (B) asked for: a compiler that turns a typed,
device-neutral cell-free :class:`~protocols.objects.Protocol` into a kernel
:class:`~expos.kernel.objects.ExperimentObject` (candidates / controls / layout /
objective observable), so B's mcl can orchestrate an authored protocol AS ONE ROUND of the
adaptive loop. Its inverse partner already exists domain-locally (the executor runs the
protocol); this closes the *kernel-object* side.

Two public entry points:

  * :func:`compile_experiment` -- ``Protocol`` -> ``ExperimentObject``. Each reaction well
    (a well that received a DNA-template transfer) becomes a :class:`Candidate`; each
    control well (a no-template / buffer well) becomes a negative :class:`Control`; the
    plate positions become the :class:`LayoutAssignment`; the objective observable is
    ``expression_fluorescence`` (the wet reporter channel the ``ReadPlate`` step reads).
    The device-IR fingerprint of the lowered protocol is stamped into
    ``DesignProvenance.protocol_fingerprint`` (so a lowering change flips run identity).
  * :func:`bind_measurements` -- the ``MEASURE`` / ``ReadPlate`` observation binding. For a
    MEASURE unit that reached **COMMITTED** through the M23 sensed-state gate, every read
    well yields one :class:`ObservationObject` on the ``expression_fluorescence`` metric
    (per-well fake plate-reader read-back). A MEASURE unit that did NOT commit yields NO
    observation -- **COMMITTED is the observation-eligibility gate**.

TRUST BOUNDARY (honest, matches ``expos.adapters.ingest``): every observation is emitted
``trust=PENDING``. This layer NEVER self-certifies trust -- adjudication (PENDING ->
TRUSTED) is the kernel QC / claim lifecycle's job (B's seam #3). The COMMITTED gate here
decides *whether an observation exists at all*, not what its trust is; only a committed,
sensed read-back enters the trusted-observation lifecycle downstream.

HONESTY (BIOLOGY_PROGRAM_2026 §5 / §6.1): SAFE cell-free protein-expression only; this is
**protocol-to-simulated-physical** against a fake backend. The compile + commit-gate
machinery is real; the physics (the readings) is FAKED. NOT a physical autonomous
laboratory; real hardware / real wet-lab validation is pending.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from protocols.objects import Incubate, Protocol, ReadPlate, Transfer, cell_free_expression_protocol
from device_ir.ir import (
    UNIT_MEASURE,
    group_units,
    ir_fingerprint,
    lower,
)
from expos.adapters.wet.labware import Labware, load_labware
from expos.kernel.objects import (
    Budget,
    Candidate,
    Control,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutAssignment,
    MeasuredResult,
    Objective,
    ObservationObject,
    RawDataRef,
    VariableDef,
    WellAssignment,
    LayoutMeta,
)

#: The objective observable the wet reporter channel binds to (the cell-free expression
#: fluorescence metric; matches ``domains/cell_free_expression_screen.yaml``).
EXPRESSION_FLUORESCENCE = "expression_fluorescence"

#: Reagent-label markers that classify a well as a no-template NEGATIVE control (not a
#: reaction / candidate well). A control well received one of these instead of DNA template.
_CONTROL_REAGENT_MARKERS = ("no_template", "buffer", "no_dna")

#: Reagent-label marker for the reporter DNA template (a well that received this is a
#: reaction / candidate well).
_TEMPLATE_MARKER = "dna"


def default_objective() -> Objective:
    """The default cell-free expression objective (maximize reporter fluorescence)."""
    return Objective(
        name=EXPRESSION_FLUORESCENCE,
        metric=EXPRESSION_FLUORESCENCE,
        direction="maximize",
        description=(
            "Wet plate-reader reporter fluorescence (a.u.), bound from the protocol's "
            "ReadPlate/MEASURE step. SIMULATION: fake plate reader, physics faked."
        ),
    )


# --------------------------------------------------------------- well classification


def _is_control_reagent(reagent: str) -> bool:
    r = reagent.lower()
    return any(m in r for m in _CONTROL_REAGENT_MARKERS)


def _is_template_reagent(reagent: str) -> bool:
    return _TEMPLATE_MARKER in reagent.lower()


def classify_wells(
    protocol: Protocol,
    control_wells: Iterable[str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a protocol's read wells into ``(reaction_wells, control_wells)`` in read order.

    A well is a CONTROL if the caller names it in ``control_wells`` OR it received a
    no-template / buffer transfer and no DNA-template transfer; every other read well is a
    reaction (candidate) well. Read order is the ``ReadPlate`` well order (falling back to
    first-seen transfer-destination order), so the classification is deterministic.
    """
    transfers_by_well: dict[str, list[Transfer]] = {}
    for t in protocol.transfers():
        transfers_by_well.setdefault(t.destination, []).append(t)

    # read wells in the ReadPlate order (the observed set); fall back to transfer order.
    read_order: list[str] = []
    for step in protocol.steps:
        if isinstance(step, ReadPlate):
            for w in step.wells:
                if w not in read_order:
                    read_order.append(w)
    if not read_order:
        read_order = list(protocol.destinations())

    named_controls = set(control_wells) if control_wells is not None else None
    reaction: list[str] = []
    controls: list[str] = []
    for w in read_order:
        ts = transfers_by_well.get(w, [])
        if named_controls is not None:
            is_control = w in named_controls
        else:
            has_template = any(_is_template_reagent(t.reagent) for t in ts)
            has_control = any(_is_control_reagent(t.reagent) for t in ts)
            is_control = has_control and not has_template
        (controls if is_control else reaction).append(w)
    return tuple(reaction), tuple(controls)


def _well_conditions(protocol: Protocol) -> dict[str, float]:
    """Plate-wide incubation conditions actuated by the protocol (recorded per well)."""
    cond: dict[str, float] = {}
    for step in protocol.steps:
        if isinstance(step, Incubate):
            cond["incubation_time"] = float(step.minutes)
            cond["temperature"] = float(step.temperature_c)
            break
    return cond


def _well_reagent_volumes(protocol: Protocol, well: str) -> dict[str, float]:
    """Total volume dispensed into ``well`` per reagent label (provenance for a candidate)."""
    vols: dict[str, float] = {}
    for t in protocol.transfers():
        if t.destination == well:
            label = t.reagent or "unlabeled"
            vols[label] = vols.get(label, 0.0) + float(t.volume_ul)
    return vols


def _grid(labware: Labware) -> tuple[int, int]:
    """(n_rows, n_cols) of the labware grid, derived from its well ordering."""
    n_rows = n_cols = 0
    for w in labware.all_wells():
        r, c = labware.rowcol(w)
        n_rows = max(n_rows, r + 1)
        n_cols = max(n_cols, c + 1)
    return n_rows, n_cols


# --------------------------------------------------------------- the compiler


def compile_experiment(
    protocol: Protocol,
    *,
    round_id: int = 0,
    domain: str = "cell_free_expression_screen",
    objective: Objective | None = None,
    design_space: DesignSpace | None = None,
    well_designs: Mapping[str, Mapping[str, Any]] | None = None,
    control_wells: Iterable[str] | None = None,
    budget: Budget | None = None,
    rounds_total: int = 2,
    adapter: str = "physical_protocol",
    labware: Labware | None = None,
    provenance: DesignProvenance | None = None,
) -> ExperimentObject:
    """Compile a typed cell-free :class:`Protocol` into a kernel :class:`ExperimentObject`.

    Reaction wells -> :class:`Candidate` (params = protocol conditions + reagent volumes,
    optionally merged with a caller-supplied ``well_designs[well]`` map for a real construct
    screen); control wells -> negative :class:`Control`; plate positions ->
    :class:`LayoutAssignment`; objective observable -> ``expression_fluorescence``. The
    lowered device-IR fingerprint is stamped into ``provenance.protocol_fingerprint``.

    ``well_designs`` lets B's mcl bind richer per-well design points (e.g. a construct id
    and its coordinate) onto the candidates without this compiler inventing a design; when
    absent, candidates carry the protocol's own conditions/volumes (an honest position/
    replicate screen). Deterministic: same protocol + same arguments -> same object shape.
    """
    lw = labware or load_labware()
    objective = objective or default_objective()
    reaction_wells, ctrl_wells = classify_wells(protocol, control_wells)
    conditions = _well_conditions(protocol)
    ir_fp = ir_fingerprint(lower(protocol))

    # -- candidates (reaction wells) --------------------------------------------------
    candidates: list[Candidate] = []
    well_to_cand: dict[str, str] = {}
    for well in reaction_wells:
        params: dict[str, Any] = {"well": well, "reagent_volumes_ul": _well_reagent_volumes(protocol, well)}
        params.update(conditions)
        if well_designs and well in well_designs:
            params.update(dict(well_designs[well]))
        cand = Candidate(
            params=params,
            source="protocol_compiler",
            rationale=f"reaction well {well} of protocol {protocol.protocol_id}",
        )
        candidates.append(cand)
        well_to_cand[well] = cand.cand_id

    # -- controls (no-template wells -> negative) -------------------------------------
    controls: list[Control] = []
    well_to_ctrl: dict[str, str] = {}
    for well in ctrl_wells:
        cparams: dict[str, Any] = {"well": well, "reagent_volumes_ul": _well_reagent_volumes(protocol, well)}
        cparams.update(conditions)
        if well_designs and well in well_designs:
            cparams.update(dict(well_designs[well]))
        ctrl = Control(kind="negative", params=cparams)
        controls.append(ctrl)
        well_to_ctrl[well] = ctrl.control_id

    # -- layout (plate positions of every observed well) ------------------------------
    n_rows, n_cols = _grid(lw)
    wells: list[WellAssignment] = []
    for well in reaction_wells:
        r, c = lw.rowcol(well)
        wells.append(WellAssignment(well_id=well, row=r, col=c, cand_id=well_to_cand[well],
                                    is_edge=lw.is_edge(well)))
    for well in ctrl_wells:
        r, c = lw.rowcol(well)
        wells.append(WellAssignment(well_id=well, row=r, col=c, control_id=well_to_ctrl[well],
                                    is_edge=lw.is_edge(well)))
    layout = LayoutAssignment(rows=n_rows, cols=n_cols, seed=0, wells=wells)

    # -- design space (honest: the CONDITION axes the protocol actuates) --------------
    if design_space is None:
        variables: list[VariableDef] = []
        if "incubation_time" in conditions:
            variables.append(VariableDef(name="incubation_time", kind="continuous",
                                         low=30.0, high=240.0, unit="min"))
            variables.append(VariableDef(name="temperature", kind="continuous",
                                         low=25.0, high=37.0, unit="C"))
        else:  # no incubate step: fall back to a categorical position axis (always valid)
            variables.append(VariableDef(name="well", kind="categorical",
                                         choices=list(reaction_wells) or ["A1"]))
        design_space = DesignSpace(name=domain, variables=variables)

    # -- provenance (protocol-fingerprint anchor realised locally) --------------------
    if provenance is None:
        provenance = DesignProvenance(
            generator="protocols.experiment.compile_experiment",
            rationale=(f"compiled from typed cell-free protocol {protocol.protocol_id} "
                       f"({len(reaction_wells)} reaction + {len(ctrl_wells)} control wells)"),
            protocol_fingerprint=ir_fp,
        )
    elif provenance.protocol_fingerprint is None:
        provenance = provenance.model_copy(update={"protocol_fingerprint": ir_fp})

    if budget is None:
        budget = Budget(wells_total=len(wells), rounds_total=rounds_total)

    return ExperimentObject(
        round_id=round_id,
        domain=domain,
        objective=objective,
        design_space=design_space,
        active_vars=[v.name for v in design_space.variables],
        candidates=candidates,
        controls=controls,
        layout=layout,
        budget=budget,
        execution_req=ExecutionReq(
            adapter=adapter,
            params={"protocol_id": protocol.protocol_id, "ir_fingerprint": ir_fp},
        ),
        provenance=provenance,
    )


# --------------------------------------------------------------- MEASURE -> observation


def bind_measurements(
    exp: ExperimentObject,
    protocol: Protocol,
    run_log,
    reader,
) -> list[ObservationObject]:
    """Bind COMMITTED ``MEASURE`` read-backs to ``expression_fluorescence`` observations.

    For every MEASURE unit that reached **COMMITTED** through the M23 sensed-state gate (per
    ``run_log``), each read well that maps to a layout candidate/control well yields one
    :class:`ObservationObject` on ``exp.objective.metric`` (``expression_fluorescence``),
    valued by ``reader.read_well``. A MEASURE unit that did NOT commit yields NO observation
    -- COMMITTED is the observation-eligibility gate.

    Observations are emitted ``trust=PENDING`` (this layer never self-certifies trust; the
    kernel QC / claim lifecycle adjudicates PENDING -> TRUSTED downstream -- B's seam #3).
    """
    if exp.layout is None:
        raise ValueError(f"exp {exp.exp_id} has no layout; compile_experiment must run first")
    by_well = {w.well_id: w for w in exp.layout.wells}
    metric = exp.objective.metric

    committed_measure_ids = {
        ur.unit_id for ur in run_log.units
        if ur.kind == UNIT_MEASURE and ur.committed
    }
    if not committed_measure_ids:
        return []

    observations: list[ObservationObject] = []
    for unit in group_units(lower(protocol)):
        if unit.kind != UNIT_MEASURE or unit.unit_id not in committed_measure_ids:
            continue
        op = unit.ops[-1]
        for well in op.params.get("wells", []) or []:
            wa = by_well.get(well)
            if wa is None:
                # a read well that is neither a candidate nor a control in the compiled
                # layout (e.g. a bare sentinel read); skip rather than fabricate a subject.
                continue
            reading = reader.read_well(op, well)
            observations.append(ObservationObject(
                exp_id=exp.exp_id,
                round_id=exp.round_id,
                cand_id=wa.cand_id,
                control_id=wa.control_id,
                is_control=wa.control_id is not None,
                result=MeasuredResult(metric=metric, value=reading, unit="arbitrary_unit"),
                raw_ref=RawDataRef(uri="", kind="sim"),
                layout_meta=LayoutMeta(well_id=wa.well_id, row=wa.row, col=wa.col,
                                       is_edge=wa.is_edge, block_id=wa.block_id),
                # trust=PENDING (default): commit-gated eligibility only; kernel QC certifies.
                qc=None, failure_attr=None, routing=None, next_action=None,
            ))
    return observations


# --------------------------------------------------------------- high-level round


@dataclass
class RoundResult:
    """One compiled + executed protocol round: the kernel object, the run log, and the
    bound (commit-gated) observations."""

    experiment: ExperimentObject
    run_log: Any
    observations: list[ObservationObject]
    ir_fingerprint: str
    protocol: Protocol = field(default=None)  # type: ignore[assignment]


def run_protocol_round(
    run_dir: str,
    protocol: Protocol | None = None,
    *,
    round_id: int = 0,
    control_wells: Iterable[str] | None = None,
    policy=None,
    well_designs: Mapping[str, Mapping[str, Any]] | None = None,
) -> RoundResult:
    """Domain-local whole-round e2e: compile the protocol to an ``ExperimentObject``, execute
    it transactionally on the fake backend, and bind the committed MEASURE read-backs to
    ``expression_fluorescence`` observations.

    This is the single entry B's mcl can call to orchestrate an authored protocol as one
    round (or B can call :func:`compile_experiment` / :func:`bind_measurements` at its own
    granularity). SIMULATION only -- fake backend, physics faked, real hardware pending.
    """
    # imported here to keep the compiler import-light for callers that only need the schema.
    from protocols.constraints import check_protocol
    from expos.adapters.physical import FakeLiquidHandler, FakePlateReader, ProtocolExecutor

    protocol = protocol or cell_free_expression_protocol()
    reservoir_names = {t.source for t in protocol.transfers()}
    check_protocol(protocol, reservoirs=frozenset(reservoir_names)).raise_if_bad()

    ops = lower(protocol)
    units = group_units(ops)
    ir_fp = ir_fingerprint(ops)

    exp = compile_experiment(protocol, round_id=round_id, control_wells=control_wells,
                             well_designs=well_designs)

    reader = FakePlateReader()
    executor = ProtocolExecutor(
        run_dir, [FakeLiquidHandler(), reader],
        protocol_id=protocol.protocol_id, round_id=round_id, spec_fingerprint=ir_fp,
        policy=policy, reservoirs={name: 1000.0 for name in reservoir_names},
    )
    run_log = executor.run(units)
    observations = bind_measurements(exp, protocol, run_log, reader)
    return RoundResult(experiment=exp, run_log=run_log, observations=observations,
                       ir_fingerprint=ir_fp, protocol=protocol)
