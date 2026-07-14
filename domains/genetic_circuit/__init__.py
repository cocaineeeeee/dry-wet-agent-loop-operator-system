"""M26 v0.1 -- genetic circuit domain package (Biology Program, Team M26).

The FOURTH biology-domain skeleton and the FIRST one whose phenotype is DYNAMIC
(a time-series), not a scalar. Everything biological / circuit-specific lives here
and in the ``expos/adapters/dry/circuit*`` + ``expos/adapters/wet/timeseries*`` leaf
adapters -- the kernel / planner / evidence-compiler / claim-ledger never see a
promoter / repressor / toggle / Hill literal (the domain-neutral-kernel hard gate,
BIOLOGY_PROGRAM_2026 §4).

v0.1 scope (thin but runnable, honest simulation level):
    desired behaviour -> typed circuit graph -> ODE/stochastic simulation
    -> time-series -> derived dynamic phenotype (steady state / response amplitude /
    switching time) -> behaviour claim -> topology/parameter revision.

Modules:
  * ``graph``    -- the SBOL-compatible-FORM typed circuit graph (canonical-hash
                    identity; borrows the SBOL data SHAPE, NOT an RDF runtime).
  * ``library``  -- preset public parts + preset circuits (expression cassette dose
                    ladder; two-node toggle switch), the design catalogue (NOT truth).
  * ``verify``   -- the GenCircuit-RL five-level deterministic verify gate that sits
                    BETWEEN propose and dry-simulate (rejects illegal topologies cheaply).
  * ``faces``    -- the high / flipped / flat DYNAMIC acceptance faces (domain-local).
  * ``provider`` -- a DomainProvider-shaped provider (local ``circuit_topology``
                    input-kind; the shared-vocabulary registration is a B seam).

Validation level: ``simulation`` (deterministic ODE proxy + optional Langevin
stochastic proxy). NO wet lab, NO real hardware. See docs/bio_seams/M26.md for the
integration-owner (B) seam list that the e2e run depends on.
"""
