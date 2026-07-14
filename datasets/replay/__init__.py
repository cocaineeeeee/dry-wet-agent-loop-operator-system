"""M27 retrospective replay datasets. Every dataset carries non-wet provenance
(``is_wet_observation=False``, ``validation_level='retrospective'``): benchmark /
calibration material only, NEVER a wet observation of the current run (charter §4)."""

from datasets.replay.synthetic_perturbseq import make_replay_dataset

__all__ = ["make_replay_dataset"]
