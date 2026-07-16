"""M27 virtual-cell model backends (adapter-layer scorers; charter §3 competition layer).

Each backend is a *proposer/scorer only* -- it emits ``ResponsePrediction`` (dry
evidence), never a claim. Five backends ship in v0.1, filling the bio_refs §3 grid
(``mean/NN baseline · linear response · pathway-informed · foundation · ensemble``):

  * :class:`~expos.adapters.models.virtual_cell_baselines.MeanBaselineBackend`   (baseline)
  * :class:`~expos.adapters.models.virtual_cell_baselines.LinearResponseBackend` (baseline)
  * :class:`~expos.adapters.models.virtual_cell_complex.KNNResponseBackend`      (complex)
  * :class:`~expos.adapters.models.virtual_cell_pathway.PathwayInformedBackend`  (structured)
  * :class:`~expos.adapters.models.virtual_cell_ensemble.EnsembleBackend`        (ensemble)

The last two must clear the baseline-gate like any expensive proposer (``is_baseline=False``).
"""

from expos.adapters.models.virtual_cell import (
    BioModelBackend,
    PerturbationBatch,
    ResponsePrediction,
)
from expos.adapters.models.virtual_cell_baselines import (
    LinearResponseBackend,
    MeanBaselineBackend,
    solve_y_axb,
)
from expos.adapters.models.virtual_cell_complex import KNNResponseBackend
from expos.adapters.models.virtual_cell_ensemble import EnsembleBackend
from expos.adapters.models.virtual_cell_pathway import PathwayInformedBackend

# The dry model-competition leg (imported last: it depends on the backend submodules
# above, which are fully bound by this point, so this is not a circular import).
from expos.adapters.models.cell_state_adapter import (
    CellStatePerturbationAdapter,
    CompetitionRoundResult,
)

__all__ = [
    "BioModelBackend",
    "PerturbationBatch",
    "ResponsePrediction",
    "MeanBaselineBackend",
    "LinearResponseBackend",
    "KNNResponseBackend",
    "PathwayInformedBackend",
    "EnsembleBackend",
    "solve_y_axb",
    "CellStatePerturbationAdapter",
    "CompetitionRoundResult",
]
