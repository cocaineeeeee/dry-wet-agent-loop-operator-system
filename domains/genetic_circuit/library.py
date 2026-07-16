"""Preset public parts + preset circuits for the M26 v0.1 genetic-circuit domain.

Public design catalogue (NEVER truth), mirroring the M24 ``constructs.py`` discipline:
these are real public genetic elements (iGEM Registry Anderson promoters, the canonical
B0034 SD RBS, the sfGFP reporter N-terminus, and the LacI/TetR repressors of the classic
Gardner-Collins toggle). The catalogue calibrates the DESIGN COORDINATE; it does not carry
the dynamic phenotype (that is produced by simulation / the hidden wet dynamic truth surface).

Two preset circuit families realise the ref's two-step task plan (docs/bio_refs/02 §B):
  1. ``expression_cassette``  -- the WALKING SKELETON: a single promoter->rbs->reporter
     unit, a constitutive expression cassette. Reuses the M24 Anderson promoter / RBS /
     reporter parts. Dynamic phenotype = monotone rise to a steady-state level; the design
     coordinate is the promoter strength (the M24 dose ladder, now with a TIME axis).
  2. ``toggle_switch``        -- the FIRST DYNAMIC milestone: a two-node mutual-repression
     latch (LacI <-> TetR), the Gardner-Collins toggle, plus a reporter on one arm.
     Dynamic phenotype = bistable separation / switching time under intrinsic noise.
"""

from __future__ import annotations

from .graph import (
    INT_ACTIVATES,
    INT_REPRESSES,
    ROLE_CDS,
    ROLE_PROMOTER,
    ROLE_RBS,
    ROLE_TERMINATOR,
    CircuitGraph,
    Interaction,
    Kinetics,
    Part,
    TranscriptionUnit,
)

# --------------------------------------------------------------------------- parts
# Real public elements (design knowledge). Sequences abbreviated but real-derived; they
# are NOT load-bearing for topology identity (only role/structure is).
_PROMOTER_STRONG = Part("pTet_J23100", ROLE_PROMOTER, "ttgacggctagctcagtcctaggtacagtgctagc")
_PROMOTER_MED = Part("pTet_J23106", ROLE_PROMOTER, "tttacggctagctcagtcctaggtatagtgctagc")
_PROMOTER_WEAK = Part("pTet_J23114", ROLE_PROMOTER, "tttatggctagctcagtcctaggtacaatgctagc")
_PLAC = Part("pLac", ROLE_PROMOTER, "aattgtgagcggataacaattgacattgtgagcggataacaagat")
_PTET = Part("pTetR", ROLE_PROMOTER, "tccctatcagtgatagagattgacatccctatcagtgatagagat")
_PCI = Part("pR_lambda", ROLE_PROMOTER, "taacaccgtgcgtgttgactattttacctctggcggtgataat")
_PIND = Part("pBAD_inducible", ROLE_PROMOTER, "acgcttttatcgcaactctctactgtttctccatacccgttttt")
_RBS_STRONG = Part("B0034", ROLE_RBS, "aaagaggagaaa")
_CDS_GFP = Part("sfGFP", ROLE_CDS, "atgagcaaaggagaagaactttt")
_CDS_LACI = Part("LacI", ROLE_CDS, "atggtgaatgtgaaaccagtaacg")
_CDS_TETR = Part("TetR", ROLE_CDS, "atgtccagattagataaaagtaaa")
_CDS_CI = Part("cI", ROLE_CDS, "atgagcacaaaaaagaaaccatta")
_TERM = Part("B0015", ROLE_TERMINATOR, "ccaggcatcaaataaaacg")

#: the external small-molecule inducer species (a dose knob, NOT a part / product).
INDUCER_IPTG = "IPTG"

#: Promoter-strength design ladder for the expression cassette (public relative strength).
#: (part, relative promoter strength coordinate in [0,1]).
_CASSETTE_LADDER: list[tuple[Part, float]] = [
    (_PROMOTER_STRONG, 1.00),
    (_PROMOTER_MED, 0.47),
    (_PROMOTER_WEAK, 0.10),
]


# --------------------------------------------------------------------------- cassette


def expression_cassette(coord: float, promoter: Part | None = None) -> CircuitGraph:
    """A single constitutive promoter->B0034->sfGFP reporter cassette (the walking
    skeleton). ``coord`` in [0,1] is the public design coordinate (promoter strength);
    it scales the max production rate ``beta`` so a stronger design rises to a higher
    steady state -- the TIME-resolved analogue of the M24 dose ladder. Deterministic."""
    prom = promoter or _PROMOTER_STRONG
    kin = Kinetics(beta=10.0 + 40.0 * coord, basal=0.5, gamma=1.0, K=1e9, n=1.0)
    tu = TranscriptionUnit(
        tu_id="tu_reporter", promoter=prom.part_id, rbs=_RBS_STRONG.part_id,
        cds=_CDS_GFP.part_id, product="GFP", kinetics=kin, is_reporter=True,
    )
    return CircuitGraph(
        circuit_id=f"cassette_{prom.part_id}",
        parts=(prom, _RBS_STRONG, _CDS_GFP, _TERM),
        units=(tu,),
        interactions=(),
        behaviour="expression_cassette",
    )


def cassette_ladder() -> list[tuple[str, float, CircuitGraph]]:
    """(circuit_id, design coord, graph) for the three-rung promoter-strength cassette
    ladder (strong -> weak). Ordered strongest first."""
    return [
        (f"cassette_{p.part_id}", coord, expression_cassette(coord, promoter=p))
        for p, coord in _CASSETTE_LADDER
    ]


# --------------------------------------------------------------------------- toggle


def toggle_switch(coord: float = 1.0) -> CircuitGraph:
    """The two-node Gardner-Collins toggle: LacI represses the TetR unit's promoter and
    TetR represses the LacI unit's promoter (a mutual-repression 2-cycle). A GFP reporter
    rides the TetR arm so the state is observable.

    ``coord`` in [0,1] tunes the repression STRENGTH (beta of both repressor arms): high
    coord -> deep, well-separated bistability; low coord -> weak/collapsing bistability.
    This is the design coordinate the high/flipped/flat dynamic faces move along.
    n=2 (cooperative) is required for bistability; K=1 in the dimensionless product units."""
    beta = 10.0 + 40.0 * coord
    kin = Kinetics(beta=beta, basal=1.0, K=1.0, n=2.0, gamma=1.0)
    tu_laci = TranscriptionUnit(
        tu_id="tu_laci", promoter=_PLAC.part_id, rbs=_RBS_STRONG.part_id,
        cds=_CDS_LACI.part_id, product="LacI", kinetics=kin,
    )
    tu_tetr = TranscriptionUnit(
        tu_id="tu_tetr", promoter=_PTET.part_id, rbs=_RBS_STRONG.part_id,
        cds=_CDS_TETR.part_id, product="TetR", kinetics=kin, is_reporter=True,
    )
    return CircuitGraph(
        circuit_id=f"toggle_c{coord:.2f}",
        parts=(_PLAC, _PTET, _RBS_STRONG, _CDS_LACI, _CDS_TETR, _TERM),
        units=(tu_laci, tu_tetr),
        interactions=(
            Interaction(kind=INT_REPRESSES, regulator="LacI", target_tu="tu_tetr"),
            Interaction(kind=INT_REPRESSES, regulator="TetR", target_tu="tu_laci"),
        ),
        behaviour="toggle_switch",
    )


# --------------------------------------------------------------------------- dose-response


def dose_response(coord: float = 1.0) -> CircuitGraph:
    """An INDUCIBLE expression cassette: a single reporter unit whose promoter is ACTIVATED by
    an external small-molecule inducer (IPTG), the canonical dose-response element. ``coord``
    in [0,1] is the applied inducer DOSE (a simulation condition mapped to the input level by
    the adapter, NOT a topology property), so a higher dose drives a higher settled reporter
    level -- the design coordinate the steady-state dynamic faces move along.
    n=2 activation Hill gives the sigmoidal dose-response whose EC50 the curve derivation reads."""
    kin = Kinetics(beta=50.0, basal=0.5, K=1.0, n=2.0, gamma=1.0)
    tu = TranscriptionUnit(
        tu_id="tu_reporter", promoter=_PIND.part_id, rbs=_RBS_STRONG.part_id,
        cds=_CDS_GFP.part_id, product="GFP", kinetics=kin, is_reporter=True,
    )
    return CircuitGraph(
        circuit_id=f"dose_IPTG_c{coord:.2f}",
        parts=(_PIND, _RBS_STRONG, _CDS_GFP, _TERM),
        units=(tu,),
        interactions=(Interaction(kind=INT_ACTIVATES, regulator=INDUCER_IPTG, target_tu="tu_reporter"),),
        behaviour="dose_response",
        inputs=(INDUCER_IPTG,),
    )


def dose_ladder(doses: tuple[float, ...] = (0.1, 0.4, 0.7, 1.0)) -> list[tuple[str, float, CircuitGraph]]:
    """(circuit_id, dose coord, graph) for a dose-response ladder (one inducible circuit per
    dose). Ordered lowest dose first; feeds the EC50 curve derivation."""
    return [(f"dose_IPTG_c{d:.2f}", d, dose_response(d)) for d in doses]


# --------------------------------------------------------------------------- feed-forward loop


def feed_forward_loop() -> CircuitGraph:
    """A three-node coherent feed-forward loop (FFL): a constitutively-expressed master TF_A
    ACTIVATES both an intermediate TF_B unit and the output reporter unit, and TF_B also
    ACTIVATES the reporter (the A->B, A->C, B->C triangle). One of the canonical six circuit
    types; here it exercises the FFL motif detector and a three-species ODE reaching a steady
    state (no dynamic face -- it is a structure/verify + simulation exemplar)."""
    kin = Kinetics(beta=40.0, basal=0.5, K=1.0, n=2.0, gamma=1.0)
    kin_a = Kinetics(beta=40.0, basal=8.0, K=1.0, n=2.0, gamma=1.0)  # constitutive driver
    tu_a = TranscriptionUnit("tu_a", _PROMOTER_STRONG.part_id, _RBS_STRONG.part_id,
                             _CDS_LACI.part_id, "TF_A", kinetics=kin_a)
    tu_b = TranscriptionUnit("tu_b", _PLAC.part_id, _RBS_STRONG.part_id,
                             _CDS_TETR.part_id, "TF_B", kinetics=kin)
    tu_c = TranscriptionUnit("tu_c", _PCI.part_id, _RBS_STRONG.part_id,
                             _CDS_GFP.part_id, "GFP", kinetics=kin, is_reporter=True)
    return CircuitGraph(
        circuit_id="ffl_coherent",
        parts=(_PROMOTER_STRONG, _PLAC, _PCI, _RBS_STRONG, _CDS_LACI, _CDS_TETR, _CDS_GFP, _TERM),
        units=(tu_a, tu_b, tu_c),
        interactions=(
            Interaction(kind=INT_ACTIVATES, regulator="TF_A", target_tu="tu_b"),
            Interaction(kind=INT_ACTIVATES, regulator="TF_A", target_tu="tu_c"),
            Interaction(kind=INT_ACTIVATES, regulator="TF_B", target_tu="tu_c"),
        ),
        behaviour="feed_forward_loop",
    )


# --------------------------------------------------------------------------- oscillator (repressilator)


def repressilator(coord: float = 1.0) -> CircuitGraph:
    """The minimal three-node REPRESSILATOR: LacI -| TetR -| cI -| LacI, a repression 3-cycle
    that sustains oscillation (docs/bio_refs/02 §B task 3). A GFP-fused cI arm is the reporter.
    ``coord`` in [0,1] tunes the degradation/dilution rate ``gamma`` of all three arms
    (gamma = 0.4 + 0.6*coord), which sets the oscillation FREQUENCY -- the design coordinate
    the oscillation-frequency derived phase moves along (frequency rises with coord).
    n=3 cooperativity + asymmetric initial condition are required for a robust limit cycle."""
    gamma = 0.4 + 0.6 * coord
    kin = Kinetics(beta=60.0, basal=0.5, K=1.0, n=3.0, gamma=gamma)
    tu_laci = TranscriptionUnit("tu_laci", _PCI.part_id, _RBS_STRONG.part_id,
                                _CDS_LACI.part_id, "LacI", kinetics=kin)
    tu_tetr = TranscriptionUnit("tu_tetr", _PLAC.part_id, _RBS_STRONG.part_id,
                                _CDS_TETR.part_id, "TetR", kinetics=kin)
    tu_ci = TranscriptionUnit("tu_ci", _PTET.part_id, _RBS_STRONG.part_id,
                              _CDS_CI.part_id, "cI", kinetics=kin, is_reporter=True)
    return CircuitGraph(
        circuit_id=f"repressilator_c{coord:.2f}",
        parts=(_PCI, _PLAC, _PTET, _RBS_STRONG, _CDS_LACI, _CDS_TETR, _CDS_CI, _TERM),
        units=(tu_laci, tu_tetr, tu_ci),
        interactions=(
            Interaction(kind=INT_REPRESSES, regulator="cI", target_tu="tu_laci"),
            Interaction(kind=INT_REPRESSES, regulator="LacI", target_tu="tu_tetr"),
            Interaction(kind=INT_REPRESSES, regulator="TetR", target_tu="tu_ci"),
        ),
        behaviour="oscillator",
    )


# --------------------------------------------------------------------------- illegal


def broken_toggle_missing_feedback() -> CircuitGraph:
    """A toggle-INTENT circuit that is missing one repression edge (LacI represses TetR,
    but TetR does NOT repress LacI). Structurally valid genetics, but the mutual-repression
    MOTIF is absent -> the function-level verify gate must REJECT it as a toggle. Used to
    prove the gate stops illegal topologies before wasting dynamic simulation."""
    tg = toggle_switch(1.0)
    # keep only the LacI->TetR edge (drop the TetR->LacI feedback).
    kept = tuple(i for i in tg.interactions if i.target_tu == "tu_tetr")
    from dataclasses import replace

    return replace(tg, circuit_id="toggle_broken", interactions=kept)


def dangling_regulator_circuit() -> CircuitGraph:
    """A circuit whose interaction references a regulator species that NO unit expresses
    (an execution-level dangling reference) -- the gate must reject at level 1."""
    tg = toggle_switch(1.0)
    from dataclasses import replace

    bad = tg.interactions + (
        Interaction(kind=INT_REPRESSES, regulator="GhostProtein", target_tu="tu_laci"),
    )
    return replace(tg, circuit_id="toggle_dangling", interactions=bad)


def broken_repressilator_even_cycle() -> CircuitGraph:
    """An oscillator-INTENT circuit whose repression graph is only a 2-CYCLE (even), not the
    odd (>=3) cycle a repressilator needs -- structurally valid genetics, but the oscillator
    MOTIF is absent, so the function-level gate must REJECT it. Proves the oscillator motif
    detector does not accept a bistable (even-cycle) topology as an oscillator."""
    from dataclasses import replace

    r = repressilator(1.0)
    # keep only the LacI-|TetR and (add) TetR-|LacI edges -> a 2-cycle, not the 3-cycle.
    kept = (
        Interaction(kind=INT_REPRESSES, regulator="LacI", target_tu="tu_tetr"),
        Interaction(kind=INT_REPRESSES, regulator="TetR", target_tu="tu_laci"),
    )
    return replace(r, circuit_id="repressilator_broken_even", interactions=kept)
