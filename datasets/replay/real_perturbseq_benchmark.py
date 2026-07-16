"""REAL public Perturb-seq retrospective benchmark ingest (charter §4 dual-role boundary).

Unlike ``synthetic_perturbseq`` (a generated fixture with a KNOWN ground truth, used to
*prove* the baseline-gate is discriminative), this module ingests a **genuinely real,
published** retrospective benchmark: the per-perturbation prediction metrics from

    Ahlmann-Eltze, Huber & Anders, "Deep learning-based predictions of gene perturbation
    effects do not yet outperform simple linear baselines", *Nature Methods* 22:1657-1661
    (2025).  DOI 10.1038/s41592-025-02772-6 ; source data Zenodo 14832393.

These are the real numbers behind the paper's headline result -- for every held-out
perturbation of the real **Adamson** and **Replogle (K562 / RPE1)** Perturb-seq screens,
the L2 distance and Pearson-delta of each method's transcriptome prediction. Methods span
the ``mean`` baseline, a ``gears`` deep-learning model, and the ``scgpt`` / ``uce`` /
``scbert`` single-cell *foundation* models. This is exactly the head-to-head the M27
baseline-gate is modeled on -- and here we run the gate on the REAL published numbers.

WHY NOT RAW EXPRESSION h5ad: obtaining and pseudobulking the raw Perturb-seq count
matrices (Norman/Replogle, GEARS ``perturb_processed.h5ad``) needs anndata/scanpy + a
multi-GB download and is out of v0.1 scope (§1.5 forbids a full single-cell pipeline).
The published *per-perturbation metric table* is the real, honest, lightweight
retrospective artifact: it is derived from that Perturb-seq data and carries its scope.

═══════════════════════ THE IRON RULE (charter §4) ═══════════════════════════════════
This is **benchmark / calibration material ONLY**. Every table carries
:class:`~domains.perturbation.objects.DatasetProvenance` with ``is_wet_observation=False``
and ``validation_level='retrospective'``; the class exposes NO path to promote a real
metric into a this-run trusted observation. Public data may benchmark/calibrate; it is
NEVER presented as an observation this run produced.

The vendored extract (``data/real_perturbseq_benchmark_single.csv.gz``) is a byte-faithful
projection of the Zenodo source data (columns dataset/seed/method/perturbation/r2_delta/
l2/approach, test split only), so the real-data interface is self-contained and does not
depend on the git-ignored ``references/`` clone.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from domains.perturbation.objects import DatasetProvenance

_DATA = Path(__file__).with_name("data") / "real_perturbseq_benchmark_single.csv.gz"

#: The published source of the vendored numbers (folds into every provenance).
_SOURCE = (
    "Ahlmann-Eltze, Huber & Anders, Nature Methods 22:1657-1661 (2025) "
    "[DOI 10.1038/s41592-025-02772-6; source data Zenodo 14832393]"
)

#: Real Perturb-seq screens behind each benchmark dataset id (scope = context boundary).
_DATASET_SCOPE = {
    "adamson": "Adamson et al. Perturb-seq, K562 (cancer line); single-gene CRISPRi UPR screen",
    "replogle_k562_essential": "Replogle et al. Perturb-seq, K562 (cancer line); essential-gene knockdowns",
    "replogle_rpe1_essential": "Replogle et al. Perturb-seq, RPE1 (near-diploid line); essential-gene knockdowns",
}

#: Which method is the mandatory cheap baseline in the real benchmark (bio_refs §1).
_BASELINE_METHOD = "mean"


@dataclass(frozen=True)
class RealBenchmarkRecord:
    dataset: str
    seed: int
    method: str
    perturbation: str
    r2_delta: float  # Pearson of predicted vs true delta-from-control (higher better)
    l2: float  # L2 distance predicted vs true pseudobulk (lower better)
    approach: str  # baseline / deep_learning / foundation_model


def _load_rows() -> list[RealBenchmarkRecord]:
    if not _DATA.exists():  # pragma: no cover - vendored file ships with the repo
        raise FileNotFoundError(
            f"vendored real benchmark missing: {_DATA} (extract from Zenodo 14832393 "
            f"source data, single_perturbation_prediction.xlsx, test split)"
        )
    out: list[RealBenchmarkRecord] = []
    with gzip.open(_DATA, "rt", newline="") as f:
        for r in csv.DictReader(f):
            out.append(
                RealBenchmarkRecord(
                    dataset=r["dataset"],
                    seed=int(r["seed"]),
                    method=r["method"],
                    perturbation=r["perturbation"],
                    r2_delta=float(r["r2_delta"]),
                    l2=float(r["l2"]),
                    approach=r["approach"],
                )
            )
    return out


@dataclass(frozen=True)
class RealBenchmarkTable:
    """The real published per-perturbation benchmark, provenance-guarded as NON-wet.

    Construct via :meth:`load`. Query real per-perturbation L2 / Pearson-delta per
    (dataset, method) with :meth:`aligned`, and run the baseline-gate on the REAL numbers
    with :meth:`real_baseline_gate`.
    """

    records: tuple[RealBenchmarkRecord, ...]

    @classmethod
    def load(cls) -> "RealBenchmarkTable":
        return cls(records=tuple(_load_rows()))

    # -- catalogue ----------------------------------------------------------

    def datasets(self) -> list[str]:
        return sorted({r.dataset for r in self.records})

    def methods(self, dataset: str) -> list[str]:
        return sorted({r.method for r in self.records if r.dataset == dataset})

    def seeds(self, dataset: str) -> list[int]:
        return sorted({r.seed for r in self.records if r.dataset == dataset})

    def provenance(self, dataset: str) -> DatasetProvenance:
        """The DUAL-ROLE guard for this real dataset (``is_wet_observation`` hard-False)."""
        return DatasetProvenance(
            source=_SOURCE,
            scope=_DATASET_SCOPE.get(dataset, f"{dataset} (real Perturb-seq benchmark)"),
            validation_level="retrospective",
            is_wet_observation=False,
            notes=(
                "REAL published per-perturbation metric table (not raw expression). "
                "Benchmark/calibration ONLY -- never a wet observation of this run "
                "(charter §4 iron rule)."
            ),
        )

    def fingerprint(self, dataset: str) -> str:
        """Content fingerprint folding provenance+scope and the real data bytes, so a
        dataset/scope change flips identity (charter: model/dataset versions -> provenance)."""
        h = hashlib.sha256()
        h.update(self.provenance(dataset).fingerprint().encode())
        for r in sorted(
            (x for x in self.records if x.dataset == dataset),
            key=lambda x: (x.method, x.seed, x.perturbation),
        ):
            h.update(f"{r.method}|{r.seed}|{r.perturbation}|{r.l2}|{r.r2_delta}".encode())
        return "realbench:sha256:" + h.hexdigest()[:16]

    # -- aligned metric access ----------------------------------------------

    def aligned(
        self, dataset: str, method: str, *, seed: int | None = None, against: str = _BASELINE_METHOD
    ) -> dict[str, np.ndarray]:
        """Per-perturbation L2/Pearson-delta for ``method`` vs the ``against`` baseline,
        aligned on the perturbations both scored (same held-out split, paired)."""

        def _index(m: str) -> dict[tuple[int, str], RealBenchmarkRecord]:
            return {
                (r.seed, r.perturbation): r
                for r in self.records
                if r.dataset == dataset and r.method == m and (seed is None or r.seed == seed)
            }

        cand, base = _index(method), _index(against)
        keys = sorted(set(cand) & set(base))
        return {
            "keys": np.array([f"{s}:{p}" for s, p in keys], dtype=object),
            "cand_l2": np.array([cand[k].l2 for k in keys]),
            "base_l2": np.array([base[k].l2 for k in keys]),
            "cand_pearson": np.array([cand[k].r2_delta for k in keys]),
            "base_pearson": np.array([base[k].r2_delta for k in keys]),
        }


@dataclass(frozen=True)
class RealGateVerdict:
    """Baseline-gate outcome for one real method vs the real ``mean`` baseline."""

    dataset: str
    method: str
    approach: str
    beat_baseline: str
    l2_improvement: float  # mean(base_l2 - cand_l2); positive = candidate better
    ci_low: float  # paired-bootstrap 95% lower bound on the improvement
    admitted: bool
    n_pert: int
    reason: str


def real_baseline_gate(
    table: RealBenchmarkTable,
    dataset: str,
    *,
    seed: int | None = None,
    min_improvement: float = 0.05,
) -> list[RealGateVerdict]:
    """Run the SAME baseline-gate logic on the REAL published numbers: does any real
    deep-learning / foundation method *significantly* beat the real ``mean`` baseline on
    this real Perturb-seq screen? Reuses the exact paired-bootstrap CI from the synthetic
    competition layer (:func:`domains.perturbation.competition._paired_bootstrap_ci_low`).

    The published result -- and this function -- confirm with REAL data that essentially
    none do. That is the external grounding of the synthetic baseline-gate: the gate is
    not a contrived toy; it enforces a genuine, published empirical fact.
    """
    from domains.perturbation.competition import _paired_bootstrap_ci_low  # local import

    verdicts: list[RealGateVerdict] = []
    for method in table.methods(dataset):
        if method == _BASELINE_METHOD:
            continue
        a = table.aligned(dataset, method, seed=seed)
        if len(a["cand_l2"]) == 0:
            continue
        imp_mean, ci_low = _paired_bootstrap_ci_low(a["base_l2"], a["cand_l2"], seed=0)
        admitted = (imp_mean > min_improvement) and (ci_low > 0.0)
        approach = next(
            (r.approach for r in table.records if r.dataset == dataset and r.method == method),
            "unknown",
        )
        verdicts.append(
            RealGateVerdict(
                dataset=dataset,
                method=method,
                approach=approach,
                beat_baseline=_BASELINE_METHOD,
                l2_improvement=float(imp_mean),
                ci_low=float(ci_low),
                admitted=bool(admitted),
                n_pert=int(len(a["cand_l2"])),
                reason=(
                    f"real {method} ({approach}) vs real {_BASELINE_METHOD}: "
                    f"L2 improvement {imp_mean:+.3f} (CI-low {ci_low:+.3f}) over "
                    f"{len(a['cand_l2'])} held-out perturbations"
                ),
            )
        )
    return verdicts
