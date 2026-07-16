"""M26 v0.1 -- genetic circuit domain package (Biology Program, Team M26).

The FOURTH biology-domain skeleton and the FIRST one whose phenotype is DYNAMIC
(a time-series), not a scalar. Everything biological / circuit-specific lives here
and in the ``expos/adapters/dry/circuit*`` + ``expos/adapters/wet/timeseries*`` leaf
adapters -- the kernel / planner / evidence-compiler / claim-ledger never see a
promoter / repressor / toggle / Hill literal (the domain-neutral-kernel hard gate,
BIOLOGY_PROGRAM_2026 §4).

v0.1 scope (deepened but still honest simulation level):
    desired behaviour -> typed circuit graph -> 5-level verify -> ODE/stochastic simulation
    -> time-series -> derived dynamic phenotype (steady state / response amplitude /
    switching time / bistable separation / oscillation frequency; EC50 across a dose curve)
    -> behaviour claim -> topology/parameter revision -> next candidate.

Circuit families (grown from 2 to the canonical family): expression cassette, dose-response
(inducible), toggle switch, feed-forward loop, oscillator (repressilator).

Modules:
  * ``graph``      -- the SBOL-compatible-FORM typed circuit graph (canonical-hash identity +
                      external-input species; borrows the SBOL data SHAPE, NOT an RDF runtime).
  * ``library``    -- preset public parts + preset circuits (cassette ladder, dose ladder,
                      toggle, FFL, repressilator + illegal exemplars), the design catalogue.
  * ``verify``     -- the GenCircuit-RL five-level deterministic verify gate with motif
                      detection for the whole family (cassette/dose/toggle/FFL/oscillator).
  * ``sbol_adapt`` -- SBOL-utilities technique ADAPT (topology_diff, complexity_score) --
                      borrowed FORM, no RDF/API.
  * ``provider``   -- a DomainProvider-shaped provider (local ``circuit_topology`` input-kind;
                      the shared-vocabulary registration is a B seam). The high/flipped/flat
                      DYNAMIC faces + oscillatory read live on the wet ``timeseries_reader``.

Validation level: ``simulation`` (deterministic ODE proxy + optional Langevin
stochastic proxy). NO wet lab, NO real hardware. See docs/bio_seams/M26.md for the
integration-owner (B) seam list that the e2e run depends on.
"""
