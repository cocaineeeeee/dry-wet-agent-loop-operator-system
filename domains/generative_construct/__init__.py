"""M25 ``generative_construct`` domain -- v0.1 SKELETON (breadth-first Biology pass).

Team M25 "Design" organ: propose *new* construct designs by deterministic design edits
over public parts (a promoter-swap mutation operator, ``expos.adapters.dry.
mutation_operators``), track parent->child *lineage*, and score each child on the
existing design-coordinate proxy. This is a scaffold: one typed domain object
(``ConstructDesign``), a lineage container, a provider stub, and a domain-local smoke
(parent -> child -> proxy score). No wet channel, no large generative model (explicit
later seam); zero kernel/ledger specialization.

Run the smoke:  ``python -m domains.generative_construct``
"""

from domains.generative_construct.objects import ConstructDesign, DesignLineage
from domains.generative_construct.provider import GenerativeConstructProvider

__all__ = ["ConstructDesign", "DesignLineage", "GenerativeConstructProvider"]
