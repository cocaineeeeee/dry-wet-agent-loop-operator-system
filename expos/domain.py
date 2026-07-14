"""域配置装配（docs/ARCHITECTURE.md §3）：YAML → DomainConfig → adapter。

这是"换域只换配置"的接线点：crystal ↔ coating 的切换只发生在这里与 domains/*.yaml，
内核零改动。校验一律响亮失败：未知配置键、未知 adapter、拼错的注入器名
都在**加载期**就炸，不留到运行期。
"""

from __future__ import annotations

import hashlib
import importlib
import json
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids a load-time import cycle
    from expos.adapters.domain_provider import DomainProvider

from expos.adapters.artifacts import validate_scenario
from expos.adapters.base import AdapterError, ExecutionAdapter
from expos.adapters.bench_manual import BenchManualAdapter
from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.sequence_adapter import SequenceProxyAdapter
from expos.adapters.sim_coating import CoatingSim
from expos.adapters.sim_crystal import CrystalSim
from expos.kernel.objects import Constraint, DesignSpace, Objective


from expos.errors import ExposError


class DomainError(ExposError):
    pass


#: M23 Phase 0 controlled unit vocabulary (REF-U §Convergence(b)(2); astropy
#: ``parse_strict="raise"`` posture). Units are DATA (pint ``default_en.txt`` "units
#: are a loadable table" stance) but we keep ONLY the small honest set expos actually
#: puts in play on the METRIC face -- seeded from the repo's live usage:
#:   * ``arbitrary_unit`` -- plate-reader response a.u. (solvent_response / catalyst_yield)
#:   * ``debye``          -- molecular dipole magnitude (polarity_proxy / reactivity_proxy)
#:   * ``dimensionless``  -- normalized / unitless metrics (posture C spec-convention)
#:   * ``celsius``        -- temperature; an OFFSET unit, present so it is a *nameable*
#:                          vocabulary member, NOT so it may be converted (see
#:                          :func:`check_unit_consistency` -- strict equality, never a
#:                          factor; pint ``default_en.txt`` degree_Celsius offset 273.15).
#:   * ``microliter``     -- wet-leg liquid volume (the real-instrument high-risk face).
#: A declared metric unit outside this set is a LOUD load-time error (T1), never a
#: silent free string (REF-U reject #4). This is the METRIC-unit vocabulary only;
#: design-space CONDITION units (``VariableDef.unit``: "mM"/"C"/"min"/"mol%"/"S"/...)
#: are a separate face, deliberately NOT validated against this set here, so every
#: shipped yaml loads byte-unchanged (the M23 Phase 0 hard gate). Extend this set
#: additively as new honest metric units enter play.
UNIT_VOCABULARY: frozenset[str] = frozenset(
    {
        "arbitrary_unit",
        "debye",
        "dimensionless",
        "celsius",
        "microliter",
    }
)


def check_unit_consistency(
    observed_unit: str | None,
    declared_unit: str | None,
    *,
    metric: str | None = None,
) -> None:
    """Ingest-side dimension-mismatch guard (REF-U §Convergence(c) T2/T3/T4).

    Compare an observation's carried unit (``MeasuredResult.unit``) against the
    domain's declared canonical unit for that metric (``DomainConfig.metric_units``)
    BEFORE any cross-record compare/aggregate. This is the Mars-Climate-Orbiter
    guard: a ``debye`` value must never be silently treated as a ``microliter`` one.

    Semantics -- STRICT EQUALITY ONLY (this is load-bearing):
      * ``declared_unit is None`` -> the metric declares no canonical unit, so there
        is no requirement; return (legacy metrics stay unit-free).
      * declared set, observed missing/empty -> LOUD (T4): a declared unit is a
        requirement; a real-instrument leg that drops its unit is refused, never
        defaulted/guessed (REF-U reject: no "guess unit / default to SI" path).
      * both set, unequal -> LOUD (T2): different units do NOT compare.
      * both set, equal -> pass.

    !! DO NOT add a conversion path to this function. It ONLY tests equality; it
    never multiplies by a factor nor applies an offset. ``celsius`` is an OFFSET unit
    (25 C is NOT 25*k K -- pint ``default_en.txt`` degree_Celsius offset 273.15), so
    ANY "convert observed to declared" scalar-factor layer silently corrupts
    temperature (REF-U signal 1.2 / reject #3). If a conversion is ever genuinely
    needed it lives in an EXPLICIT, named, boundary-layer pure function with its own
    offset-unit handling -- never inside this equality guard. The T3 test pins this:
    it asserts this function carries NO conversion parameter and REFUSES (does not
    convert) a mismatch. Anyone adding a scalar-factor conversion here breaks that
    test's intent by design.
    """
    if declared_unit is None:
        return
    if declared_unit not in UNIT_VOCABULARY:
        raise DomainError(
            (f"metric {metric!r}: " if metric else "")
            + f"declared unit {declared_unit!r} is not in the controlled unit "
            f"vocabulary {sorted(UNIT_VOCABULARY)}"
        )
    observed = (observed_unit or "").strip()
    if not observed:
        raise DomainError(
            (f"metric {metric!r}: " if metric else "")
            + f"observation carries no unit but the domain declares a required unit "
            f"{declared_unit!r}; a missing unit on a declared-unit metric is refused, "
            "never defaulted or guessed"
        )
    if observed != declared_unit:
        raise DomainError(
            (f"metric {metric!r}: " if metric else "")
            + f"observed unit {observed!r} != declared unit {declared_unit!r}; units "
            "of different dimension do not compare (no implicit conversion)"
        )


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 未知键响亮失败


class PlateSpec(_Cfg):
    rows: int
    cols: int


class SentinelSpec(_Cfg):
    n: int = 5
    params: dict[str, Any]
    expected_band: tuple[float, float] | None = None


class BudgetSpec(_Cfg):
    wells_total: int = Field(gt=0)   # 预算须为正（J-3：0/负预算静默接受→空跑）
    rounds_total: int = Field(gt=0)


class TrustSpec(_Cfg):
    suspect_high: float = 0.6
    quarantine_low: float = 0.3

    @model_validator(mode="after")
    def _ordered(self) -> "TrustSpec":
        # J-3：阈值倒置（suspect_high<quarantine_low）会直接改写裁决方向，
        # 全干净观测被判 SUSPECT——加载期响亮拒绝，不留到运行期。
        if not (0.0 < self.quarantine_low < self.suspect_high < 1.0):
            raise ValueError(
                "trust 阈值须满足 0 < quarantine_low < suspect_high < 1，"
                f"实得 quarantine_low={self.quarantine_low}, suspect_high={self.suspect_high}"
            )
        return self


class ControlSpec(_Cfg):
    """A domain-declared assay control (M24 bio ruling ①; additive-optional, absent =>
    NO controls, byte-identical chemistry plate). The wet leg lays these into the plate
    layout and hands them to the readout normalization layer (percent-of-control needs a
    positive + negative baseline). ``role`` is the neutral control class:

      * ``negative`` / ``positive`` -> the native kernel ``Control.kind`` of the same name;
      * ``reference`` -> ``kind=sentinel`` + ``params.semantic_role="reference"`` (mcl adds
        the marker), so a calibration reference needs NO new kernel ``Control.kind``.

    The kernel/ledger/certification learn no biology from this — a control is an observation
    baseline, NOT a new certification rule. ``params`` are the control's experimental
    parameters (e.g. its screening-level, or a no-template marker); they are experimental
    INPUTS, never truth."""

    control_id: str
    role: Literal["negative", "positive", "reference"]
    params: dict[str, Any] = Field(default_factory=dict)


class SeedClaimSpec(_Cfg):
    """M20 seed-claim neutralization: a domain-declared seed claim. When a domain
    carries a ``seed_claims`` block the run's seed ledger AND its posed hypotheses
    derive from it instead of the built-in ``c_polar`` family, so the loop holds no
    domain-specific claim literal. ``direction`` ('higher'|'lower') is which
    acquisition direction the claim asserts, consumed by mcl to steer the
    preference. Additive-optional (absent => legacy c_polar behaviour)."""

    claim_id: str
    statement: str = ""
    status: str
    direction: str

    @model_validator(mode="after")
    def _checks(self) -> "SeedClaimSpec":
        if self.direction not in ("higher", "lower"):
            raise ValueError(
                f"seed_claims[{self.claim_id!r}].direction must be 'higher' or 'lower', "
                f"got {self.direction!r}"
            )
        return self


# ---- M20 domain-contract v2 schema additions (case 1; all additive-optional, so
# every existing yaml that omits these blocks stays byte-valid unchanged and the
# legacy behaviour is byte-identical). These declare the "scientific honesty" half
# of the domain contract (REF-M §a / REF-P2 §Convergence(a)): a controlled metric
# vocabulary, an explicit execution-kind enum (replaces class-identity dispatch),
# the observation channels a domain emits, and the acceptance-face debt ledger.
# The machine reconcile of these declarations against the live registries lives in
# scripts/expos_lint.py EXP013 (preview tier) -- pydantic does the structural half
# here (loud at load), the lint does the cross-registry half.


class ExecutionKind(str, Enum):
    """How a domain's named ``adapter`` (the ``adapter:`` field) is executed -- an
    explicit domain declaration that replaces the class-identity dispatch
    (``if cls is PySCFDryAdapter`` in :func:`build_adapter`) with a declared enum
    (REF-P2 §3.4 / home-assistant ``iot_class`` precedent). Additive-optional
    (absent => the legacy class-identity dispatch, byte-identical).

      * ``dry_compute`` -- async job-shaped compute leg (the dual-leg dry PySCF
        leg, ``pyscf_dry``); driven by ``--loop mcl`` and NEVER through
        ``build_adapter`` (the async guard). The wet assay rides out-of-band.
      * ``wet_assay``   -- a synchronous wet-assay ``ExecutionAdapter``.
      * ``sim_execute`` -- a synchronous in-silico simulator ``ExecutionAdapter``
        (``sim_crystal`` / ``sim_coating`` / ``bench_manual``).

    EXP013 clause 2 reconciles the declared kind against real dispatch: a
    ``dry_compute`` domain's ``build_adapter`` must fail loud (async guard); a
    synchronous kind's must succeed.
    """

    dry_compute = "dry_compute"
    wet_assay = "wet_assay"
    sim_execute = "sim_execute"


class ObservableSpec(_Cfg):
    """One observation channel a domain emits (REF-M §a observables / nf-core
    meta.yml output four-tuple). ``metric`` must be a member of the domain's
    ``metrics`` controlled vocabulary when that block is present (validated on
    :class:`DomainConfig`). ``note`` records honest scope debt in-band -- e.g. a
    dry-leg metric label that is carried on the wet observation's secondary channel
    and is not yet a first-class objective metric."""

    name: str
    metric: str
    description: str = ""
    note: str = ""


class AcceptanceFaceSpec(_Cfg):
    """A declared discriminative acceptance face (REF-M §a acceptance_faces /
    galaxy ``<tests>`` precedent) -- THE machine-debt ledger that turns the
    flipped/flat "TODO" letter 110 deferred into a recorded, lint-checked debt.

      * ``face_name``    -- the domain-facing face label (e.g. ``polar_high``).
      * ``truth_profile``-- the hidden reader face it selects; must exist in the
        reader's ``TRUTH_PROFILES`` registry (checked by EXP013 clause 3, not here,
        so this module stays free of an adapter import).
      * ``status``       -- ``landed`` (a real discriminative test backs it) or
        ``declared`` (committed to, but its test is not yet written -- a recorded
        debt, e.g. ``catalyst_low``).
      * ``test_anchor``  -- ``file::test_id`` (EXP012 anchor form). A ``landed``
        face MUST carry one (enforced here); a ``declared`` face may be null. EXP013
        clause 4 verifies a landed face's anchor actually exists.
    """

    face_name: str
    truth_profile: str
    status: Literal["declared", "landed"]
    test_anchor: str | None = None

    @model_validator(mode="after")
    def _landed_needs_anchor(self) -> "AcceptanceFaceSpec":
        if self.status == "landed" and not self.test_anchor:
            raise ValueError(
                f"acceptance_faces[{self.face_name!r}] status=landed requires a "
                "non-null test_anchor (file::test_id); a landed face without a "
                "backing test is not landed"
            )
        return self


class DomainConfig(_Cfg):
    name: str
    adapter: str
    objective: Objective
    design_space: DesignSpace
    restrictions: list[Constraint] = Field(default_factory=list)
    plate: PlateSpec
    replicates: int = Field(default=2, gt=0)   # J-3：副本数须为正
    # ---- M24 replicate-independence declaration (bio ruling ③; additive-optional,
    # absent/None => LEGACY behaviour, byte-identical: every replicate enters the
    # evidence compiler as an independent observation — the chemistry regression
    # anchor). Semantics are domain-neutral: ``technical`` = the SAME experimental
    # unit re-measured (repeated pipetting/reading of one sample), so its replicates
    # are CORRELATED and must be collapsed by the upstream QC layer into ONE
    # independent observation before certification, or the e-product OVER-estimates
    # the information (false decisive). ``biological`` = INDEPENDENT units (separate
    # prep/reaction), which ARE independent evidence and reach the compiler at full
    # n. The kernel/compiler learn no biology from this — the flag only steers the
    # qc-layer collapse (:func:`expos.qc.replicate_collapse.collapse_technical_replicates`)
    # that feeds the compiler the correct independent-unit count. The Literal makes an
    # unknown value a loud load-time ValidationError.
    replicate_kind: Literal["technical", "biological"] | None = None
    # ---- M24 assay controls (bio ruling ①; additive-optional, absent/None => NO controls,
    # byte-identical: every chemistry domain declares none and its wet plate is unchanged).
    # A biological domain declares its negative/positive/reference trio here; the mcl wet leg
    # (:func:`expos.mcl._domain_controls`) turns them into kernel ``Control`` objects and lays
    # them into the plate + the readout normalization layer. ZERO kernel change: the roles map
    # onto existing ``Control.kind`` values (reference rides sentinel + a params marker).
    controls: list[ControlSpec] | None = None
    sentinel: SentinelSpec
    metric_range: tuple[float, float] = (0.0, 1.2)
    simulator: dict[str, Any] = Field(default_factory=dict)
    budget: BudgetSpec
    trust: TrustSpec = Field(default_factory=TrustSpec)
    # ---- M20 seed-claim neutralization (additive-optional; absent => mcl legacy
    # c_polar family, byte-identical). A domain-declared ``seed_claims`` block makes
    # the run's seed ledger + posed hypotheses its own claim family, so the loop
    # holds no domain-specific claim literal. The per-variable ``descriptors``
    # mechanism (categorical level -> physical coordinate) lives on
    # ``DesignSpace``'s ``VariableDef`` (INDEX_M19_DOMAIN2 §5), NOT here.
    seed_claims: list[SeedClaimSpec] | None = None
    # ---- M20 domain-contract v2 (additive-optional; absent => legacy, byte-identical).
    # ``metrics``: the domain's controlled metric vocabulary (borrowed from ASE's
    # ``all_properties`` shared-vocabulary discipline, REF-P2 §2.1). When present,
    # ``objective.metric`` and every ``observables[*].metric`` MUST be a member
    # (validated below, loud). ``execution_kind``: the dispatch-kind declaration
    # (see :class:`ExecutionKind`). ``observables``: the observation channels the
    # domain emits. ``acceptance_faces``: the discriminative-face debt ledger.
    metrics: list[str] | None = None
    execution_kind: ExecutionKind | None = None
    observables: list[ObservableSpec] | None = None
    acceptance_faces: list[AcceptanceFaceSpec] | None = None
    # ---- M23 Phase 0 per-metric unit declaration (REF-U §Convergence(b)(2);
    # additive-optional, absent => byte-identical, no unit declared or enforced). A
    # PARALLEL ``{metric_name: unit}`` map rather than promoting ``metrics`` entries
    # to ``{name, unit}`` objects: the parallel map keeps ``metrics: [a, b]`` a pure
    # ``list[str]``, so every shipped yaml (which declares ``metrics`` as a bare list)
    # loads byte-unchanged -- the hard gate. A promoted union type would force the
    # existing bare-list yamls to change, which the gate forbids. Each key MUST be a
    # member of ``metrics`` (a unit for an undeclared metric is loud) and each value
    # MUST be in :data:`UNIT_VOCABULARY` (unknown unit => loud, T1; validated below).
    # Declaring a unit for a metric here IS that metric's unit REQUIREMENT: the ingest
    # cross-check :func:`check_unit_consistency` treats a declared-but-missing observed
    # unit as a loud failure (T4). Absent unit => no requirement (legacy metrics stay
    # free). A codes both flagship domains' ``metric_units`` against THIS shape
    # (catalyst_yield/solvent_response -> arbitrary_unit; reactivity_proxy/
    # polarity_proxy -> debye).
    metric_units: dict[str, str] | None = None
    # ---- M21 domain-provider wiring (additive-optional; absent => no provider is
    # loaded and behaviour is byte-identical). ``provider`` is a dotted
    # ``<module_path>:<ClassName>`` locator for the domain's
    # :class:`expos.adapters.domain_provider.DomainProvider` (mailbox 120 consumption
    # spec). When present, ``load_domain`` imports it, runs its birth-time
    # ``check_complete`` governance, and calls ``validate_yaml(self)`` -- and its
    # source-hash ``provider_fingerprint`` is folded into ``config_fingerprint`` so a
    # domain-implementation drift trips resume drift-rejection. The loaded instance is
    # attached as the private ``_provider`` attribute (non-serialised, so model_dump
    # and the fingerprint base material are unchanged for provider-less domains).
    provider: str | None = None
    _provider: "DomainProvider | None" = PrivateAttr(default=None)

    @field_validator("provider")
    @classmethod
    def _provider_format(cls, v: str | None) -> str | None:
        # Format is loud at load: exactly one ':' separating a non-empty module path
        # from a non-empty class name. The module-path-must-start-with-'expos.' and
        # importability checks live in ``load_provider`` (they need the import machinery,
        # not just the string), but a malformed locator is rejected here.
        if v is None:
            return v
        module_path, sep, class_name = v.partition(":")
        if sep != ":" or ":" in class_name or not module_path or not class_name:
            raise ValueError(
                f"provider must be '<module_path>:<ClassName>' with exactly one ':', "
                f"got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _metric_range_ordered(self) -> "DomainConfig":
        # J-3：metric_range 上下界倒置（lo≥hi）会污染任何按区间的标定——加载期拒绝。
        lo, hi = self.metric_range
        if not lo < hi:
            raise ValueError(f"metric_range 须满足 lo < hi，实得 [{lo}, {hi}]")
        return self

    @model_validator(mode="after")
    def _metrics_vocabulary(self) -> "DomainConfig":
        # M20 REF-M M2 (declaration must reconcile with what is emitted): when a
        # domain declares a ``metrics`` controlled vocabulary, the objective metric
        # and every declared observable channel's metric MUST be members -- a metric
        # label outside the domain's own vocabulary is a loud load-time error, never
        # a silent free string.
        if self.metrics is not None:
            if len(set(self.metrics)) != len(self.metrics):
                raise ValueError(f"metrics 词表含重复项: {self.metrics}")
            vocab = set(self.metrics)
            if self.objective.metric not in vocab:
                raise ValueError(
                    f"objective.metric={self.objective.metric!r} 不在 metrics 词表 "
                    f"{sorted(vocab)}（受控词表须含目标度量）"
                )
            for obs in self.observables or []:
                if obs.metric not in vocab:
                    raise ValueError(
                        f"observables[{obs.name!r}].metric={obs.metric!r} 不在 metrics "
                        f"词表 {sorted(vocab)}（声明的观测通道度量须在受控词表内）"
                    )
        return self

    @model_validator(mode="after")
    def _metric_units_vocabulary(self) -> "DomainConfig":
        # M23 Phase 0 (REF-U §Convergence(c) T1): a declared per-metric unit must
        # (a) name a metric that is in this domain's controlled ``metrics``
        # vocabulary, and (b) be a member of the controlled UNIT_VOCABULARY. An
        # unknown unit is a LOUD load-time error, never a silent free string
        # (astropy ``parse_strict="raise"``). load_domain wraps the resulting
        # ValidationError as a DomainError, so the failure surfaces loud at load.
        if self.metric_units is None:
            return self
        if self.metrics is None:
            raise ValueError(
                "metric_units declared but no metrics vocabulary is present; a unit "
                "can only be attached to a declared metric"
            )
        vocab = set(self.metrics)
        for metric, unit in self.metric_units.items():
            if metric not in vocab:
                raise ValueError(
                    f"metric_units[{metric!r}] names a metric outside the domain "
                    f"metrics vocabulary {sorted(vocab)}（单位只能挂在已声明的度量上）"
                )
            if unit not in UNIT_VOCABULARY:
                raise ValueError(
                    f"metric_units[{metric!r}] unit {unit!r} 不在受控单位词表 "
                    f"{sorted(UNIT_VOCABULARY)}（未知单位是加载期响亮错误，非静默自由字符串）"
                )
        return self


# NOTE (dimw5_handoff §1): ``pyscf_dry`` (the M16 dry screening leg) is registered
# so ``load_domain`` accepts ``domains/solvent_screen.yaml`` (its metric cross-check
# no-ops: the adapter has no ``default_metric``/``required_params`` and the empty
# ``simulator: {}`` carries no metric). It is async-job-shaped, NOT the synchronous
# ``ExecutionAdapter.execute`` protocol, so ``build_adapter(cfg)`` must NEVER be
# called for it — the ``--loop mcl`` driver (expos.mcl) bypasses ``build_adapter``
# and drives both legs through the W5 glue (build_adapter fails loud for it below).
ADAPTER_REGISTRY: dict[str, type] = {
    "sim_crystal": CrystalSim,
    "sim_coating": CoatingSim,
    "bench_manual": BenchManualAdapter,
    "pyscf_dry": PySCFDryAdapter,
    # M24: the biological dry leg. Like ``pyscf_dry`` it is a DUAL-LEG dry adapter driven
    # out-of-band by ``--loop mcl`` (NEVER through build_adapter -- see the guard below and
    # ``_DUAL_LEG_DRY_ADAPTERS``); it is synchronous/in-process (no PySCF/subprocess/sbatch).
    # Registering it lets ``load_domain(domains/cell_free_expression_screen.yaml)`` pass the
    # adapter gate, the exact staging precedent solvent/catalyst sat in.
    "sequence_proxy": SequenceProxyAdapter,
}

#: The DUAL-LEG dry-compute adapters (M16/M24): they screen the dry leg of the
#: ``--loop mcl`` pipeline and are DRIVEN out-of-band by ``expos.mcl`` (which selects them
#: by Contract-v3 capability), NEVER built through :func:`build_adapter`. Two consequences,
#: both mirroring the original ``pyscf_dry`` staging (dimw5_handoff §1):
#:   * ``build_adapter`` fails LOUD for them (their ctor is not the synchronous single-leg
#:     ``ExecutionAdapter(cfg.simulator)`` shape; EXP013 clause 2 also requires a
#:     ``dry_compute`` domain's build_adapter to fail loud);
#:   * the load-time ``objective.metric == adapter.default_metric`` cross-check is SKIPPED
#:     for them: the objective is the WET metric, while the dry adapter emits a SEPARATE
#:     dry-channel metric (``expression_proxy`` / dipole) consumed only by mcl's dry leg, so
#:     the two legitimately differ (``pyscf_dry`` sidesteps this by declaring no
#:     ``default_metric``; ``sequence_proxy`` declares one, so the skip must be explicit).
_DUAL_LEG_DRY_ADAPTERS: frozenset[type] = frozenset({PySCFDryAdapter, SequenceProxyAdapter})


def load_domain(path: str | Path) -> DomainConfig:
    p = Path(path)
    if not p.exists():
        raise DomainError(f"域配置不存在: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise DomainError(f"域配置 YAML 解析失败: {p}: {e}") from e
    if not isinstance(raw, dict):
        raise DomainError(f"域配置必须是映射: {p}")
    try:
        cfg = DomainConfig.model_validate(raw)
    except ValidationError as e:
        raise DomainError(f"域配置校验失败: {p}:\n{e}") from e
    if cfg.adapter not in ADAPTER_REGISTRY:
        raise DomainError(
            f"未知 adapter: {cfg.adapter!r}（可用: {sorted(ADAPTER_REGISTRY)}）"
        )
    try:
        validate_scenario(cfg.simulator.get("artifact_scenario"))
    except AdapterError as e:
        raise DomainError(f"域配置 artifact_scenario 非法: {e}") from e
    # 哨兵参数必须是设计空间的合法点（加载期校验，不留到运行期）
    from expos.design.space import DesignError, to_unit

    try:
        to_unit(cfg.design_space, cfg.sentinel.params)
    except DesignError as e:
        raise DomainError(f"哨兵参数非法: {e}") from e
    # 指标与必需参数的交叉校验也在加载期完成（对抗审查 #4）
    cls = ADAPTER_REGISTRY[cfg.adapter]
    # A dual-leg dry adapter's objective is the WET metric; its dry-channel metric
    # (adapter.default_metric) is a SEPARATE channel consumed by mcl, so the two
    # legitimately differ -- skip the single-leg objective/adapter-metric cross-check.
    if cls not in _DUAL_LEG_DRY_ADAPTERS:
        sim_metric = cfg.simulator.get("metric", getattr(cls, "default_metric", None))
        if sim_metric is not None and sim_metric != cfg.objective.metric:
            raise DomainError(
                f"objective.metric={cfg.objective.metric!r} 与 adapter 指标 {sim_metric!r} 不符"
            )
    required = set(getattr(cls, "required_params", frozenset()))
    missing = required - {v.name for v in cfg.design_space.variables}
    if missing:
        raise DomainError(f"设计空间缺 adapter 必需变量: {sorted(missing)}")
    # M21 domain-provider loading line (mailbox 120): when the yaml declares a
    # ``provider:``, import it, run its birth-time governance (check_complete) and its
    # domain-specialized yaml validation (validate_yaml) -- all LOUD at load, never a
    # silent fallback. The validated instance is attached as ``cfg._provider`` so
    # ``config_fingerprint`` can fold its source hash without re-importing. Absent
    # provider => this is a no-op and behaviour is byte-identical.
    if cfg.provider is not None:
        cfg._provider = load_provider(cfg)
    return cfg


def load_provider(cfg: DomainConfig) -> "DomainProvider":
    """Resolve and validate the domain's declared ``provider`` (mailbox 120 two-line
    consumption spec, step 1): ``importlib.import_module`` the ``<module_path>`` →
    ``getattr`` the ``<ClassName>`` → ``Cls.check_complete()`` (birth-time
    completeness + cross-hook governance, returns a validated instance) →
    ``inst.validate_yaml(cfg)`` (domain-specialized yaml checks). Every failure is
    LOUD.

    The module path MUST live inside the importable ``expos.`` package: we import by
    an explicit dotted path via ``importlib`` and do NOT scan the filesystem or pip
    entry_points (INDEX_M21_DOMAINPLUGIN's entry_points rejection -- declarative
    loading is an explicit yaml field, not discovery). An arbitrary/off-tree module
    path is rejected before any import so a domain yaml can never pull code from
    outside the OS package.
    """
    spec = cfg.provider
    assert spec is not None  # caller guards; format already validated on the field
    module_path, _, class_name = spec.partition(":")
    if module_path != "expos" and not module_path.startswith("expos."):
        raise DomainError(
            f"provider module {module_path!r} must live under the 'expos.' package "
            "(declarative loading is an explicit in-tree yaml locator, not filesystem "
            "or entry_point discovery); refusing to import an off-tree module"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise DomainError(
            f"provider module {module_path!r} could not be imported: {e}"
        ) from e
    try:
        provider_cls = getattr(module, class_name)
    except AttributeError as e:
        raise DomainError(
            f"provider class {class_name!r} not found in module {module_path!r}"
        ) from e
    if not (isinstance(provider_cls, type) and hasattr(provider_cls, "check_complete")):
        raise DomainError(
            f"provider {spec!r} does not resolve to a DomainProvider class "
            f"(got {provider_cls!r})"
        )
    # check_complete() raises DomainProviderError on an incomplete/inconsistent
    # provider; validate_yaml() raises it on a yaml the provider cannot realise.
    # Both are ExposError-loud and propagate unswallowed (birth-time governance).
    inst = provider_cls.check_complete()
    inst.validate_yaml(cfg)
    return inst


def config_fingerprint(domain_config: DomainConfig | dict[str, Any]) -> str:
    """domain_config 全文的 sha256 指纹（canonical JSON：sort_keys + 紧凑分隔符 + UTF-8）。

    用途（STRESS_TEST_R1 P2「域配置漂移放行」修复）：resume 时只比对 domain/mode/seed
    三键会放行 domain_config 全文漂移（阈值/注入器/预算变了照样续跑，run 语义漂移）。
    新 run 把本指纹存入 config.json（键 ``config_fingerprint``）；resume 时对存储的
    domain_config 与当前加载结果各算指纹比对——任何键值变化都会改变指纹，不匹配须
    响亮拒绝（逃生门 --allow-config-drift 落 config_drift 事件留痕）。

    对 DomainConfig 实例取 ``model_dump(mode="json")`` 后哈希；对 dict（config.json
    里存的 domain_config 原文）直接哈希——mode="json" 的 dump 与 JSON 往返后的 dict
    逐值相等（tuple 已降为 list），故两侧指纹可比。

    M21 provider fold: the ``provider`` locator field is EXCLUDED from the base
    material and, when a provider is present, its source-hash ``provider_fingerprint``
    is folded in additively (``hash([base_material, provider_fingerprint])``). Two
    consequences, both intentional:
      * A provider-LESS domain (``provider`` absent) is byte-identical to the
        pre-provider fingerprint: excluding a field that is absent/None leaves the
        base material unchanged, and no fold happens. So crystal/coating/flipped and
        any OLD provider-less run resume with no drift.
      * A provider-declaring domain's fingerprint changes (it now carries the
        provider source hash), so a domain-implementation drift trips resume
        drift-rejection. Only the NEWLY-declaring yamls change. Resuming an OLD run
        whose stored ``config`` predates the provider declaration will mismatch the
        current fingerprint -- that is handled by the existing LOUD mismatch path in
        loop.py (``--allow-config-drift`` to override), never silently.
    ``provider_fingerprint`` needs the live provider source, so it is read from the
    attached ``_provider`` instance (populated by ``load_domain``); the dict path
    (an old stored ``domain_config`` with no live instance) folds nothing, matching
    the provider-less base.
    """
    provider_fp: str | None = None
    if isinstance(domain_config, DomainConfig):
        inst = domain_config._provider
        if inst is not None:
            provider_fp = inst.provider_fingerprint()
        domain_config = domain_config.model_dump(mode="json", exclude={"provider"})
    elif isinstance(domain_config, dict) and "provider" in domain_config:
        # A stored domain_config dict (config.json) may carry the serialized
        # ``provider`` locator; drop it so the base material matches the DomainConfig
        # path (which excludes it) and stays byte-identical to the provider-less form.
        domain_config = {k: v for k, v in domain_config.items() if k != "provider"}
    blob = json.dumps(
        domain_config, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    material = blob.encode("utf-8")
    if provider_fp is None:
        return hashlib.sha256(material).hexdigest()
    h = hashlib.sha256()
    h.update(material)
    h.update(b"\x00provider_fingerprint\x00")
    h.update(provider_fp.encode("utf-8"))
    return h.hexdigest()


def build_adapter(cfg: DomainConfig) -> ExecutionAdapter:
    cls = ADAPTER_REGISTRY.get(cfg.adapter)
    if cls is None:
        raise DomainError(f"未知 adapter: {cfg.adapter!r}")
    if cls in _DUAL_LEG_DRY_ADAPTERS:
        # A dual-leg dry adapter (pyscf_dry job-shaped / sequence_proxy in-process dry leg)
        # is NOT the synchronous single-leg ExecutionAdapter this factory builds; a
        # positional ``cls(cfg.simulator or None)`` would crash. Fail loud with the fix
        # (dimw5_handoff §1): the dual-leg domain is driven by ``--loop mcl``.
        raise DomainError(
            f"adapter {cfg.adapter!r} is a dual-leg dry adapter and cannot be built "
            "through build_adapter; run this domain with `--loop mcl` "
            "(expos.mcl.run_mcl_loop), which drives both legs through the W5 glue"
        )
    try:
        return cls(cfg.simulator or None)
    except AdapterError as e:
        raise DomainError(f"adapter {cfg.adapter!r} 构造失败: {e}") from e
