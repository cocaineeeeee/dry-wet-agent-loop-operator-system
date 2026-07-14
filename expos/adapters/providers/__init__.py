"""Concrete domain providers (M21 A-side).

Each module here implements the :class:`expos.adapters.domain_provider.DomainProvider`
contract for one domain by CONSOLIDATING the existing scattered adapter tables BY
REFERENCE (importing the live module dicts, not copying their bytes). The legacy
tables stay where they are -- the mcl LEGACY-FALLBACK path and every existing test
still point at the originals; physically retiring them is a separate batch that
follows B's declarative loader landing.

These providers live inside the importable ``expos`` package (not under the
config-only ``domains/`` directory) so:
  * they can import the leaf adapter tables they consolidate, and
  * B's ``load_domain`` can import a provider by a stable dotted path
    (``expos.adapters.providers.<name>:<Class>``) via ``importlib.import_module``,
    with no need to turn ``domains/`` into a Python package.
They import ONLY leaf adapter tables (never ``expos.domain``/``expos.mcl``), so
wiring ``load_domain -> provider`` cannot form an import cycle.
"""
