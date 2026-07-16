"""Deterministic, auditable dry-leg mutation operators for the M25 generative-
construct domain (Team M25 "Design").

v0.1 COMPLETE (breadth-first Biology Program pass, 2026-07-16; supersedes the
promoter-swap-only skeleton of 2026-07-14). This is the design-move adapter: it
takes a *parent* construct (the ``{sequence, promoter, rbs, cds}`` component dict
used everywhere else in the biology dry leg) and applies a **pure, deterministic**
design edit to yield a *child* construct.

FIVE auditable operators, one uniform contract
----------------------------------------------
Every operator is a pure function ``apply(parent, op_params, seed) -> (child,
EditProvenance)`` -- same ``(parent, params, seed)`` yields a byte-identical child
and provenance. NO stochastic sampler and NO large generative model is involved
(the ESM/ProtGPT/diffusion upgrade is an explicit later seam, ``sequences.
expression_features``). A v0.1 "mutation" is a catalogue-constrained or
synonymous edit over PUBLIC design elements -- exactly the honest-biased-proxy
regime the rest of the dry leg lives in.

  1. ``promoter_swap``   -- swap the promoter for a catalogue Anderson promoter.
  2. ``rbs_swap``        -- swap the 5'UTR/RBS for a catalogue RBS ladder element.
  3. ``codon_optimize``  -- re-encode the CDS toward optimal (or rare) synonymous
                            codons; SAME peptide (translation-invariant, auditable).
  4. ``utr5_mutation``   -- a single point mutation in the 5'UTR/RBS region.
  5. ``cds_synonymous``  -- a single synonymous codon substitution in the CDS
                            (SAME amino acid; translation-invariant).

INVARIANTS (machine-checked in tests/test_m25_generative_v01.py):
  * ``sequence == promoter + rbs + cds`` for every child (feeds the UNCHANGED
    ``SequenceProxyAdapter`` / ``expression_features`` -- zero-adapter-change).
  * CDS-touching operators (codon_optimize, cds_synonymous) are TRANSLATION-
    INVARIANT: the child CDS encodes the exact same amino-acid peptide (a
    synonymous, dual-use-safe edit -- no new protein is designed).
  * Determinism: same ``(parent, params, seed)`` -> byte-identical child + id.
  * ``child_id`` is content-addressed over ``(parent_id, operator, resolved
    params, seed)``; a child NEVER reuses the parent's id/observation identity
    (design lineage is over DESIGNS, never over observations -- M24-B red line).

Reuses (never re-implements): ``constructs.CONSTRUCTS`` for the public promoter/RBS
catalogue and ``sequences.expression_features`` / ``sequences.ECOLI_W`` for the
design-coordinate proxy and codon preference. No truth here (dry proxy only).
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Callable

from expos.adapters.dry.constructs import CONSTRUCTS, components_for
from expos.adapters.dry.sequences import ECOLI_W, expression_features

# ---------------------------------------------------------------------------
# Public part catalogues -- derived deterministically from the M24 presets.
# Each preset contributes its promoter and its RBS element; these are the
# (public, design-knowledge) part libraries the swap operators draw from. NO
# truth: a part's presence in the library is design knowledge, not a reading.
# ---------------------------------------------------------------------------

PROMOTER_LIBRARY: dict[str, str] = {
    cid: comp["promoter"] for cid, comp in CONSTRUCTS.items()
}

RBS_LIBRARY: dict[str, str] = {cid: comp["rbs"] for cid, comp in CONSTRUCTS.items()}

_BASES = ("A", "C", "G", "T")

# ---------------------------------------------------------------------------
# Standard genetic code (DNA alphabet) -- domain-local, for the synonymous /
# codon operators. Enables translation-invariance checks and synonymous-codon
# enumeration. SOURCE: the standard (NCBI transl_table=1) genetic code, a public
# constant. Stops -> "*". This is NOT truth; it is the codon<->amino-acid map.
# ---------------------------------------------------------------------------

CODON_TABLE: dict[str, str] = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


def _build_synonyms() -> dict[str, tuple[str, ...]]:
    """amino_acid -> synonymous codons, ranked by E. coli relative adaptiveness
    (``ECOLI_W``) DESCENDING (most-preferred first). Codons absent from ECOLI_W
    (single-codon Met/Trp) get an implicit w of 1.0. Deterministic tie-break on
    the codon string so the ranking is stable."""
    by_aa: dict[str, list[str]] = {}
    for codon, aa in CODON_TABLE.items():
        by_aa.setdefault(aa, []).append(codon)
    ranked: dict[str, tuple[str, ...]] = {}
    for aa, codons in by_aa.items():
        ranked[aa] = tuple(
            sorted(codons, key=lambda c: (-ECOLI_W.get(c, 1.0), c))
        )
    return ranked


#: amino_acid -> synonymous codons ranked most-preferred (E. coli) first.
SYNONYMS: dict[str, tuple[str, ...]] = _build_synonyms()


def translate(cds: str) -> str:
    """Translate a coding sequence to its amino-acid peptide (stops -> "*").

    The translation-invariance witness for the synonymous operators: a codon
    edit is only auditable-safe if ``translate(child_cds) == translate(parent_
    cds)``. Trailing partial codon (len % 3) is ignored (mirrors ``cai``)."""
    s = cds.upper().replace("U", "T")
    return "".join(
        CODON_TABLE.get(s[i : i + 3], "X") for i in range(0, len(s) - len(s) % 3, 3)
    )


# ---------------------------------------------------------------------------
# Provenance (PROV-shaped) for one deterministic design edit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditProvenance:
    """PROV-shaped provenance for one deterministic design edit (an "activity").

    Shaped on the W3C PROV derivation: the edit ``operator`` (activity) USED the
    ``parent_id`` entity and GENERATED the ``child_id`` entity under ``params`` +
    ``seed``. ``operator_fingerprint`` is the sha256 of the operator's source, so
    a change to the operator flips design identity (the config-fingerprint seam
    for the integration owner). It carries NO observation id -- a design lineage
    is over DESIGNS, never over observations (M24-B technical-replicate red line).
    """

    operator: str
    params: dict[str, object]
    seed: int
    parent_id: str
    child_id: str
    detail: str
    operator_fingerprint: str

    def as_activity(self) -> dict[str, object]:
        """PROV 'activity' record: what generated the child design (audit shape)."""
        return {
            "prov_type": "activity",
            "operator": self.operator,
            "params": {k: self.params[k] for k in sorted(self.params)},
            "seed": self.seed,
            "used_entity": self.parent_id,       # PROV: parent design used
            "generated_entity": self.child_id,   # PROV: child design generated
            "operator_fingerprint": self.operator_fingerprint,
            "detail": self.detail,
        }


class MutationError(ValueError):
    """A design edit that cannot be applied (unknown part, non-synonymous CDS
    edit, out-of-range position). Fails LOUDLY -- a mis-specified operator must
    never silently emit the parent unchanged."""


# Backwards-compatible alias for the old skeleton's container name. The child is
# now returned as (components, EditProvenance); MutatedConstruct wraps both for
# any caller that wants a single object.
@dataclass(frozen=True)
class MutatedConstruct:
    child_id: str
    parent_id: str
    operator: str
    detail: str
    components: dict[str, str]
    provenance: EditProvenance | None = None


# ---------------------------------------------------------------------------
# Internal edit primitives -- each returns (child_components, resolved_params,
# detail). Pure; no id/fingerprint concern (the wrapper adds those).
# ---------------------------------------------------------------------------


def _rebuild(promoter: str, rbs: str, cds: str) -> dict[str, str]:
    """Assemble a child component dict obeying ``sequence == promoter+rbs+cds``."""
    return {
        "promoter": promoter,
        "rbs": rbs,
        "cds": cds,
        "sequence": promoter + rbs + cds,
    }


def _edit_promoter_swap(
    parent: dict[str, str], op_params: dict[str, object], seed: int
) -> tuple[dict[str, str], dict[str, object], str]:
    """Swap the promoter for the catalogue promoter ``new_promoter_id``.

    If ``new_promoter_id`` is absent, deterministically pick from the catalogue by
    ``seed`` (excluding a promoter already equal to the parent's)."""
    new_id = op_params.get("new_promoter_id")
    if new_id is None:
        choices = [
            cid for cid, seq in PROMOTER_LIBRARY.items() if seq != parent["promoter"]
        ]
        new_id = sorted(choices)[seed % len(choices)]
    if new_id not in PROMOTER_LIBRARY:
        raise MutationError(
            f"unknown promoter {new_id!r}; catalogue: {sorted(PROMOTER_LIBRARY)}"
        )
    child = _rebuild(PROMOTER_LIBRARY[new_id], parent["rbs"], parent["cds"])
    return child, {"new_promoter_id": new_id}, f"promoter<-{new_id}"


def _edit_rbs_swap(
    parent: dict[str, str], op_params: dict[str, object], seed: int
) -> tuple[dict[str, str], dict[str, object], str]:
    """Swap the 5'UTR/RBS for the catalogue RBS element ``new_rbs_id``."""
    new_id = op_params.get("new_rbs_id")
    if new_id is None:
        choices = [
            cid for cid, seq in RBS_LIBRARY.items() if seq != parent["rbs"]
        ]
        new_id = sorted(choices)[seed % len(choices)]
    if new_id not in RBS_LIBRARY:
        raise MutationError(
            f"unknown rbs {new_id!r}; catalogue: {sorted(RBS_LIBRARY)}"
        )
    child = _rebuild(parent["promoter"], RBS_LIBRARY[new_id], parent["cds"])
    return child, {"new_rbs_id": new_id}, f"rbs<-{new_id}"


def _edit_codon_optimize(
    parent: dict[str, str], op_params: dict[str, object], seed: int
) -> tuple[dict[str, str], dict[str, object], str]:
    """Re-encode the CDS toward ``target`` synonymous codons (SAME peptide).

    ``target`` = "optimal" (highest E. coli w) or "rare" (lowest). ``positions``
    (optional list of codon indices) restricts the edit; absent -> all positions
    after the fixed start codon (index 0). Translation-invariant by construction.
    """
    target = str(op_params.get("target", "optimal"))
    if target not in ("optimal", "rare"):
        raise MutationError(f"codon_optimize target must be optimal|rare, got {target!r}")
    cds = parent["cds"].upper().replace("U", "T")
    n_codons = len(cds) // 3
    positions = op_params.get("positions")
    if positions is None:
        positions = list(range(1, n_codons))  # keep index-0 start codon fixed
    positions = [p for p in positions if 0 < p < n_codons]
    codons = [cds[i * 3 : i * 3 + 3] for i in range(n_codons)]
    for p in positions:
        aa = CODON_TABLE.get(codons[p])
        if aa is None or aa == "*":
            continue
        ranked = SYNONYMS[aa]
        codons[p] = ranked[0] if target == "optimal" else ranked[-1]
    child_cds = "".join(codons)
    child = _rebuild(parent["promoter"], parent["rbs"], child_cds)
    return (
        child,
        {"target": target, "positions": sorted(positions)},
        f"codon_optimize:{target}:{len(positions)}pos",
    )


def _edit_utr5_mutation(
    parent: dict[str, str], op_params: dict[str, object], seed: int
) -> tuple[dict[str, str], dict[str, object], str]:
    """Single point mutation in the 5'UTR/RBS region at ``position`` -> ``base``.

    If unspecified, deterministically derive (position, base) from ``seed`` (a
    reproducible point mutation). The edit must actually change a base (a no-op
    mutation is a mis-specification and fails loudly)."""
    rbs = parent["rbs"].upper().replace("U", "T")
    if not rbs:
        raise MutationError("parent has no rbs region to mutate")
    position = op_params.get("position")
    base = op_params.get("base")
    if position is None:
        position = seed % len(rbs)
    if not (0 <= int(position) < len(rbs)):
        raise MutationError(f"utr5 position {position} out of range [0,{len(rbs)})")
    position = int(position)
    if base is None:
        # deterministic: next base in the cycle that differs from the current one
        alt = [b for b in _BASES if b != rbs[position]]
        base = alt[seed % len(alt)]
    base = str(base).upper()
    if base not in _BASES:
        raise MutationError(f"utr5 base must be one of {_BASES}, got {base!r}")
    if base == rbs[position]:
        raise MutationError(
            f"utr5_mutation is a no-op ({rbs[position]}->{base} at {position})"
        )
    child_rbs = rbs[:position] + base + rbs[position + 1 :]
    child = _rebuild(parent["promoter"], child_rbs, parent["cds"])
    return (
        child,
        {"position": position, "base": base},
        f"utr5:{position}{rbs[position]}>{base}",
    )


def _edit_cds_synonymous(
    parent: dict[str, str], op_params: dict[str, object], seed: int
) -> tuple[dict[str, str], dict[str, object], str]:
    """Single synonymous codon substitution in the CDS (SAME amino acid).

    ``codon_index`` + ``target_codon`` name the edit; if unspecified, pick a
    codon (after the start) by ``seed`` and rotate to the next-ranked synonymous
    codon. Rejects a non-synonymous target LOUDLY (translation-invariance is the
    dual-use-safety guarantee)."""
    cds = parent["cds"].upper().replace("U", "T")
    n_codons = len(cds) // 3
    if n_codons < 2:
        raise MutationError("CDS too short for a synonymous substitution")
    codons = [cds[i * 3 : i * 3 + 3] for i in range(n_codons)]
    idx = op_params.get("codon_index")
    if idx is None:
        # deterministic: first codon (after start) with >1 synonym, offset by seed
        candidates = [
            i for i in range(1, n_codons) if len(SYNONYMS[CODON_TABLE[codons[i]]]) > 1
        ]
        if not candidates:
            raise MutationError("no degenerate codon available for synonymous edit")
        idx = candidates[seed % len(candidates)]
    idx = int(idx)
    if not (0 < idx < n_codons):
        raise MutationError(f"codon_index {idx} out of range (1,{n_codons})")
    aa = CODON_TABLE[codons[idx]]
    ranked = SYNONYMS[aa]
    target = op_params.get("target_codon")
    if target is None:
        cur = codons[idx]
        # rotate to the next synonymous codon (deterministic, seed-offset)
        pos = ranked.index(cur)
        target = ranked[(pos + 1 + (seed % max(1, len(ranked) - 1))) % len(ranked)]
        if target == cur and len(ranked) > 1:
            target = ranked[(pos + 1) % len(ranked)]
    target = str(target).upper()
    if CODON_TABLE.get(target) != aa:
        raise MutationError(
            f"cds_synonymous target {target!r} is not synonymous with {codons[idx]!r} "
            f"(would change {aa}->{CODON_TABLE.get(target)})"
        )
    old = codons[idx]
    codons[idx] = target
    child = _rebuild(parent["promoter"], parent["rbs"], "".join(codons))
    return (
        child,
        {"codon_index": idx, "target_codon": target},
        f"cds_syn:{idx}:{old}>{target}",
    )


# ---------------------------------------------------------------------------
# Operator registry + uniform apply()
# ---------------------------------------------------------------------------

_EDIT_FNS: dict[str, Callable[..., tuple[dict[str, str], dict[str, object], str]]] = {
    "promoter_swap": _edit_promoter_swap,
    "rbs_swap": _edit_rbs_swap,
    "codon_optimize": _edit_codon_optimize,
    "utr5_mutation": _edit_utr5_mutation,
    "cds_synonymous": _edit_cds_synonymous,
}

#: The five auditable operators, in a stable order (the M25 design-move menu).
OPERATOR_NAMES: tuple[str, ...] = tuple(_EDIT_FNS)


def _source_fingerprint(fn: Callable[..., object]) -> str:
    """sha256 over the operator source -- a change to an operator flips design
    identity (mirrors ``kernel.claims._source_fingerprint``)."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = fn.__qualname__
    return "sha256:" + hashlib.sha256(src.encode("utf-8")).hexdigest()


def _canonical_params(params: dict[str, object]) -> str:
    import json

    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _child_id(parent_id: str, operator: str, resolved: dict[str, object], seed: int) -> str:
    """Content-addressed child id over (parent, operator, resolved params, seed).

    Same edit -> same id (idempotent); different edit -> different id; NEVER the
    parent's id. A short readable prefix + an 8-hex content hash."""
    payload = f"{parent_id}|{operator}|{_canonical_params(resolved)}|{seed}"
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"{parent_id}>{operator}:{h}"


def apply_operator(
    operator: str,
    parent_id: str,
    parent_components: dict[str, str],
    op_params: dict[str, object] | None = None,
    seed: int = 0,
) -> tuple[dict[str, str], EditProvenance]:
    """Apply one named operator to a parent construct.

    Pure and deterministic: ``apply(parent, op_params, seed) -> (child_components,
    EditProvenance)``. ``child_components`` obeys ``sequence == promoter+rbs+cds``
    and drops straight into the unchanged sequence adapter. Raises ``MutationError``
    on any mis-specified edit (never silently returns the parent)."""
    if operator not in _EDIT_FNS:
        raise MutationError(
            f"unknown operator {operator!r}; available: {list(OPERATOR_NAMES)}"
        )
    op_params = dict(op_params or {})
    fn = _EDIT_FNS[operator]
    child_components, resolved, detail = fn(parent_components, op_params, seed)
    # invariant: concatenation contract holds
    assert (
        child_components["sequence"]
        == child_components["promoter"] + child_components["rbs"] + child_components["cds"]
    ), "operator broke sequence==promoter+rbs+cds"
    child_id = _child_id(parent_id, operator, resolved, seed)
    prov = EditProvenance(
        operator=operator,
        params=resolved,
        seed=seed,
        parent_id=parent_id,
        child_id=child_id,
        detail=detail,
        operator_fingerprint=_source_fingerprint(fn),
    )
    return child_components, prov


def apply_by_id(
    operator: str,
    parent_id: str,
    op_params: dict[str, object] | None = None,
    seed: int = 0,
) -> tuple[dict[str, str], EditProvenance]:
    """Convenience: apply an operator to a PRESET construct named ``parent_id``."""
    return apply_operator(operator, parent_id, components_for(parent_id), op_params, seed)


# Back-compat shim: the skeleton exported ``promoter_swap(parent_id, new_promoter_id)``
# returning a MutatedConstruct. Keep it working (provider used it), now backed by the
# uniform path so there is one source of truth.
def promoter_swap(parent_id: str, new_promoter_id: str) -> MutatedConstruct:
    child, prov = apply_by_id(
        "promoter_swap", parent_id, {"new_promoter_id": new_promoter_id}
    )
    return MutatedConstruct(
        child_id=prov.child_id,
        parent_id=parent_id,
        operator="promoter_swap",
        detail=prov.detail,
        components=child,
        provenance=prov,
    )


# ---------------------------------------------------------------------------
# Dry design-coordinate proxy (NOT truth)
# ---------------------------------------------------------------------------


def score_components(components: dict[str, str]) -> float:
    """Deterministic ``expression_proxy`` for a (mutated or preset) construct's
    components. Design-coordinate proxy only -- NOT a truth/wet channel (see
    ``sequences.expression_features``)."""
    return expression_features(
        sequence=components["sequence"],
        promoter=components.get("promoter"),
        rbs=components.get("rbs"),
        cds=components.get("cds"),
    ).expression_proxy


def module_fingerprint() -> str:
    """sha256 over the whole operator module source -- the ``config_fingerprint``
    seam: when generated candidates are used, this hash should enter run identity
    (a change to any operator flips it). One value for all five operators."""
    src = inspect.getsource(inspect.getmodule(apply_operator))
    return "sha256:" + hashlib.sha256(src.encode("utf-8")).hexdigest()
