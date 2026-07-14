"""M27 virtual-cell model backends (adapter-layer scorers; charter §3 competition layer).

Each backend is a *proposer/scorer only* -- it emits ``ResponsePrediction`` (dry
evidence), never a claim. Three backends ship in v0.1:

  * :class:`~expos.adapters.models.virtual_cell_baselines.MeanBaselineBackend`   (baseline)
  * :class:`~expos.adapters.models.virtual_cell_baselines.LinearResponseBackend` (baseline)
  * :class:`~expos.adapters.models.virtual_cell_complex.KNNResponseBackend`      (complex)
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

__all__ = [
    "BioModelBackend",
    "PerturbationBatch",
    "ResponsePrediction",
    "MeanBaselineBackend",
    "LinearResponseBackend",
    "KNNResponseBackend",
    "solve_y_axb",
]
