"""Domain-local smoke for M25 generative_construct: parent -> child -> proxy score.

Deterministic. Takes a weak preset root (j23103), applies promoter-swap children over
the public catalogue, and shows that swapping in a stronger promoter yields a higher
design-coordinate proxy (the design move is proxy-visible). NOT a wet/truth channel.
"""

from __future__ import annotations

from domains.generative_construct.provider import GenerativeConstructProvider


def smoke() -> int:
    prov = GenerativeConstructProvider()
    root_id = "j23103"  # weakest-design preset
    lineage = prov.build_lineage(root_id)
    root = lineage.nodes[root_id]
    children = lineage.children_of(root_id)

    assert children, "no children generated"
    best = lineage.best()
    # A promoter-swap into a stronger promoter must beat the weak root's proxy.
    assert best is not None and best.proxy > root.proxy, (
        f"best child {best.proxy:.4f} did not beat root {root.proxy:.4f}"
    )
    # Determinism: re-running yields byte-identical child proxies.
    again = prov.build_lineage(root_id)
    assert [c.proxy for c in again.children_of(root_id)] == [
        c.proxy for c in children
    ], "non-deterministic proxies"

    print(f"[M25 smoke] root {root_id} proxy={root.proxy:.4f}")
    print(f"[M25 smoke] {len(children)} promoter-swap children")
    print(f"[M25 smoke] best design {best.design_id!r} proxy={best.proxy:.4f} "
          f"(delta +{best.proxy - root.proxy:.4f})")
    print("[M25 smoke] PASS (deterministic parent->child->proxy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(smoke())
