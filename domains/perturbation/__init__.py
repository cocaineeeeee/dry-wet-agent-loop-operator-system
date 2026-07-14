"""M27 perturbation-biology / virtual-cell domain (charter breadth-first v0.1).

Loop shape:
    cell-state + perturbation -> response prediction (competing backends) ->
    baseline-gate -> active selection -> trusted observation -> causal claim update ->
    changed knowledge alters the next selection.

Biological semantics live here (domain layer) + in ``expos/adapters/models/virtual_cell*``
(the model-competition backends). The kernel/ledger/evidence-compiler stay biology-blind.
"""
