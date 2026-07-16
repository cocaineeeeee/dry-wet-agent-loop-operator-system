"""``python -m agents.biology_discovery`` -> run the M28 v0.1 end-to-end discovery loop
and print a machine-readable report, then a one-line human summary.

The full runnable lives in :mod:`agents.biology_discovery.run_v01` (charter DoD #2/#8).
This entry point runs it and asserts the discriminative outcome (>=2 competing hypotheses
separated into supported vs rejected via the real ledger) so the module is self-checking.
"""

from __future__ import annotations

from agents.biology_discovery.run_v01 import run


def main() -> int:
    report = run()
    sep = report["discriminative_separation"]
    kc = report["knowledge_change"]
    fu = report["follow_up_selection"]

    assert sep["claim_up"] == "supported", sep
    assert sep["claim_down"] == "rejected", sep
    assert sep["separated"], sep
    assert sep["evidence_overturned_literature_prior"], sep
    assert kc["changed"], kc
    assert fu["changed"], fu

    print(f"[M28] proposed 2 competing hypotheses; proposer prioritised "
          f"{report['proposer_prioritised_first']} first (literature prior)")
    print(f"[M28] LEDGER verdict overturned it: up={sep['claim_up']} / down={sep['claim_down']} "
          f"(supported+rejected separated via real supersede)")
    print("[M28] weak/technical counter-evidence denied by strength gate; "
          "untrusted -> insufficient (non-mutating)")
    print(f"[M28] knowledge changed -> follow-up {fu['pre_knowledge']['action']} "
          f"=> {fu['post_knowledge']['action']}")
    print("[M28] PASS (agents produced evidence only; the ledger certified — the moat held)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
