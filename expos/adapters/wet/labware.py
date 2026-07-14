"""Externalised labware definitions -- data-driven plate geometry (Q4 of INDEX_M16).

Opentrons paradigm: a plate is a JSON data contract, not hard-coded code. The 96
well ordering and per-well geometry live in ``labware/plate96.json`` (a minimal
``ordering`` + ``wells`` schema, after Opentrons ``labware/schemas/2.json``); this
module loads and *validates* that contract, then hands callers a small read-only
accessor. ``protocol_spec`` sources its well order / edge test / capacity from
here instead of an ``8x12`` assumption baked into code, and ``driver.capabilities``
reports the loaded plate as a machine-readable capability.

A malformed definition (missing keys, an ``ordering`` that disagrees with the
``wells`` map, a bad geometry cell) is rejected LOUDLY with :class:`LabwareError`
-- a silent fall-through to a default plate would let a mis-declared deck reach the
instrument, which the "no silent degradation" red line forbids.

Default behaviour is bit-for-bit unchanged: ``plate96.json`` encodes exactly the
column-major ``A1,B1,..,H1,A2,..`` order the previous hard-coded ``all_wells()``
produced, so nothing downstream shifts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Default plate definition shipped with the wet adapter.
DEFAULT_PLATE_PATH = Path(__file__).resolve().parent / "labware" / "plate96.json"

#: Required top-level keys of the minimal labware contract (subset of the
#: Opentrons v2 schema -- the fields this project actually consumes).
_REQUIRED_KEYS = (
    "schemaVersion",
    "namespace",
    "metadata",
    "parameters",
    "dimensions",
    "ordering",
    "wells",
)
#: Required per-well geometry fields (minimal version: shape + capacity + depth).
_REQUIRED_WELL_KEYS = ("shape", "totalLiquidVolume", "depth", "x", "y", "z")


class LabwareError(Exception):
    """Raised when a labware definition file is missing or malformed."""


@dataclass(frozen=True)
class Labware:
    """A validated, read-only view over an externalised labware definition."""

    load_name: str
    display_name: str
    ordering: tuple[str, ...]  # flattened column-major traversal order
    wells: dict[str, dict[str, Any]]
    well_channels: int
    well_capacity_ul: float
    dimensions: dict[str, float]

    def all_wells(self) -> list[str]:
        """Canonical traversal order (data-driven replacement for ``all_wells``)."""
        return list(self.ordering)

    def rowcol(self, well_id: str) -> tuple[int, int]:
        """('B3') -> (row_index, col_index) using the declared ``ordering`` grid."""
        row = well_id[:1]
        rows = self._row_labels()
        if row not in rows:
            raise ValueError(f"well_id {well_id!r}: row {row!r} not in {rows}")
        try:
            col = int(well_id[1:])
        except ValueError as exc:
            raise ValueError(
                f"well_id {well_id!r}: column not an integer"
            ) from exc
        n_cols = len(self._columns())
        if not (1 <= col <= n_cols):
            raise ValueError(f"well_id {well_id!r}: column {col} not in 1-{n_cols}")
        return rows.index(row), col - 1

    def is_edge(self, well_id: str) -> bool:
        """True iff the well sits on the plate perimeter (first/last row or col)."""
        r, c = self.rowcol(well_id)
        n_rows = len(self._row_labels())
        n_cols = len(self._columns())
        return r in (0, n_rows - 1) or c in (0, n_cols - 1)

    def capacity_of(self, well_id: str) -> float:
        """Per-well total liquid volume from the geometry (uL)."""
        if well_id not in self.wells:
            raise ValueError(f"well_id {well_id!r} not in labware {self.load_name!r}")
        return float(self.wells[well_id]["totalLiquidVolume"])

    def capabilities(self) -> dict[str, Any]:
        """Machine-readable capability slice for the driver contract."""
        return {
            "load_name": self.load_name,
            "display_name": self.display_name,
            "well_count": len(self.ordering),
            "well_channels": self.well_channels,
            "well_capacity_ul": self.well_capacity_ul,
            "well_total_liquid_ul": (
                self.capacity_of(self.ordering[0]) if self.ordering else 0.0
            ),
            "dimensions_mm": dict(self.dimensions),
        }

    # -- internal grid helpers ------------------------------------------------

    def _columns(self) -> list[list[str]]:
        # reconstruct columns from the flattened ordering + declared row count
        rows = self._row_labels()
        n = len(rows)
        return [list(self.ordering[i : i + n]) for i in range(0, len(self.ordering), n)]

    def _row_labels(self) -> str:
        # rows are the leading letters of the first column of the ordering grid
        return "".join(w[:1] for w in self.ordering[: self._rows_per_col()])

    def _rows_per_col(self) -> int:
        # count the run of wells sharing column "1" == rows per column
        count = 0
        for w in self.ordering:
            if w[1:] == "1":
                count += 1
            elif count:
                break
        return count or len(self.ordering)


def _validate(doc: dict[str, Any], source: str) -> None:
    """Reject a malformed labware document loudly."""
    for key in _REQUIRED_KEYS:
        if key not in doc:
            raise LabwareError(f"{source}: missing required key {key!r}")
    ordering = doc["ordering"]
    if not isinstance(ordering, list) or not ordering:
        raise LabwareError(f"{source}: 'ordering' must be a non-empty list of columns")
    flat: list[str] = []
    col_len = None
    for col in ordering:
        if not isinstance(col, list) or not col:
            raise LabwareError(f"{source}: each 'ordering' column must be a non-empty list")
        if col_len is None:
            col_len = len(col)
        elif len(col) != col_len:
            raise LabwareError(
                f"{source}: ragged 'ordering' -- columns must be equal length "
                f"(got {len(col)} vs {col_len})"
            )
        flat.extend(col)
    wells = doc["wells"]
    if not isinstance(wells, dict):
        raise LabwareError(f"{source}: 'wells' must be an object keyed by well id")
    # every ordered well must have geometry, and vice versa (no orphan cells)
    ordered = set(flat)
    declared = set(wells)
    if ordered != declared:
        missing = sorted(ordered - declared)
        extra = sorted(declared - ordered)
        raise LabwareError(
            f"{source}: 'ordering' and 'wells' disagree "
            f"(ordered-but-undefined={missing}, defined-but-unordered={extra})"
        )
    if len(flat) != len(ordered):
        raise LabwareError(f"{source}: 'ordering' contains duplicate well ids")
    for wid, geom in wells.items():
        if not isinstance(geom, dict):
            raise LabwareError(f"{source}: well {wid!r} geometry must be an object")
        for gk in _REQUIRED_WELL_KEYS:
            if gk not in geom:
                raise LabwareError(
                    f"{source}: well {wid!r} missing geometry field {gk!r}"
                )
    params = doc["parameters"]
    if "loadName" not in params:
        raise LabwareError(f"{source}: parameters.loadName is required")


@lru_cache(maxsize=8)
def load_labware(path: str | Path = DEFAULT_PLATE_PATH) -> Labware:
    """Load + validate a labware definition file into a :class:`Labware`.

    Cached by path: the default plate is parsed once per process. Raises
    :class:`LabwareError` if the file is missing, not JSON, or fails validation.
    """
    p = Path(path)
    if not p.exists():
        raise LabwareError(f"labware definition not found: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LabwareError(f"{p}: not valid JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise LabwareError(f"{p}: top-level labware document must be an object")
    _validate(doc, str(p))
    params = doc["parameters"]
    ordering = tuple(w for col in doc["ordering"] for w in col)
    return Labware(
        load_name=str(params["loadName"]),
        display_name=str(doc["metadata"].get("displayName", params["loadName"])),
        ordering=ordering,
        wells=dict(doc["wells"]),
        well_channels=int(params.get("wellChannels", 1)),
        well_capacity_ul=float(params.get("wellCapacityUl", 0.0)),
        dimensions={k: float(v) for k, v in doc["dimensions"].items()},
    )


def load_labware_doc(doc: dict[str, Any], *, source: str = "<in-memory>") -> Labware:
    """Validate an already-parsed labware document (used by tests for bad defs)."""
    if not isinstance(doc, dict):
        raise LabwareError(f"{source}: top-level labware document must be an object")
    _validate(doc, source)
    params = doc["parameters"]
    ordering = tuple(w for col in doc["ordering"] for w in col)
    return Labware(
        load_name=str(params["loadName"]),
        display_name=str(doc["metadata"].get("displayName", params["loadName"])),
        ordering=ordering,
        wells=dict(doc["wells"]),
        well_channels=int(params.get("wellChannels", 1)),
        well_capacity_ul=float(params.get("wellCapacityUl", 0.0)),
        dimensions={k: float(v) for k, v in doc["dimensions"].items()},
    )
