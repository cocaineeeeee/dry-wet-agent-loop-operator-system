"""solvent_screen domain glue (M16 W5): drive the W4 wet leg from domain objects.

This is the ADHESIVE layer that binds the standalone wet stack (``adapters.wet``)
to the expos domain/kernel contract. It is deliberately the wet-side module that
imports the kernel: ``bridge.py`` maps wet *readings* onto the ingestion contract,
and this module maps domain *candidates* onto a wet :class:`ProtocolSpec` and runs
the full WetDriver flow into ``ObservationObject``s tagged with the wet channel.

Two metrics, two channels (M16 G4):
  * dry channel -- metric ``polarity_proxy`` (PySCF dipole magnitude, Debye),
    produced by ``adapters.dry`` directly from candidate params. It carries REAL
    method error (basis/geometry/functional), never an injected artefact: a
    first-principles estimate is honestly biased vs the truth surface. That is
    the honest ``dry`` semantics -- no artefact injection on the dry leg.
  * wet channel -- metric ``solvent_response`` (plate-reader response, a.u.).
    The hidden truth surface (:class:`sim_reader.TruthSurface`) is unimodal in
    solvent POLARITY; artefacts (calibration drift, dropout) are injected
    server-side in the reader, not via the expos artifact-injector framework.

Truth-leakage boundary: a solvent's polarity is PUBLIC design knowledge (choosing
a solvent of known polarity and mixing to realise it is an experimental input).
The hidden truth is how RESPONSE depends on polarity, which lives only in the
reader. Mapping ``solvent -> polarity`` here therefore leaks no truth.
"""

from __future__ import annotations

from expos.adapters.base import AdapterError
from expos.adapters.dry.solvents import SOLVENTS
from expos.adapters.ingest import raw_to_observations
from expos.kernel.objects import (
    ExperimentObject,
    InstrumentMeta,
    LayoutAssignment,
    ObservationObject,
    WellAssignment,
)

from .bridge import to_execution_result
from .driver import WetDriver, WetExecutionResult
from .ot_protocol import OTProtocol, compile_and_validate
from .protocol_spec import (
    ProtocolSpec,
    SolventSample,
    all_wells,
    is_edge,
    well_rowcol,
)

#: Metric names for the two channels of the solvent_screen domain.
DRY_METRIC = "polarity_proxy"
WET_METRIC = "solvent_response"

#: Public normalized solvent polarity (~ET(30) ordering, in [0, 1]). NOT truth:
#: the hidden truth is response(polarity), which lives only in the reader. This
#: is the load-bearing screening dim -- the dry leg estimates it (dipole) and the
#: wet leg realises it by mixing two bracketing stocks.
SOLVENT_POLARITY: dict[str, float] = {
    "water": 1.00,
    "methanol": 0.76,
    "ethanol": 0.65,
    "acetonitrile": 0.46,
    "dmso": 0.44,
    "acetone": 0.36,
    "toluene": 0.10,
    "hexane": 0.01,
}

#: The two screening stocks bracket this target-polarity window; a solvent's raw
#: polarity is linearly mapped into it so every preset is mixable + pipette-feasible
#: (checked by ot_protocol at compile time). The reader truth optimum (mu=0.55)
#: falls inside this window, so the 8 presets bracket a clear unimodal response.
P_TARGET_LO, P_TARGET_HI = 0.30, 0.75


def _validate_polarity_table() -> None:
    """Guard: the polarity table must cover exactly the dry preset solvents, so a
    candidate that the dry leg can compute is one the wet leg can prepare."""
    missing = set(SOLVENTS) - set(SOLVENT_POLARITY)
    extra = set(SOLVENT_POLARITY) - set(SOLVENTS)
    if missing or extra:
        raise AdapterError(
            "SOLVENT_POLARITY must cover exactly the dry preset solvents "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )


_validate_polarity_table()


def target_polarity(solvent: str) -> float:
    """Map a preset solvent's public polarity into the mixable target window.

    Solvent-domain default provider (kept bit-for-bit): thin wrapper over the
    generic :func:`target_coord`, so the M16 behaviour is reproduced exactly while
    the generic path handles any second domain.
    """
    if solvent not in SOLVENT_POLARITY:
        raise AdapterError(
            f"unknown solvent {solvent!r}; presets: {sorted(SOLVENT_POLARITY)}"
        )
    raw = SOLVENT_POLARITY[solvent]
    return P_TARGET_LO + raw * (P_TARGET_HI - P_TARGET_LO)


def target_coord(
    level: str,
    descriptors: dict[str, dict[str, float]],
    coord_name: str = "coord",
    *,
    lo: float = P_TARGET_LO,
    hi: float = P_TARGET_HI,
) -> float:
    """Generic "categorical level -> normalized physical coordinate -> mixable
    target window" map -- the domain-neutral replacement for the hard-coded
    ``SOLVENT_POLARITY`` path (INDEX_M19_DOMAIN2 §5,缺口①).

    ``descriptors`` is the externally-injected ``{level: {coord_name: value}}``
    table (summit ``CategoricalVariable.descriptors`` shape). A missing ``level``
    or a descriptor lacking ``coord_name`` is a LOUD rejection -- never a silent
    fallback (a candidate the map cannot place must not slip through as a default).
    The realised window ``[lo, hi]`` is the same mixable/pipette-feasible band the
    two bracketing stocks span, so any domain's coordinate is prepared identically.
    """
    if level not in descriptors:
        raise AdapterError(
            f"level {level!r} not in descriptors; known: {sorted(descriptors)}"
        )
    coords = descriptors[level]
    if coord_name not in coords:
        raise AdapterError(
            f"descriptor for level {level!r} has no coordinate {coord_name!r}; "
            f"present: {sorted(coords)}"
        )
    return lo + float(coords[coord_name]) * (hi - lo)


#: A single 96-well plate is the wet leg's hard deck capacity (one round = one
#: plate). A replicate layout that would overflow it is rejected loudly rather
#: than silently truncated (letter 085 discipline: capacity is a hard wall).
PLATE_CAPACITY = len(all_wells())


def _replicate_order(
    n_base: int, n_replicates: int, interleave: bool
) -> list[tuple[int, int]]:
    """The MEASUREMENT ORDER of a replicate plate as ``(base_index, replicate)``
    pairs -- the single source of truth shared by the spec expansion (physical
    plate / capture order) and the layout expansion (kernel LayoutAssignment), so
    the two can never disagree on where a replicate lands.

    * ``interleave=False`` (sequential): candidate-major -- a candidate's replicate
      wells are contiguous (``c0,c0,c0, c1,c1,c1, ...``). This CONFOUNDS capture
      order with the arm split (letter 085's failure mode) and is the negative
      control the discriminative suite feeds the aggregator.
    * ``interleave=True`` (balanced): a Latin-square style pass -- replicate ``r``
      is a cyclic rotation of the candidate order by ``r`` (``c0,c1,c2,c3 |
      c1,c2,c3,c0 | c2,c3,c0,c1``). Every candidate (hence every arm) is spread
      evenly across the capture range, so ``corr(capture_index, arm) -> 0``.
      Balanced plate order is the experimental-design cure for the order confound
      (letters 075/085), letting the round aggregator reach a decisive verdict.
    """
    if n_replicates < 1:
        raise AdapterError(f"n_replicates must be >= 1, got {n_replicates}")
    order: list[tuple[int, int]] = []
    if interleave:
        for rep in range(n_replicates):
            for k in range(n_base):
                order.append(((k + rep) % n_base, rep))
    else:
        for base in range(n_base):
            for rep in range(n_replicates):
                order.append((base, rep))
    return order


def _expand_samples(
    base: list[SolventSample], n_replicates: int, interleave: bool
) -> list[SolventSample]:
    """Expand one-per-candidate ``base`` samples into an n-replicate plate in
    measurement order. ``n_replicates == 1`` and ``interleave is False`` returns
    ``base`` UNCHANGED (regression-frozen). Overflowing the 96-well deck is a loud
    rejection."""
    if n_replicates == 1 and not interleave:
        return base
    order = _replicate_order(len(base), n_replicates, interleave)
    if len(order) > PLATE_CAPACITY:
        raise AdapterError(
            f"replicate plate needs {len(order)} wells "
            f"({len(base)} samples x {n_replicates} replicates) but a plate holds "
            f"only {PLATE_CAPACITY} -- reduce candidates or replicates"
        )
    # A replicate index is stamped only when a candidate is genuinely replicated
    # (n_replicates >= 2); a single-well interleave pass keeps the bare sample_id.
    stamp = n_replicates >= 2
    out: list[SolventSample] = []
    for base_idx, rep in order:
        s = base[base_idx]
        out.append(
            SolventSample(
                cand_id=s.cand_id,
                target_polarity=s.target_polarity,
                is_control=s.is_control,
                control_id=s.control_id,
                replicate=rep if stamp else None,
            )
        )
    return out


def protocol_spec_from_experiment(
    exp: ExperimentObject,
    *,
    total_volume_ul: float = 200.0,
    n_replicates: int = 1,
    interleave: bool = False,
    descriptors: dict[str, dict[str, float]] | None = None,
    screen_param: str = "solvent",
    coord_name: str = "coord",
) -> ProtocolSpec:
    """Build a wet :class:`ProtocolSpec` from an experiment's candidates+controls.

    Candidate order is preserved as sample order (so the compiled deck positions
    line up with a layout built from the same protocol). Controls without the
    screening-level param default to the mid target coordinate (calibration
    sentinels).

    Domain-neutral screening dim (M20, INDEX_M19_DOMAIN2 §5): the categorical level
    is read from the ``screen_param`` param and mapped to a mixable target
    coordinate. Two modes, selected by ``descriptors``:

      * ``descriptors is None`` (DEFAULT) -- the solvent_screen path: read the
        ``solvent`` param and map via the built-in ``SOLVENT_POLARITY`` provider.
        BIT-FOR-BIT the M16 behaviour (the hard regression gate: pass no new args
        and nothing about the solvent domain changes).
      * ``descriptors`` given -- the generic path: read the ``screen_param`` param
        (e.g. ``"catalyst"``) and map the level's ``coord_name`` coordinate via
        :func:`target_coord`. A candidate whose level is absent from ``descriptors``
        is REJECTED loudly (never silently skipped). WIRING (session B, once the
        domain schema carries per-variable ``descriptors``): the mcl wet-leg build
        passes ``descriptors=cfg.design_space.var(<cat>).descriptors,
        screen_param=<cat>`` straight through :func:`compile_wet`.

    ``n_replicates``/``interleave`` (M17 K-F multi-replicate substrate) lay each
    candidate + control across ``n_replicates`` wells in a balanced plate order
    when ``interleave`` is set. The defaults (``1``/``False``) reproduce the
    pre-K-F single-well plate BIT-FOR-BIT.
    """
    def _resolve(level: str) -> float:
        if descriptors is None:
            return target_polarity(level)
        return target_coord(level, descriptors, coord_name)

    def _known(level: object) -> bool:
        table = SOLVENT_POLARITY if descriptors is None else descriptors
        return level in table

    samples: list[SolventSample] = []
    for c in exp.candidates:
        level = c.params.get(screen_param)
        if level is None:
            raise AdapterError(
                f"candidate {c.cand_id} has no {screen_param!r} param "
                f"(the screening dim required to realise a target coordinate)"
            )
        samples.append(
            SolventSample(cand_id=c.cand_id, target_polarity=_resolve(level))
        )
    mid = (P_TARGET_LO + P_TARGET_HI) / 2.0
    for ctl in exp.controls:
        level = ctl.params.get(screen_param)
        tp = _resolve(level) if _known(level) else mid
        samples.append(
            SolventSample(
                cand_id=ctl.control_id,
                target_polarity=tp,
                is_control=True,
                control_id=ctl.control_id,
            )
        )
    if not samples:
        raise AdapterError(
            f"exp {exp.exp_id} has no candidates/controls to prepare a wet plate"
        )
    samples = _expand_samples(samples, n_replicates, interleave)
    return ProtocolSpec(samples=samples, total_volume_ul=total_volume_ul)


def layout_from_protocol(
    otp: OTProtocol,
    *,
    seed: int = 0,
    n_replicates: int = 1,
    interleave: bool = False,
) -> LayoutAssignment:
    """Build a kernel :class:`LayoutAssignment` from compiled deck positions, so
    the wet observations ingest against a layout that matches the physical plate.

    Default (``n_replicates=1``, ``interleave=False``) mirrors ``otp.wells``
    one-for-one at their compiled well ids -- BIT-FOR-BIT the pre-K-F behaviour
    (the hard regression gate). This default is also what the mcl loop uses when
    the *protocol itself* was already compiled with replicates (``compile_wet``
    owns the physical measurement order + custody in that path); the layout then
    simply mirrors the already-replicated deck.

    When ``n_replicates >= 2`` (or ``interleave`` is set) this expands a
    ONE-PER-CANDIDATE ``otp`` into an n-replicate plate directly at the layout
    level: each candidate gets ``n_replicates`` wells sharing its ``cand_id`` (so
    they land in the same aggregator arm) placed at distinct real deck coordinates
    in the balanced ``_replicate_order``. A candidate's replicate index rides in
    ``block_id`` (the well id must stay a parseable plate coordinate, so the
    replicate marker cannot be spliced into it). Used to synthesise a
    multi-replicate substrate for a layout without a reader round-trip. Overflowing
    the 96-well deck is a loud rejection.
    """
    if n_replicates == 1 and not interleave:
        wells: list[WellAssignment] = []
        max_row = max_col = 0
        for wp in otp.wells:
            row, col = well_rowcol(wp.well_id)
            max_row, max_col = max(max_row, row), max(max_col, col)
            wells.append(
                WellAssignment(
                    well_id=wp.well_id,
                    row=row,
                    col=col,
                    cand_id=wp.cand_id,
                    control_id=wp.control_id,
                    is_edge=wp.is_edge,
                )
            )
        return LayoutAssignment(
            rows=max_row + 1, cols=max_col + 1, seed=seed, wells=wells
        )

    base = otp.wells
    order = _replicate_order(len(base), n_replicates, interleave)
    if len(order) > PLATE_CAPACITY:
        raise AdapterError(
            f"replicate layout needs {len(order)} wells "
            f"({len(base)} candidates x {n_replicates} replicates) but a plate "
            f"holds only {PLATE_CAPACITY} -- reduce candidates or replicates"
        )
    stamp = n_replicates >= 2
    coords = all_wells()
    wells = []
    max_row = max_col = 0
    for pos, (base_idx, rep) in enumerate(order):
        wp = base[base_idx]
        well_id = coords[pos]
        row, col = well_rowcol(well_id)
        max_row, max_col = max(max_row, row), max(max_col, col)
        group = wp.cand_id or wp.control_id
        wells.append(
            WellAssignment(
                well_id=well_id,
                row=row,
                col=col,
                cand_id=wp.cand_id,
                control_id=wp.control_id,
                is_edge=is_edge(well_id),
                block_id=f"{group}-r{rep}" if stamp else "",
            )
        )
    return LayoutAssignment(rows=max_row + 1, cols=max_col + 1, seed=seed, wells=wells)


def run_wet_leg(
    exp: ExperimentObject,
    otp: OTProtocol,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    holder: str = "solvent-screen-wet",
    timeout_s: float = 2.0,
    max_retries: int = 3,
    calibrate: bool = True,
    wet_metric: str = WET_METRIC,
    wet_unit: str = "",
) -> tuple[list[ObservationObject], WetExecutionResult]:
    """Drive the full wet flow for ``exp`` and return (wet observations, result).

    ``exp.layout`` MUST have been built from ``otp`` (see :func:`layout_from_protocol`)
    so deck positions align. Observations enter the wet channel: ``raw_ref.kind``
    is ``"wet"`` and ``instrument_meta.instrument_id`` names the reader. The
    reader's hidden truth NEVER reaches these observations (fairness red line);
    truth is harvested separately via ``sim_reader.harvest_truth`` (scoring path).

    ``wet_metric`` (M20) is the expected wet objective metric; it defaults to the
    solvent_screen ``solvent_response`` so existing callers are unchanged BIT-FOR-BIT.
    WIRING (session B): the mcl wet-leg call passes ``wet_metric=cfg.objective.metric``
    so a second domain (e.g. ``catalyst_yield``) is accepted without a hard-coded
    solvent constant blocking it.
    """
    if exp.objective.metric != wet_metric:
        raise AdapterError(
            f"wet leg expects objective.metric=={wet_metric!r}, got "
            f"{exp.objective.metric!r}"
        )
    driver = WetDriver(
        host=host,
        port=port,
        exp_id=exp.exp_id,
        round_id=exp.round_id,
        holder=holder,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )
    driver.submit_goal(otp)
    result = driver.run(calibrate=calibrate)

    exec_result = to_execution_result(result, exp, unit=wet_unit)
    observations = raw_to_observations(exp, exec_result.raw_results, raw_kind="wet")
    # Stamp the wet instrument provenance so the channel is self-describing in the
    # store (dry observations carry a pyscf@... instrument id + raw_ref.kind="dry").
    reader_id = f"plate_reader_sim@{host}:{port}"
    for obs in observations:
        obs.instrument_meta = InstrumentMeta(
            instrument_id=reader_id,
            capture_index=obs.instrument_meta.capture_index,
            # Preserve the formal engine provenance carried in from the bridge
            # (letter 060); the reader is the producing engine.
            engine=obs.instrument_meta.engine,
        )
    return observations, result


def compile_wet(
    exp: ExperimentObject,
    *,
    total_volume_ul: float = 200.0,
    n_replicates: int = 1,
    interleave: bool = False,
    descriptors: dict[str, dict[str, float]] | None = None,
    screen_param: str = "solvent",
    coord_name: str = "coord",
) -> OTProtocol:
    """Convenience: candidates+controls -> validated OTProtocol (one call).

    ``n_replicates``/``interleave`` build the M17 K-F multi-replicate substrate at
    the PROTOCOL level: the returned protocol's ``wells`` are the replicate deck in
    balanced measurement order (so the reader's capture sequence balances the arm
    split) and its custody chain issues an independent four-segment record per
    replicate well. This is the mcl-loop path -- pass ``layout_from_protocol(otp)``
    with its defaults afterwards, which mirrors the already-replicated deck. The
    defaults (``1``/``False``) reproduce the pre-K-F single-well protocol exactly.

    ``descriptors``/``screen_param``/``coord_name`` (M20) select the domain-neutral
    screening path -- see :func:`protocol_spec_from_experiment`. Defaults keep the
    solvent_screen behaviour BIT-FOR-BIT.
    """
    spec = protocol_spec_from_experiment(
        exp,
        total_volume_ul=total_volume_ul,
        n_replicates=n_replicates,
        interleave=interleave,
        descriptors=descriptors,
        screen_param=screen_param,
        coord_name=coord_name,
    )
    return compile_and_validate(spec)
