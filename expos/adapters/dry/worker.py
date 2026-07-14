"""Out-of-process compute worker: ``python -m expos.adapters.dry.worker <workdir>``.

This is the *separate interpreter* that actually runs PySCF (G2: no in-process
engine call). It reads ``<workdir>/spec.json``, runs the compute, and writes:
- success  -> ``<workdir>/result.json`` and exits 0
- SCF non-convergence -> ``<workdir>/error.json`` (reason=convergence), exit 10
- any other error     -> ``<workdir>/error.json`` (reason=worker_error), exit 20

The worker also echoes the result/error as a single marked line on stdout
(``__DRY_RESULT__ <json>`` / ``__DRY_ERROR__ <json>``) so a backend whose
``collect()`` returns only captured stdout (ssh/sbatch) can still recover the
product without a shared filesystem. Exit codes are the wire protocol the
backend uses to classify the terminal state (0 ok / 10 convergence / 20 error).
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

EXIT_OK = 0
EXIT_CONVERGENCE = 10
EXIT_WORKER_ERROR = 20

RESULT_MARKER = "__DRY_RESULT__"
ERROR_MARKER = "__DRY_ERROR__"


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)  # atomic: collect() never sees a half-written file


def _echo(marker: str, payload: dict) -> None:
    # Single-line marked stdout so filesystem-less backends can recover it.
    print(f"{marker} {json.dumps(payload, sort_keys=True)}", flush=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m expos.adapters.dry.worker <workdir>", file=sys.stderr)
        return EXIT_WORKER_ERROR

    workdir = Path(argv[1])
    spec_path = workdir / "spec.json"
    result_path = workdir / "result.json"
    error_path = workdir / "error.json"

    # Imports are inside main so an import failure is caught and reported as a
    # worker error rather than crashing before the error file is written.
    try:
        from expos.adapters.dry.compute import ComputeError, ConvergenceError, run_pyscf
        from expos.adapters.dry.spec import JobSpec

        spec = JobSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        payload = {"reason": "worker_error", "detail": f"failed to load spec: {exc}"}
        _write(error_path, {**payload, "traceback": traceback.format_exc()})
        _echo(ERROR_MARKER, payload)
        return EXIT_WORKER_ERROR

    try:
        result = run_pyscf(spec)
    except ConvergenceError as exc:
        payload = {"reason": "convergence", "detail": str(exc)}
        _write(error_path, payload)
        _echo(ERROR_MARKER, payload)
        return EXIT_CONVERGENCE
    except (ComputeError, Exception) as exc:  # noqa: BLE001
        payload = {"reason": "worker_error", "detail": str(exc)}
        _write(error_path, {**payload, "traceback": traceback.format_exc()})
        _echo(ERROR_MARKER, payload)
        return EXIT_WORKER_ERROR

    result_dict = json.loads(result.model_dump_json())
    _write(result_path, result_dict)
    _echo(RESULT_MARKER, result_dict)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
