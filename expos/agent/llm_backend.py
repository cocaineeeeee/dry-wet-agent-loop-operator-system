"""LLM AgentBackend — proposal-only, model-agnostic, offline-testable (M18).

Design of record: r4_os_references/INDEX_M18_LLM_READY.md (Q1 route B = instructor-style
validate-and-reask; Q2 distributional G1; Q3 litellm string routing x dependency injection;
Q4 usage accounting into DecisionRecord.content).

Non-negotiable architecture constraints (user construction order, Phase 5):
  * TemplateBackend stays the production default; this backend is selected in only when a
    config explicitly asks for it (wiring belongs to the B domain / mcl.py).
  * This backend PRODUCES PROPOSALS ONLY. It satisfies the exact AgentBackend Protocol
    (backends.py) shape and holds NO write handle, no adjudication surface — Axiom 7 is a
    structural guard, so a buggy/hallucinating model still cannot touch the store.
  * Structured output = validate-and-reask (route B): every proposal is forced through a
    pydantic ``ProposalSchema`` whose ``knowledge_fingerprint`` is a REQUIRED field validated
    (via pydantic ``validation_context``) against the caller-supplied compiled knowledge — an
    LLM cannot invent a legal fingerprint. Retries happen model-side (reask) BEFORE any
    DecisionRecord is minted; on exhaustion the round is legal-quiet (empty proposal list)
    plus a structured failure record — never a fabricated proposal.
  * Model-agnostic two layers: litellm string routing (provider string is swappable) x
    dependency injection (a ``completion_fn`` callable is injected into the constructor — tests
    inject a mock callable, zero network, zero API key). ``from_provider`` is a convenience
    factory that lazily imports litellm and loud-fails if the library is absent; it is NEVER
    imported at module load.
  * usage accounting (token / latency / cost) rides in DecisionRecord.content["usage"].
    Registering it as an EVENT_PAYLOAD_REQUIRED key for agent-proposal events is a kernel/
    store.py (B domain) change — see the handoff doc; this module only emits the payload.

Structural boundary (Axiom 7): this module consumes only the read-only ReadOnlyRunView /
KnowledgeView value objects. It does NOT import RunStore's write methods, lifecycle
adjudication functions, adapters, planner, or models, and it defines no public
save/append/write/delete/update/remove API (gate test enforces this).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from expos.errors import ExposError
from expos.kernel.knowledge import KnowledgeView
from expos.kernel.objects import (
    Actor,
    DecisionKind,
    DecisionRecord,
    TrustLevel,
)
from expos.kernel.store import ReadOnlyRunView

# Free-text rationale is bounded so a chatty model cannot bloat the event log with an
# unbounded blob; the reasoning value is in the candidates/basis, not the prose (Q1: the
# "format tax" literature says proposal quality, not token-level legality, is what is scarce).
_RATIONALE_MAX = 1000
#: Bounded reask budget (N=2): up to two model-side re-asks after the first attempt, so at
#: most three round-trips before the round goes legal-quiet.
_DEFAULT_MAX_REASKS = 2

_LOG = logging.getLogger(__name__)


# ----------------------------------------------------------------- truth-isolation guard
#
# Design (M18 §0.2, the second non-negotiable guardrail): the ONLY things allowed onto the
# prompt-construction input face are the compiled KnowledgeView + candidate space + budget.
# The hidden truth surface lives exclusively inside the wet reader's server-side sidecar
# (adapters/wet/sim_reader.py: ``TRUTH_PROFILES`` / ``TruthSurface`` / the ``truth_records`` /
# ``true_response`` sidecar, drained only by the out-of-band scoring harness) and MUST NEVER
# reach an agent prompt — an agent that could see truth is no longer solving the closed loop.
#
# This is a *static structural* guard, not a semantic one: it scans the input object (recursing
# through pydantic dumps / mappings / sequences / plain-object ``__dict__``) and loud-fails the
# instant a truth-side MARKER KEY appears. It is deliberately NARROW — exact, case-insensitive
# KEY matching (never substring, never values) — so a legitimate KnowledgeView (whose keys are
# pv / n_hypotheses / n_claims / knowledge_fingerprint / hypotheses / hypothesis_id / statement /
# stored_status / effective_status / evidence / claim_id / status / signal) never trips it, while
# any leak of the reader's private naming trips it immediately. The blacklist is hard-coded here
# on purpose: importing it from the adapters package would (a) break the kernel/agent -> adapters
# layering red line and (b) make the guard's own import surface a truth vector. Keep it in sync
# with sim_reader by NAME, not by import.
#
# "删守卫必红" contract: dropping the ``_assert_prompt_isolated`` call from ``_render_prompt`` lets
# a truth-tainted input render silently — the negative tests in test_agent_truth_isolation.py go
# red, which is the whole point.
_FORBIDDEN_MARKER_KEYS: frozenset[str] = frozenset({
    "truth_profile",    # sim_reader ReaderState.truth_profile / measure-request selector
    "truth_profiles",   # sim_reader.TRUTH_PROFILES registry (peak-polarity table)
    "truth_surface",    # generic marker for the hidden response surface (M18-mandated)
    "truthsurface",     # sim_reader.TruthSurface dataclass, key-normalized
    "hidden_truth",     # generic marker for any hidden-truth payload (M18-mandated)
    "truth",            # sim_reader ReaderState.truth (the live TruthSurface instance)
    "true_response",    # sim_reader truth-sidecar record field (the un-noised ground truth)
    "truth_records",    # sim_reader ReaderState.truth_records (server-only sidecar list)
    "truth_dump",       # sim_reader admin command that reads the sidecar out
    "harvest_truth",    # sim_reader scoring-harness-only truth pull
    "save_truth",       # store-side opaque truth persistence (scoring harness only)
})


def _assert_prompt_isolated(obj: Any, *, where: str, _seen: set[int] | None = None) -> None:
    """Recursively assert that ``obj`` carries no truth-side marker key (see _FORBIDDEN_MARKER_KEYS).

    Walks pydantic models (via ``model_dump``), mappings, sequences and plain-object ``__dict__``
    graphs, guarding against cycles. Raises ``LLMBackendError`` (user_facing=False, so the CLI
    keeps the loud traceback) on the first offending KEY. Values are never inspected — only keys —
    so this cannot misfire on free-text hypothesis statements that merely mention the word."""
    seen = _seen if _seen is not None else set()
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)

    if isinstance(obj, BaseModel):
        _assert_prompt_isolated(obj.model_dump(), where=where, _seen=seen)
        return
    if isinstance(obj, Mapping):
        for key, val in obj.items():
            if isinstance(key, str) and key.strip().lower() in _FORBIDDEN_MARKER_KEYS:
                raise LLMBackendError(
                    f"truth-isolation violation while building {where}: input object carries the "
                    f"truth-side marker key {key!r}. The prompt input face admits only the "
                    "compiled KnowledgeView / candidate space / budget; the hidden truth surface "
                    "(sim_reader TRUTH_PROFILES / TruthSurface / truth sidecar) must never reach an "
                    "agent prompt."
                )
            _assert_prompt_isolated(val, where=where, _seen=seen)
        return
    if isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            _assert_prompt_isolated(item, where=where, _seen=seen)
        return
    # Plain object (dataclass instance, SimpleNamespace, ...): scan its attribute dict.
    attrs = getattr(obj, "__dict__", None)
    if attrs:
        _assert_prompt_isolated(dict(attrs), where=where, _seen=seen)


class LLMBackendError(ExposError):
    """Wiring/config misuse of the LLM backend (e.g. gated method called without compiled
    knowledge, or ``from_provider`` invoked with litellm absent). Programming/deployment bug,
    not a domain error — CLI must not swallow it."""

    user_facing = False


# ----------------------------------------------------------------- injected completion I/O


@dataclass(frozen=True)
class CompletionResult:
    """The narrow value a ``completion_fn`` returns: the raw model text plus optional usage
    accounting. Keeping this a plain dataclass (not a litellm ModelResponse) is what makes a
    mock callable a one-liner and keeps the core loop provider-agnostic."""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)


#: The injected model callable. Signature ``(messages, **kwargs) -> CompletionResult``. The
#: default production adapter is built by ``LLMBackend.from_provider``; tests inject a stub.
CompletionFn = Callable[..., CompletionResult]

#: A KnowledgeView, or a callable that compiles one from the current read-only view. The
#: caller (loop / mcl.py, B domain) owns knowledge compilation; the backend only consumes it.
KnowledgeProvider = KnowledgeView | Callable[[ReadOnlyRunView], KnowledgeView]


# ----------------------------------------------------------------- forced proposal schema


class ProposalSchema(BaseModel):
    """One knowledge-gated proposal the model must emit field-legally (Q1 route B).

    * ``candidates`` — the ranked candidate list (ORDER is load-bearing: the G1 discriminator
      reads candidate ordering as one axis that must shift when knowledge flips).
    * ``basis`` — claim_ids the proposal rests on; each must resolve to a claim present in the
      compiled knowledge (evidence-gating, checked against ``validation_context``).
    * ``knowledge_fingerprint`` — REQUIRED; must equal the compiled fingerprint the caller
      injected. An LLM cannot fabricate a legal fingerprint, so a missing/forged one is
      re-asked model-side and never becomes a proposal.
    * ``rationale`` — bounded free text.

    ``validation_context`` keys consumed: ``knowledge_fingerprint`` (str) and ``claim_ids``
    (a set/collection of in-ledger claim_ids). When no context is supplied (bare schema tests)
    the cross-field checks are skipped but the fields remain structurally required.
    """

    model_config = ConfigDict(extra="forbid")

    candidates: list[str] = Field(min_length=1)
    basis: list[str] = Field(default_factory=list)
    knowledge_fingerprint: str = Field(min_length=1)
    rationale: str = Field(default="", max_length=_RATIONALE_MAX)

    @model_validator(mode="after")
    def _gate_against_context(self, info: ValidationInfo) -> "ProposalSchema":
        ctx = info.context
        if not ctx:
            return self
        expected_fp = ctx.get("knowledge_fingerprint")
        if expected_fp is not None and self.knowledge_fingerprint != expected_fp:
            raise ValueError(
                "knowledge_fingerprint does not match the compiled knowledge "
                f"(got {self.knowledge_fingerprint!r}, expected {expected_fp!r}); "
                "an agent cannot mint knowledge it did not consume"
            )
        known = ctx.get("claim_ids")
        if known is not None:
            unknown = [c for c in self.basis if c not in known]
            if unknown:
                raise ValueError(
                    f"basis cites claim_ids absent from the compiled knowledge: {unknown} "
                    "(evidence-gating: proposals rest only on in-ledger claims)"
                )
        return self


class ProposalBatch(BaseModel):
    """Top-level parse target: the model returns ``{"proposals": [ProposalSchema, ...]}``.
    ``validation_context`` propagates to the nested per-proposal validators."""

    model_config = ConfigDict(extra="forbid")

    proposals: list[ProposalSchema] = Field(default_factory=list)


@dataclass(frozen=True)
class FailureRecord:
    """Structured record of one failed generation (reask exhaustion / provider outage).

    This is NOT a proposal and NOT an adjudication — it is a diagnostic the loop may fold into
    a failure event. The backend records it (see ``LLMBackend.recent_failures``) and returns an
    empty proposal list; it never writes anything itself."""

    round_id: int
    kind: str
    reason: str
    knowledge_fingerprint: str | None
    attempts: int
    detail: str = ""


# ----------------------------------------------------------------- the backend


class LLMBackend:
    """LLM-backed AgentBackend (satisfies the backends.AgentBackend Protocol).

    Construction:
      * ``completion_fn`` — injected model callable (REQUIRED for the LLM path). Build the
        production one with ``LLMBackend.from_provider("openai/gpt-4o-mini")`` (the string is a
        litellm ROUTE, e.g. ``openai/...``/``anthropic/...`` — a bare ``litellm/`` prefix is
        not a valid route; Stage-1 live caught exactly that); tests inject a stub.
      * ``knowledge_provider`` — a KnowledgeView or a ``view -> KnowledgeView`` callable. The
        knowledge-gated methods (suggest / propose_priors) require it; without it they loud-fail
        (a wiring bug, not a provider outage).
      * ``model_id`` / ``provider`` / ``seed`` — carried into usage accounting and passed to
        the completion callable (temperature is pinned to 0 for reproducibility, Q2 layer 1).
      * ``max_reasks`` — bounded reask budget (default N=2).

    The backend holds no store handle; every product is a returned DecisionRecord that the loop
    routes through kernel entry points (Axiom 7).
    """

    def __init__(
        self,
        completion_fn: CompletionFn,
        knowledge_provider: KnowledgeProvider | None = None,
        *,
        model_id: str = "injected",
        provider: str = "injected",
        seed: int = 7,
        temperature: float = 0.0,
        max_reasks: int = _DEFAULT_MAX_REASKS,
    ) -> None:
        if completion_fn is None:  # defensive: the whole point is the injected seam
            raise LLMBackendError("LLMBackend requires an injected completion_fn")
        self._complete = completion_fn
        self._knowledge = knowledge_provider
        self._model_id = model_id
        self._provider = provider
        self._seed = seed
        self._temperature = temperature
        self._max_reasks = max(0, int(max_reasks))
        #: Structured failures from the most recent generating call (drained by the loop into a
        #: diagnostic event — B domain wiring). Read-only accessor: ``recent_failures``.
        self._failures: list[FailureRecord] = []

    # -------------------------------------------------- litellm adapter factory (lazy)

    @staticmethod
    def from_provider(provider_string: str, **completion_kwargs: Any) -> CompletionFn:
        """Build a production ``completion_fn`` that routes through litellm.

        litellm is imported LAZILY here at CONSTRUCTION time — importing this module never
        touches litellm (offline/test paths inject their own stub and never reach here), but
        an invalid route now fails at assembly instead of mid-loop: Stage-1 live burned two
        calls discovering ``litellm/...`` is not a route, so the route is pre-validated with
        litellm's own pure-string resolver (INDEX_M18_LLMOPS #1 — fail-fast, whitelist tracks
        the pinned litellm version instead of being hand-copied here).
        """
        try:
            import litellm  # noqa: PLC0415  (deliberate lazy import — construction time)
        except ImportError as exc:  # pragma: no cover - exercised only where litellm absent
            raise LLMBackendError(
                "from_provider(...) needs the 'litellm' package, which is not installed; "
                "inject a completion_fn directly for offline/test use"
            ) from exc
        try:
            litellm.get_llm_provider(model=provider_string)  # pure string check, no network
        except Exception as exc:
            raise LLMBackendError(
                f"invalid litellm route {provider_string!r} (e.g. 'openai/gpt-4o-mini'; "
                f"'litellm/' is a library name, not a route): {exc}"
            ) from exc

        def _complete(messages: Sequence[dict[str, Any]], **kwargs: Any) -> CompletionResult:
            merged = {**completion_kwargs, **kwargs}
            # The provider string given to this factory is the single routing authority:
            # the backend also passes its model_id per-call (stubs record it), which would
            # otherwise collide with litellm.completion's own ``model`` kwarg.
            merged.pop("model", None)
            t0 = time.perf_counter()
            resp = litellm.completion(
                model=provider_string, messages=list(messages), **merged
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            text = resp.choices[0].message.content or ""
            usage = _extract_litellm_usage(resp, provider_string, elapsed_ms)
            return CompletionResult(text=text, usage=usage)

        return _complete

    # -------------------------------------------------- diagnostics (read-only)

    def recent_failures(self) -> list[FailureRecord]:
        """Structured failures from the most recent generating call (copy — no mutation)."""
        return list(self._failures)

    # -------------------------------------------------- AgentBackend Protocol surface

    def ingest(self, view: ReadOnlyRunView) -> None:
        # Stateless: evidence is read from the frozen view at each call (same discipline as
        # TemplateBackend — no accumulated memory that could drift between rounds).
        return None

    def suggest(
        self, view: ReadOnlyRunView, round_id: int, batch_size: int = 3
    ) -> list[DecisionRecord]:
        """Responsibility 4 (proposal half): knowledge-gated ACTION_PROPOSALs for this round's
        SUSPECT observations. Returns [] (legal-quiet) if the model produces nothing legal."""
        return self._generate(
            view, round_id, DecisionKind.ACTION_PROPOSAL, batch_size,
            task=self._suggest_task(view, round_id),
        )

    def propose_priors(
        self, view: ReadOnlyRunView, round_id: int,
        candidate_pool: Sequence[str] | None = None,
    ) -> list[DecisionRecord]:
        """Responsibility 2: knowledge-gated PRIOR_PROPOSALs.

        ``candidate_pool`` (optional, letter 103): the caller's legal candidate ids,
        rendered verbatim into the task so the model can only rank real candidates.
        Without it the model has no way to know the pool and its inventions are
        (correctly) filtered to an empty proposal downstream — Stage-3 live burned
        two quiet rounds discovering that. ``None`` keeps the old prompt byte-exact."""
        return self._generate(
            view, round_id, DecisionKind.PRIOR_PROPOSAL, batch_size=8,
            task=self._priors_task(view, candidate_pool),
        )

    def translate_goal(self, text: str, domain_names: list[str]) -> DecisionRecord:
        """Responsibility 1: NL goal -> GOAL_TRANSLATION. Not knowledge-consuming, so it does
        not carry the fingerprint gate; a parse failure yields a needs_clarification record
        (never raises — adjudication stays with planner/human)."""
        messages = [
            {"role": "system", "content": _GOAL_SYSTEM},
            {"role": "user", "content": json.dumps(
                {"goal": text, "domains": list(domain_names)}, ensure_ascii=False
            )},
        ]
        try:
            result = self._complete(
                messages=messages, model=self._model_id,
                temperature=self._temperature, seed=self._seed,
            )
            obj = _loads(result.text)
            content: dict[str, Any] = {
                "domain": obj.get("domain"),
                "direction": obj.get("direction"),
                "rounds": obj.get("rounds"),
                "usage": self._usage(result),
            }
            if not content["domain"]:
                content["needs_clarification"] = True
        except Exception as exc:  # noqa: BLE001 - translation must always yield a record
            content = {
                "domain": None,
                "direction": None,
                "rounds": None,
                "needs_clarification": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return DecisionRecord(
            round_id=0,
            actor=Actor.AGENT,
            kind=DecisionKind.GOAL_TRANSLATION,
            refs=[],
            content=content,
        )

    def narrate_round(
        self, view: ReadOnlyRunView, round_id: int, n_submitted: int = 0
    ) -> DecisionRecord:
        """Responsibility 4 (narration half): ROUND_RATIONALE. A soft text pass — on any model
        failure it falls back to a legal-quiet factual line rather than raising."""
        obs_round = [o for o in view.observations if o.round_id == round_id]
        n_trusted = sum(1 for o in obs_round if o.trust == TrustLevel.TRUSTED)
        n_suspect = sum(1 for o in obs_round if o.trust == TrustLevel.SUSPECT)
        narrative = (
            f"round {round_id}: {n_trusted} trusted, {n_suspect} suspect; "
            f"submitted {n_submitted} pending proposal(s)."
        )
        usage: dict[str, Any] = {}
        try:
            result = self._complete(
                messages=[
                    {"role": "system", "content": _NARRATE_SYSTEM},
                    {"role": "user", "content": narrative},
                ],
                model=self._model_id, temperature=self._temperature, seed=self._seed,
            )
            text = (result.text or "").strip()
            if text:
                narrative = text[:_RATIONALE_MAX]
            usage = self._usage(result)
        except Exception as exc:  # noqa: BLE001 - narration is best-effort, never fatal
            # No silent degradation (CONTRIBUTING §3): the factual fallback narrative is kept,
            # but the model failure is surfaced loudly on the log rather than swallowed.
            _LOG.warning(
                "narrate_round: model call failed (round %s), using factual fallback: %s: %s",
                round_id, type(exc).__name__, exc,
            )
        return DecisionRecord(
            round_id=round_id,
            actor=Actor.AGENT,
            kind=DecisionKind.ROUND_RATIONALE,
            refs=[o.obs_id for o in obs_round],
            content={
                "round_id": round_id,
                "n_trusted": n_trusted,
                "n_suspect": n_suspect,
                "n_submitted": n_submitted,
                "narrative": narrative,
                "usage": usage,
            },
        )

    def explain_verdict(self, view: ReadOnlyRunView, obs_id: str) -> str:
        """Responsibility 3: human-readable QC/attribution explanation. Best-effort text; on
        failure it returns a plain factual fallback (never raises)."""
        obs = next((o for o in view.observations if o.obs_id == obs_id), None)
        if obs is None:
            return f"observation {obs_id} is not present in the current view; cannot explain."
        fallback = f"observation {obs_id} verdicted {obs.trust.value}."
        try:
            result = self._complete(
                messages=[
                    {"role": "system", "content": _EXPLAIN_SYSTEM},
                    {"role": "user", "content": obs.model_dump_json()},
                ],
                model=self._model_id, temperature=self._temperature, seed=self._seed,
            )
            text = (result.text or "").strip()
            return text[:_RATIONALE_MAX] if text else fallback
        except Exception as exc:  # noqa: BLE001 - explanation is best-effort, never fatal
            _LOG.warning(
                "explain_verdict: model call failed for %s, using factual fallback: %s: %s",
                obs_id, type(exc).__name__, exc,
            )
            return fallback

    # -------------------------------------------------- core validate-and-reask loop

    def _generate(
        self,
        view: ReadOnlyRunView,
        round_id: int,
        kind: DecisionKind,
        batch_size: int,
        task: str,
    ) -> list[DecisionRecord]:
        """Bounded validate-and-reask loop (Q1 route B). Re-asks happen model-side, before any
        DecisionRecord is minted. Exhaustion / provider outage -> [] + a FailureRecord.

        The ``self._failures`` list is reset at the top of every generating call, so
        ``recent_failures`` always reflects only this call."""
        self._failures = []
        kv = self._resolve_knowledge(view)
        ctx = _validation_context(kv)
        messages = [
            {"role": "system", "content": _PROPOSAL_SYSTEM},
            {"role": "user", "content": self._render_prompt(kv, task)},
        ]

        last_detail = ""
        for attempt in range(self._max_reasks + 1):
            try:
                result = self._complete(
                    messages=messages, model=self._model_id,
                    temperature=self._temperature, seed=self._seed,
                )
            except Exception as exc:  # noqa: BLE001 - provider outage -> legal-quiet
                self._failures.append(FailureRecord(
                    round_id=round_id, kind=kind.value, reason="provider_error",
                    knowledge_fingerprint=kv.knowledge_fingerprint,
                    attempts=attempt + 1, detail=f"{type(exc).__name__}: {exc}",
                ))
                return []

            try:
                obj = _loads(result.text)
                batch = ProposalBatch.model_validate(obj, context=ctx)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
                # Reask model-side: append the validation error so the next round-trip can fix
                # it. The frozen knowledge is re-injected implicitly (ctx is unchanged) — no
                # knowledge drift across reasks (INDEX item 8).
                messages = messages + [
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": (
                        "Validation error found:\n" + last_detail
                        + "\nReturn corrected JSON matching the schema. Every proposal MUST "
                        f"carry knowledge_fingerprint == {kv.knowledge_fingerprint!r} and a "
                        "basis of in-ledger claim_ids only."
                    )},
                ]
                continue

            usage = self._usage(result)
            return [
                self._to_decision(p, round_id, kind, usage)
                for p in batch.proposals[:batch_size]
            ]

        # Reask budget exhausted -> legal-quiet + structured failure (never fabricate).
        self._failures.append(FailureRecord(
            round_id=round_id, kind=kind.value, reason="reask_exhausted",
            knowledge_fingerprint=kv.knowledge_fingerprint,
            attempts=self._max_reasks + 1, detail=last_detail,
        ))
        return []

    def _to_decision(
        self, p: ProposalSchema, round_id: int, kind: DecisionKind, usage: dict[str, Any]
    ) -> DecisionRecord:
        return DecisionRecord(
            round_id=round_id,
            actor=Actor.AGENT,
            kind=kind,
            refs=list(p.basis),
            content={
                "candidates": list(p.candidates),
                "basis": list(p.basis),
                "knowledge_fingerprint": p.knowledge_fingerprint,
                "rationale": p.rationale,
                "usage": usage,
            },
        )

    def _resolve_knowledge(self, view: ReadOnlyRunView) -> KnowledgeView:
        kp = self._knowledge
        if kp is None:
            raise LLMBackendError(
                "LLMBackend knowledge-gated methods require a knowledge_provider "
                "(a KnowledgeView or a view->KnowledgeView callable) — the caller compiles "
                "and injects the frozen knowledge; the backend never invents a fingerprint"
            )
        kv = kp(view) if callable(kp) else kp
        if not isinstance(kv, KnowledgeView):
            raise LLMBackendError(
                f"knowledge_provider must resolve to a KnowledgeView, got {type(kv).__name__}"
            )
        return kv

    def _usage(self, result: CompletionResult) -> dict[str, Any]:
        """Merge the completion's usage with routing metadata (Q4). Present even when the
        injected callable reports nothing, so the ``usage`` key is always populated."""
        return {
            "model_id": self._model_id,
            "provider": self._provider,
            "seed": self._seed,
            **(result.usage or {}),
        }

    # -------------------------------------------------- prompt rendering

    def _render_prompt(self, kv: KnowledgeView, task: str) -> str:
        """Render the frozen knowledge + task into the user message. The knowledge_fingerprint
        and per-hypothesis effective status are rendered verbatim so (a) the model is told the
        exact fingerprint it must echo and (b) a knowledge-conditioned mock can branch on it —
        this is the substrate the G1 distributional discriminator reads."""
        # Truth-isolation guard (M18 §0.2): before ANY bytes of the knowledge prompt are
        # assembled, structurally reject an input face that smuggles a truth-side marker key.
        # This is the single choke point every knowledge-gated prompt (suggest / propose_priors)
        # flows through; deleting this call is what the negative isolation tests catch.
        _assert_prompt_isolated(kv, where="knowledge prompt")
        hyps = [
            {
                "hypothesis_id": h.hypothesis_id,
                "statement": h.statement,
                "effective_status": h.effective_status.value,
                "claim_ids": [e.claim_id for e in h.evidence],
            }
            for h in kv.hypotheses
        ]
        payload = {
            "task": task,
            "knowledge_fingerprint": kv.knowledge_fingerprint,
            "claim_ids": sorted({e.claim_id for h in kv.hypotheses for e in h.evidence}),
            "hypotheses": hyps,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _suggest_task(self, view: ReadOnlyRunView, round_id: int) -> str:
        suspect = [
            o.obs_id for o in view.observations
            if o.trust == TrustLevel.SUSPECT and o.round_id == round_id
        ]
        return (
            "Propose next-round actions for this round's SUSPECT observations "
            f"{suspect}. Rank candidates; cite the claim_ids your ranking rests on."
        )

    def _priors_task(
        self, view: ReadOnlyRunView, candidate_pool: Sequence[str] | None = None
    ) -> str:
        domains = sorted({e.domain for e in view.experiments})
        base = (
            f"Propose priors for the design spaces of domains {domains}. "
            "Rank candidate prior settings; cite supporting claim_ids."
        )
        if candidate_pool is None:
            return base
        return base + (
            f" The ONLY legal candidates are {sorted(candidate_pool)}; put them in "
            "'candidates' verbatim (exact strings, no invented names), ordered from "
            "most to least promising under the given knowledge."
        )


# ----------------------------------------------------------------- module helpers


def _validation_context(kv: KnowledgeView) -> dict[str, Any]:
    """Build the pydantic validation context from compiled knowledge: the exact fingerprint the
    model must echo and the set of in-ledger claim_ids the basis may cite."""
    return {
        "knowledge_fingerprint": kv.knowledge_fingerprint,
        "claim_ids": frozenset(e.claim_id for h in kv.hypotheses for e in h.evidence),
    }


def _loads(text: str) -> Any:
    """Parse model text as JSON, tolerating a leading/trailing ```json fence some providers add."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -len("```")]
        s = s.removeprefix("json").strip()
    return json.loads(s)


def _extract_litellm_usage(resp: Any, provider_string: str, elapsed_ms: float) -> dict[str, Any]:
    """Map a litellm ModelResponse's usage/latency/cost into a plain dict (Q4). Defensive: any
    missing attribute degrades to None rather than raising inside the adapter."""
    u = getattr(resp, "usage", None)
    hidden = getattr(resp, "_hidden_params", {}) or {}
    # Key names follow the gen_ai.* semantic convention where one exists (letter 094 §1:
    # prompt_tokens/completion_tokens are the DEPRECATED forms; input_tokens/output_tokens
    # are canonical, system_fingerprint maps to gen_ai.openai.system_fingerprint).
    # cost/latency/route have no convention name -> self-owned keys, kept.
    return {
        "input_tokens": getattr(u, "prompt_tokens", None),
        "output_tokens": getattr(u, "completion_tokens", None),
        "total_tokens": getattr(u, "total_tokens", None),
        "system_fingerprint": getattr(resp, "system_fingerprint", None),
        "response_ms": getattr(resp, "_response_ms", None) or elapsed_ms,
        "response_cost": hidden.get("response_cost"),
        "route": provider_string,
    }


_PROPOSAL_SYSTEM = (
    "You are a proposal-only agent in a closed-loop materials experiment OS. You have NO "
    "authority to adjudicate or write anything. Return ONLY JSON of the form "
    '{"proposals": [{"candidates": [...], "basis": [...], "knowledge_fingerprint": "...", '
    '"rationale": "..."}]}. Every proposal MUST echo the exact knowledge_fingerprint given to '
    "you and cite only claim_ids present in the provided knowledge. Do not invent claim_ids or "
    "fingerprints."
)
_GOAL_SYSTEM = (
    "Translate the natural-language goal into JSON "
    '{"domain": <one of the given domains or null>, "direction": "maximize"|"minimize"|null, '
    '"rounds": <int or null>}. Return ONLY that JSON.'
)
_NARRATE_SYSTEM = (
    "Rewrite the given round summary as one concise, factual sentence. Invent no numbers."
)
_EXPLAIN_SYSTEM = (
    "Explain, in plain language, why this observation received its verdict, citing only the QC "
    "checks and attribution present in the given record. Invent nothing."
)
