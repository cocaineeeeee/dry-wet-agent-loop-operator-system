"""M24 bio ruling ③ — the QC-layer technical-replicate collapse (UPSTREAM of the
evidence compiler, domain-agnostic).

The ruling (user, 2026-07-14): a TECHNICAL replicate is the SAME experimental unit
re-measured (repeated pipetting/reading of one sample); a BIOLOGICAL replicate is an
INDEPENDENT unit (separate prep/reaction). If N technical replicates each enter the
e-value aggregation as N independent observations, the information is OVER-estimated
(inflated e-product / false decisive), because correlated re-reads are counted as
independent evidence. The fix lives HERE, in the qc layer, strictly upstream of
``qc.certification_stats.aggregate_round``: technical replicates that share one
biological unit are reduced to ONE observation, so the compiler — which stays
DOMAIN-AGNOSTIC and BYTE-IDENTICAL for chemistry — sees the correct independent-unit
count. Biological replicates are left untouched (they ARE independent evidence).

Domain-neutrality (an M24 cleanliness point + lint EXP001/EXP011): this module hard-
codes NO biology literal (no "construct"/"promoter"/"cds"). It groups by an ABSTRACT
``biological_unit_key`` callable the caller supplies — for the wet loop that key is the
observation's PUBLIC arm key (its ``cand_id``, or ``control_id`` for a control), the
exact join key the aggregator already uses. Technical replicates of one unit share
that key today (the multi-replicate wet substrate gives every replicate the same
``cand_id`` with a distinct ``sample_id`` / ``capture_index``), so grouping by it is
precisely "collapse the re-reads of one unit". The qc layer thus stays domain-agnostic.

Reducer + uncertainty (honest): the collapsed observation's ``value`` is the reducer
(mean or median) of the unit's ``k`` technical reads; its ``uncertainty`` is the
WITHIN-UNIT standard error of that mean, ``s / sqrt(k)`` where ``s`` is the sample
standard deviation of the reads (``k < 2`` => no spread estimable => the single read's
own uncertainty is carried through, unchanged). This ``s/sqrt(k)`` is recorded for
provenance; it is NOT what shrinks the evidence. The information reduction is
STRUCTURAL: because each unit now contributes exactly ONE observation, the compiler's
paired contrast pools ``n = (number of biological units)`` — not ``n = (units ×
technical replicates)`` — so its BETWEEN-UNIT variance sees the right ``n``. That is the
whole point: a mean of k technical replicates has SE ``s/sqrt(k)``, but the collapsed
unit counts as ONE observation to the compiler.

Determinism (gate K5): pure function, no clock / no randomness. Groups are emitted in
sorted-key order; within a group the reads are ordered by ``(capture_index, obs_id)``
before reduction, so the same input collapses bitwise-identically every call.

Layering: this module imports only ``kernel`` objects + stdlib (EXP007 clean; the
planner certification seam may call it, one-directional). It reads no truth surface
(EXP001): it consumes only public observation values and the caller's abstract key.
"""

from __future__ import annotations

import hashlib
import statistics
from collections.abc import Callable, Iterable
from typing import Literal

from expos.kernel.objects import MeasuredResult, ObservationObject

#: The declared replicate-independence kinds (mirrors the domain schema Literal). A
#: ``technical`` domain collapses; ``biological`` / ``None`` do not.
ReplicateKind = Literal["technical", "biological"]
REPLICATE_KINDS: tuple[str, ...] = ("technical", "biological")

#: Group key = an observation's PUBLIC arm identity (control id for a control, else its
#: candidate id) — the same abstract key the evidence compiler joins arms on. Kept as a
#: default here so callers on the wet loop need not re-derive it; entirely domain-neutral
#: (no biology literal). Callers may pass any ``ObservationObject -> str | None`` key.
def public_unit_key(obs: ObservationObject) -> str | None:
    return obs.control_id if obs.is_control else obs.cand_id


#: Named reducers over a unit's technical reads -> the collapsed central value. Extend by
#: passing a callable directly; ``mean`` is the default (its SE is the reported s/sqrt(k)).
_REDUCERS: dict[str, Callable[[list[float]], float]] = {
    "mean": lambda vs: float(statistics.fmean(vs)),
    "median": lambda vs: float(statistics.median(vs)),
}


def _derive_obs_id(unit_key: str) -> str:
    """Deterministic obs_id for a collapsed biological unit (same idiom as the
    aggregation-policy median collapse): same unit key -> same id, reproducibly. The
    ``obsbio_`` prefix marks it a DERIVED biological-unit observation, not a raw well."""
    h = hashlib.sha256(unit_key.encode("utf-8")).hexdigest()[:10]
    return f"obsbio_{h}"


def _within_unit_se(values: list[float], carried: float | None) -> float | None:
    """Within-unit standard error of the collapsed mean: ``s / sqrt(k)`` with ``s`` the
    sample std of the ``k`` reads. ``k < 2`` => no spread estimable => carry the single
    read's own uncertainty through unchanged (honest: one read has no internal spread)."""
    k = len(values)
    if k < 2:
        return carried
    s = float(statistics.stdev(values))
    return s / (k**0.5)


def _mean_secondary(group: list[ObservationObject]) -> dict[str, float]:
    """Per-key mean of the reads' ``secondary`` maps (key union; a key absent from some
    reads is averaged only where present) — carried onto the collapsed observation."""
    keys: set[str] = set()
    for o in group:
        keys.update(o.result.secondary.keys())
    out: dict[str, float] = {}
    for key in sorted(keys):
        vals = [o.result.secondary[key] for o in group if key in o.result.secondary]
        if vals:
            out[key] = float(statistics.fmean(vals))
    return out


def collapse_technical_replicates(
    observations: Iterable[ObservationObject],
    *,
    biological_unit_key: Callable[[ObservationObject], str | None] = public_unit_key,
    reducer: str | Callable[[list[float]], float] = "mean",
) -> list[ObservationObject]:
    """Collapse technical replicates sharing one biological unit into ONE observation.

    Pure and deterministic (K5). For each biological unit (group of observations with an
    equal ``biological_unit_key``): the value-bearing reads are reduced to a single
    observation whose ``value`` is ``reducer`` of the reads and whose ``uncertainty`` is
    the within-unit standard error ``s/sqrt(k)`` (see module docstring). All other fields
    (arm identity ``cand_id``/``control_id``/``is_control``, ``layout_meta``,
    ``instrument_meta`` incl. ``capture_index``, ``trust`` …) are inherited from the
    unit's first read in ``(capture_index, obs_id)`` order, so the collapsed unit keeps a
    real, representative plate position and measurement order; only ``obs_id`` (a derived
    ``obsbio_`` id) and ``result`` are replaced. A unit with NO value-bearing read is
    passed through untouched (its reads are skipped by the compiler anyway).

    ``biological_unit_key`` is an ABSTRACT key callable (default: the public arm key), so
    the qc layer hard-codes no biology semantics. ``reducer`` is ``"mean"`` (default),
    ``"median"``, or any ``list[float] -> float``.

    Returns a new list; input observations are never mutated.
    """
    reduce_fn = _REDUCERS[reducer] if isinstance(reducer, str) else reducer
    if isinstance(reducer, str) and reducer not in _REDUCERS:  # pragma: no cover
        raise KeyError(f"unknown reducer {reducer!r}; use one of {sorted(_REDUCERS)}")

    groups: dict[str, list[ObservationObject]] = {}
    order: list[str] = []
    for obs in observations:
        key = biological_unit_key(obs)
        # A missing key cannot name a biological unit; leave such observations as their
        # own singleton pass-through units (keyed by obs_id so they stay distinct).
        gkey = key if key is not None else f"\x00obs:{obs.obs_id}"
        if gkey not in groups:
            groups[gkey] = []
            order.append(gkey)
        groups[gkey].append(obs)

    out: list[ObservationObject] = []
    for gkey in sorted(order):  # sorted-key output => deterministic ordering
        group = groups[gkey]
        # Stable within-unit order (measurement order, obs_id tiebreak) so both the
        # representative choice and the reduced value are reproducible.
        group_sorted = sorted(
            group, key=lambda o: (o.instrument_meta.capture_index, o.obs_id)
        )
        valued = [o for o in group_sorted if o.result.value is not None]
        if not valued:
            out.extend(group_sorted)  # no value to reduce -> pass the unit through
            continue
        values = [float(o.result.value) for o in valued]
        rep = valued[0]
        central = reduce_fn(values)
        se = _within_unit_se(values, rep.result.uncertainty)
        collapsed = rep.model_copy(
            update={
                "obs_id": _derive_obs_id(str(gkey)),
                "result": MeasuredResult(
                    metric=rep.result.metric,
                    value=central,
                    uncertainty=se,
                    secondary=_mean_secondary(valued),
                    unit=rep.result.unit,
                ),
            }
        )
        out.append(collapsed)
    return out
