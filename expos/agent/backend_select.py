"""Agent-backend three-mode resolution for the MCL (M18 LLM live wiring).

The ``agent_backend`` config ``{mode, provider|completion_fn, ...}`` is resolved ONCE,
at construction, into a strategy object (letters blue_to_red/086 §2 + red_to_blue/086
§2(b): EXP002 injection discipline — no ``mode``-string branching inside the round
loop; the loop is handed a resolved backend object, not a mode string).

Three modes (docs/M18_LLM_LIVE_SMOKE.md §1, ratified in mailbox letters 080/086/088/095):

  * ``template`` (or ``None``) — the deterministic template proposal drives the decision,
    BYTE-IDENTICAL to the pre-M18 loop, zero new events (the M16/M17 regression twin).
  * ``shadow``   — the decision STILL comes from the template path unchanged; additionally
    the LLM backend generates a proposal in parallel each round and exactly one
    ``agent_shadow_proposal`` audit event is emitted. The shadow leg can never affect the
    decision path (any exception from the shadow leg is caught and recorded as a failed
    audit, the loop proceeds).
  * ``llm``      — the LLM proposal DRIVES the decision. Reask exhaustion / provider death →
    empty proposal legal-quiet (the loop's ``if not cands`` guard closes the round) plus one
    ``agent_generation_failed`` event.

Invalid mode / invalid provider route form → loud failure at construction (the provider
route is pre-validated by ``LLMBackend.from_provider`` — a bare ``litellm/...`` prefix is a
library name, not a valid litellm route, and is rejected there; letter 088 §3).

Resume discipline (letter 095 §resume, I4): the shadow / failed events are emitted only while
a round actually runs; ``run_mcl_loop`` never re-runs a completed round on resume, so these
events replay from the log and the shadow leg does not re-fire (verified by the resume test).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

from expos.agent.llm_backend import LLMBackend, ProposalBatch, _loads
from expos.errors import ExposError
from expos.kernel.knowledge import KnowledgeView
from expos.kernel.objects import Actor, Candidate, DecisionKind, DecisionRecord
from expos.kernel.store import RunStore

_log = logging.getLogger("expos.agent.backend_select")

#: The three legal modes. Anything else is a construction-time loud failure.
VALID_MODES: tuple[str, ...] = ("template", "shadow", "llm")

#: Named gate versions carried on every ``agent_shadow_proposal`` event (letter 094 §4 /
#: 095 §4): the fingerprint-echo gate and the basis-subset gate the shadow leg evaluates.
#: Bump the version suffix if a gate's semantics change (Stage 2 keys off these).
SHADOW_VALIDATOR_VERSIONS: list[str] = ["fingerprint_echo@v1", "basis_subset@v1"]

#: Provider-less template proposals carry an empty usage block: "usage key present is the
#: contract, a provider not honouring usage is a legal degradation" (方案 A, letter 080/086).
_TEMPLATE_USAGE: dict[str, Any] = {}


class AgentBackendError(ExposError):
    """Invalid ``agent_backend`` config (bad mode, missing provider/completion_fn). A user
    wiring/config mistake — surfaced loud at construction, before any round runs."""


# ----------------------------------------------------------------- injected-completion capture


class _CapturingCompletion:
    """Wraps the injected completion callable and remembers the last raw text + usage so the
    shadow leg can evaluate its gates on the model's actual output and carry the usage block
    onto the event, without re-rendering (and thus re-exposing) the truth-isolated prompt."""

    def __init__(self, fn: Callable[..., Any]) -> None:
        self._fn = fn
        self.last_text: str | None = None
        self.last_usage: dict[str, Any] | None = None

    def reset(self) -> None:
        self.last_text = None
        self.last_usage = None

    def __call__(self, messages: Any, **kwargs: Any) -> Any:
        result = self._fn(messages, **kwargs)
        self.last_text = result.text
        self.last_usage = dict(result.usage or {})
        return result


class _KnowledgeHolder:
    """A one-cell knowledge provider. The strategy owns knowledge compilation (the MCL
    compiles the KnowledgeView from the live claim ledger each round) and sets ``current``
    before invoking the LLM; the backend consumes it via this callable. This is how the
    backend is constructed ONCE yet fed the round's frozen knowledge (llm_backend docstring:
    the caller owns knowledge compilation, the backend only consumes it)."""

    def __init__(self) -> None:
        self.current: KnowledgeView | None = None

    def __call__(self, _view: Any) -> KnowledgeView:
        if self.current is None:
            raise AgentBackendError(
                "no KnowledgeView set for this round — the strategy must set the holder "
                "before invoking the LLM backend"
            )
        return self.current


# ----------------------------------------------------------------- shadow gate helpers


def _claim_ids_of(kv: KnowledgeView) -> frozenset[str]:
    return frozenset(e.claim_id for h in kv.hypotheses for e in h.evidence)


def _shadow_gates(
    raw_text: str | None, kv: KnowledgeView
) -> tuple[bool, bool, bool, list[str]]:
    """Evaluate the two named gates independently on the LLM's raw output.

    Returns ``(schema_valid, fingerprint_match, basis_subset, candidate_order)``:
      * ``fingerprint_match`` — every proposal echoes the frozen ``knowledge_fingerprint``
        (``fingerprint_echo@v1``);
      * ``basis_subset``      — every proposal's basis ⊆ in-ledger claim_ids
        (``basis_subset@v1``);
      * ``schema_valid``      — the output would mint a legal DecisionRecord: it parses,
        is structurally valid, is non-empty, AND passes both gates (equivalent to the
        backend actually minting a proposal). An invalid fingerprint therefore records
        ``schema_valid=False`` AND ``fingerprint_match=False`` (the discriminative case).
    ``candidate_order`` is the first proposal's candidate ordering (for ``order_diff``)."""
    claim_ids = _claim_ids_of(kv)
    try:
        batch = ProposalBatch.model_validate(_loads(raw_text or ""))
    except Exception:  # noqa: BLE001 - any parse/validation failure => schema invalid
        return False, False, False, []
    props = batch.proposals
    if not props:
        return False, False, False, []
    fingerprint_match = all(p.knowledge_fingerprint == kv.knowledge_fingerprint for p in props)
    basis_subset = all(set(p.basis) <= claim_ids for p in props)
    schema_valid = fingerprint_match and basis_subset
    return schema_valid, fingerprint_match, basis_subset, list(props[0].candidates)


def _order_diff(template_order: list[str], llm_order: list[str]) -> dict[str, Any]:
    """A deterministic diff descriptor between the template candidate order and the LLM
    proposal order (sorted set-differences make it order-stable / replayable)."""
    t, m = list(template_order), list(llm_order)
    return {
        "template_order": t,
        "llm_order": m,
        "identical": t == m,
        "common": sorted(set(t) & set(m)),
        "template_only": sorted(set(t) - set(m)),
        "llm_only": sorted(set(m) - set(t)),
    }


def _agent_prompt_sha256(kv: KnowledgeView) -> str:
    """A stable prompt hash: a pure function of the frozen knowledge, so it is bit-identical
    across rounds under frozen knowledge (letter 094 §4 / 095 §4 — Stage 3 asserts same
    condition => same prompt hash). Salted with a version tag so a prompt-shape change bumps
    the hash intentionally."""
    canonical = json.dumps(
        {
            "v": "agent_prompt.v1",
            "knowledge_fingerprint": kv.knowledge_fingerprint,
            "claim_ids": sorted(_claim_ids_of(kv)),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- strategies


class TemplateStrategy:
    """``mode=template`` / ``None``: the deterministic template proposal drives the decision,
    zero new events (BYTE-IDENTICAL regression twin)."""

    mode = "template"

    def __init__(self, record_proposal: Callable[..., None]) -> None:
        self._record = record_proposal

    def decide(
        self, store: RunStore, round_id: int, view: KnowledgeView, template_cands: list[Candidate]
    ) -> list[Candidate]:
        self._record(store, round_id, view, template_cands)
        return template_cands


class ShadowStrategy:
    """``mode=shadow``: template drives the decision (recorded unchanged); the LLM generates a
    proposal in parallel and exactly one ``agent_shadow_proposal`` audit event is emitted. The
    shadow leg NEVER affects the decision path — any exception is caught, recorded as a failed
    audit (schema_valid=False), and the loop proceeds."""

    mode = "shadow"

    def __init__(
        self,
        record_proposal: Callable[..., None],
        llm: LLMBackend,
        holder: _KnowledgeHolder,
        capture: _CapturingCompletion,
    ) -> None:
        self._record = record_proposal
        self._llm = llm
        self._holder = holder
        self._capture = capture

    def decide(
        self, store: RunStore, round_id: int, view: KnowledgeView, template_cands: list[Candidate]
    ) -> list[Candidate]:
        # Decision path: unchanged template proposal (byte-identical to template mode).
        self._record(store, round_id, view, template_cands)
        # Shadow leg: strictly additive, decision-inert, failure-isolated.
        self._emit_shadow(store, round_id, view, template_cands)
        return template_cands

    def _emit_shadow(
        self, store: RunStore, round_id: int, kv: KnowledgeView, template_cands: list[Candidate]
    ) -> None:
        self._holder.current = kv
        self._capture.reset()
        usage: dict[str, Any] = {}
        try:
            records = self._llm.propose_priors(
                store.export_view(), round_id,
                candidate_pool=[c.params["solvent"] for c in template_cands],
            )
            if records:
                usage = dict(records[0].content.get("usage", {}))
            else:
                usage = dict(self._capture.last_usage or {})
            schema_valid, fingerprint_match, basis_subset, llm_order = _shadow_gates(
                self._capture.last_text, kv
            )
        except Exception as exc:  # noqa: BLE001 - shadow leg must never break the decision path
            _log.warning(
                "agent_shadow_proposal: shadow LLM leg failed at round %s "
                "(decision path unaffected): %s: %s",
                round_id, type(exc).__name__, exc,
            )
            schema_valid = fingerprint_match = basis_subset = False
            llm_order = []
            usage = dict(self._capture.last_usage or {})
        template_order = [c.params["solvent"] for c in template_cands]
        store.append_event(
            "agent_shadow_proposal",
            {
                "round_id": round_id,
                "schema_valid": schema_valid,
                "fingerprint_match": fingerprint_match,
                "basis_subset": basis_subset,
                "order_diff": _order_diff(template_order, llm_order),
                "usage": usage,
                "prompt_sha256": _agent_prompt_sha256(kv),
                "validator_versions": list(SHADOW_VALIDATOR_VERSIONS),
            },
        )


class LLMStrategy:
    """``mode=llm``: the LLM proposal drives the decision. Reask exhaustion / provider death →
    empty proposal legal-quiet (the loop closes the round) plus one ``agent_generation_failed``
    event carrying the failure taxonomy."""

    mode = "llm"

    def __init__(
        self,
        llm: LLMBackend,
        holder: _KnowledgeHolder,
        capture: _CapturingCompletion,
        make_candidate: Callable[[str], Candidate | None],
    ) -> None:
        self._llm = llm
        self._holder = holder
        self._capture = capture
        self._make_candidate = make_candidate

    def decide(
        self, store: RunStore, round_id: int, view: KnowledgeView, template_cands: list[Candidate]
    ) -> list[Candidate]:
        self._holder.current = view
        self._capture.reset()
        pool = [c.params["solvent"] for c in template_cands]
        try:
            records = self._llm.propose_priors(
                store.export_view(), round_id, candidate_pool=pool,
            )
        except Exception as exc:  # noqa: BLE001 - provider death is legal-quiet, not a crash
            _log.warning(
                "agent(llm): proposal generation raised at round %s, treating as legal-quiet: "
                "%s: %s", round_id, type(exc).__name__, exc,
            )
            records = []
        if not records:
            self._emit_generation_failed(store, round_id, view)
            return []
        rec = records[0]
        llm_order = list(rec.content.get("candidates", []))
        cands: list[Candidate] = []
        for solvent in llm_order:
            cand = self._make_candidate(solvent)
            if cand is not None:  # drop hallucinated / unknown solvents, preserve order
                cands.append(cand)
        _record_llm_proposal(store, round_id, view, rec, [c.params["solvent"] for c in cands])
        return cands

    def _emit_generation_failed(
        self, store: RunStore, round_id: int, kv: KnowledgeView
    ) -> None:
        fails = self._llm.recent_failures()
        f = fails[0] if fails else None
        store.append_event(
            "agent_generation_failed",
            {
                "round_id": round_id,
                "failure_kind": f.reason if f is not None else "empty_proposal",
                "attempts": f.attempts if f is not None else 0,
                "usage": dict(self._capture.last_usage or {}),
                "prompt_sha256": _agent_prompt_sha256(kv),
            },
        )


def _record_llm_proposal(
    store: RunStore, round_id: int, kv: KnowledgeView, rec: DecisionRecord, cand_solvents: list[str]
) -> None:
    """Persist the LLM-driven proposal as a PRIOR_PROPOSAL decision, same content shape as the
    template proposal plus the mandatory ``usage`` block (方案 A). The frozen knowledge's own
    fingerprint is authoritative (the backend already validated the echo)."""
    store.append_decision(
        DecisionRecord(
            decision_id=f"prop_r{round_id}",
            round_id=round_id,
            actor=Actor.AGENT,
            kind=DecisionKind.PRIOR_PROPOSAL,
            content={
                "knowledge_fingerprint": kv.knowledge_fingerprint,
                "basis": sorted(rec.content.get("basis", [])),
                "candidates": list(cand_solvents),
                "usage": dict(rec.content.get("usage", {})),
            },
        )
    )


# ----------------------------------------------------------------- resolution (construction)


def resolve_agent_backend(
    spec: dict[str, Any] | None,
    *,
    record_proposal: Callable[..., None],
    make_candidate: Callable[[str], Candidate | None],
) -> TemplateStrategy | ShadowStrategy | LLMStrategy:
    """Resolve ``agent_backend`` into a strategy object ONCE, at construction.

    ``record_proposal(store, round_id, kv, cands)`` records the template proposal (byte-
    identical to the pre-M18 loop); ``make_candidate(solvent) -> Candidate | None`` maps an
    LLM-proposed solvent name back into a MCL ``Candidate`` (``None`` drops an unknown one).

    Loud-fails at construction on an invalid mode or an invalid provider route form (the
    route is pre-validated by ``LLMBackend.from_provider``; ``litellm/...`` is rejected there).
    """
    if spec is None:
        return TemplateStrategy(record_proposal)
    if not isinstance(spec, dict):
        raise AgentBackendError(
            f"agent_backend must be a dict or None, got {type(spec).__name__}"
        )
    mode = spec.get("mode", "template")
    if mode not in VALID_MODES:
        raise AgentBackendError(
            f"invalid agent_backend mode {mode!r}; expected one of {VALID_MODES}"
        )
    if mode == "template":
        return TemplateStrategy(record_proposal)

    # shadow / llm both need an LLM backend. Tests inject a ``completion_fn`` (zero network);
    # production passes a ``provider`` route string that from_provider pre-validates.
    completion_fn = spec.get("completion_fn")
    provider = spec.get("provider")
    if completion_fn is None and provider is None:
        raise AgentBackendError(
            f"agent_backend mode {mode!r} requires a 'provider' route string "
            "(e.g. 'openai/gpt-4o-mini') or an injected 'completion_fn'"
        )
    source = completion_fn if completion_fn is not None else LLMBackend.from_provider(provider)
    capture = _CapturingCompletion(source)
    holder = _KnowledgeHolder()
    llm = LLMBackend(
        capture,
        knowledge_provider=holder,
        model_id=spec.get("model_id", "injected"),
        provider=provider or "injected",
        seed=int(spec.get("seed", 7)),
        max_reasks=int(spec.get("max_reasks", 2)),
    )
    if mode == "shadow":
        return ShadowStrategy(record_proposal, llm, holder, capture)
    return LLMStrategy(llm, holder, capture, make_candidate)
