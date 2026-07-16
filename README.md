# expos — Biology-Primary Adaptive Dry–Wet–Agent Research OS

> 中文版见 [README_ZH.md](README_ZH.md)

**expos** is a research OS whose **primary scientific direction is biology**
(2026-07-14 strategic pivot), built around a self-adaptive "dry–wet–agent"
scientific loop. **Biology now drives that loop end to end:** the first
biological closed loop — cell-free protein expression, **sequence → phenotype →
claim → knowledge → redesign** — is **double-signed (M24-B, simulation-level).**
The engine underneath is deliberately **domain-neutral**: a loop that knows
nothing about any specific science domain (**propose → dry compute → promotion
gate → wet measurement → evidence compile → claim → knowledge → next proposal**),
with an agent making adaptive decisions inside it. That biology-agnostic kernel
is the core design principle — and the two chemistry domains already proved it,
by running a different domain with **the kernel / planner / evidence-compiler /
ledger / knowledge-compiler unchanged byte for byte**, which is exactly what now
lets biology ride the same loop.

**Elevator pitch:**
- **One closed loop, not a pipeline.** The kernel holds only two persistent
  scientific objects (ExperimentObject / ObservationObject) plus an append-only
  event log and a round state machine; design / dry / promotion gate / wet / QC /
  evidence aggregation / claim ledger / knowledge compiler / agent are all
  **swappable modules**.
- **Domain-neutrality is a hard gate, not a slogan.** The kernel understands only
  seven concepts — candidate / observation / trust / evidence(claim) / knowledge /
  decision (+ the ExperimentObject carrier); all chemistry / biology semantics
  are **allowed to live only in the domain / provider / adapter / QC layers.**
  This has been demonstrated by "the same kernel running a different domain byte
  for byte" (see the status ledger below) — it is the project's core selling point.
- **An observation is "evidence pending adjudication," not data.** Any measurement
  enters with `trust = PENDING` and is routed only after QC adjudication;
  experimental evidence is statistically aggregated into a claim (supported /
  rejected / qualified / insufficient), written back to the ledger, recompiled
  into knowledge, and drives the next round's proposal — **data rewrites the
  knowledge-fingerprint chain by self-derivation**, not by external injection.

---

## Biology Program (the primary direction)

Biology is where the system stops being a chemistry demo and has to face
genuinely new scientific objects: **molecule / reaction → sequence / construct →
expression / phenotype.** That is why the 2026-07-14 ruling made biology the
primary direction; the two chemistry domains had already served their purpose
(proving the runtime is domain-swappable). Authoritative roadmap:
[`docs/ROADMAP_BIOLOGY_PRIMARY.md`](docs/ROADMAP_BIOLOGY_PRIMARY.md) and
[`docs/BIOLOGY_PROGRAM_2026.md`](docs/BIOLOGY_PROGRAM_2026.md).

### First biological closed loop — `cell_free_expression_screen` (M24-B, double-signed, simulation-level)

A real dry–wet–agent loop over cell-free protein expression:
**sequence → phenotype → claim → knowledge → redesign.**

- **Three-state separation on one claim head** (driven by pure phenotype =
  fluorescence): `expression_high` → **supported** (e-product 102→1033, +0.234);
  `expression_flipped` → **rejected** (e=42, −0.004); `flat` → **insufficient**
  (e=0, p=1.0).
- **Knowledge-fingerprint migration** d04b4d05 → 7f8ce457, driven by wet
  fluorescence — data self-derives new knowledge, not external injection.
- **Biology-blind dry leg** — 33 dry observations from sequence proxies / sim,
  **0 Z-matrix / geometry / PySCF**: the kernel never learns it is doing biology.
- **Real biological parts** — Anderson promoter ladder (J23100…), RBS, GFP CDS,
  plus auditable mutation operators (these are *design knowledge / calibration*,
  not this run's observations).
- **Double-signed = both certification paths certify:** raw readout +
  percent-of-control (the controls path via a scale-aware `w_min` fix,
  effective_w_min 83.33 on the percent scale, e=102→1034, CI widths ≪ w_min).
- **One caveat kept exactly:** criterion ④ (changed knowledge → next construct)
  is **mechanism-proven** (flipping the knowledge direction fully reorders the
  proposal) but does **not self-trigger inside a single low-signal loop** — a
  real explore/exploit limit, honestly flagged, not a bug.

### Breadth-first: the five-organ Biology-Primary OS (M25–M29, fuller v0.1)

Built in parallel and now deepened from skeletons to **fuller v0.1 vertical
slices** of the biology OS — **135 tests green incl. chemistry regression**, with
the kernel staying biology-blind. Each organ is simulation-level; **only M26 is
wired whole-OS** (its dry+wet loop runs through `run_mcl_loop`), the other four
run domain-local e2e end to end with whole-OS wiring the remaining seam
(authoritative: `docs/BIOLOGY_PROGRAM_2026.md` §1.5, per-seam docs in
`docs/bio_seams/`).

| Organ | What | Maturity |
|---|---|---|
| **M25 · Design** | 5 auditable mutation operators (2 translation-invariant) + generation pool + PROV lineage + diversity acquisition; discriminative case (dry ranking overturned by wet phenotype, effect −0.322) on the real claim + knowledge ledger | fuller v0.1 — domain-local e2e + 24 tests |
| **M26 · Program** | typed genetic-circuit graph, circuit family 2→5 (dose-response / FFL / repressilator oscillator) + 5-tier verify gate + time-series dynamic faces + oscillation-frequency phase | fuller v0.1 — **whole-OS mcl e2e landed** · 20 tests + 9 landed mcl e2e |
| **M27 · Perturb** | 5-backend virtual-cell tournament + discriminative baseline-gate, grounded in a real published Perturb-seq benchmark (no method beats the mean across 3 datasets; scGPT clears zero) enforced as a test | fuller v0.1 — domain-local e2e + 26 tests |
| **M28 · Understand** | four discovery agents (Hypothesis / Analysis / Contradiction / Replication) driving the real claim ledger; agents emit evidence only, the kernel gate certifies (structural moat) | fuller v0.1 — domain-local e2e + 8 tests |
| **M29 · Execute** | typed protocol → device_ir → fake liquid-handler / plate-reader through the M23 sensed-state COMMITTED gate; five transaction faces | fuller v0.1 — domain-local e2e + 19 tests |

**Honest boundary.** All biology is **simulation-level** — credible simulated wet
reads + real *sequence* dry proxies (GC / CAI / RBS / RNA-folding ΔG, honestly
labelled biased proxies), with **no real wet-lab and no real hardware.** M27's
benchmark grounding is a real *published* result used as calibration, not this
run's own wet observation; M29 is protocol-to-simulated-physical. M24-B is
double-signed only in the sense of *raw + controls certification*; the five
organs are fuller v0.1 slices — not a finished product, not double-signed, and
(except M26) not yet wired whole-OS. Public sequence data and parts are used as
design knowledge / calibration, never as this run's observations.

---

## Honest status ledger

The full per-direction status (biology detailed above; the ledger keeps the
complete picture incl. chemistry and the real-hardware track):

| Direction | What | Status |
|---|---|---|
| **Biology (primary direction) · execution surface** | cell-free protein expression / genetic-construct screening: Domain Contract v3 (`compute_targets → ComputeTarget`, `input_kind` supports `molecular_geometry` / `sequence_construct`); real sequence dry proxies + three truth faces (expression_high / expression_flipped / flat, by *design* not measured) | ✅ execution surface in place (M24-A) |
| **Biology · adaptive closed loop** | first real biological loop (`cell_free_expression_screen`): phenotype → evidence → claim → knowledge; three-state separation + fingerprint migration + biology-blind dry leg (details above) | ✅ **double-signed (M24-B): raw + controls both certify** (simulation-level) |
| **Biology · breadth-first five organs** | M25–M29 fuller v0.1 vertical slices of the Biology-Primary OS (Design / Program / Perturb / Understand / Execute), 135 tests green incl. chemistry regression; M26 wired whole-OS (mcl e2e landed), the other four domain-local e2e | 🔨 fuller v0.1 (simulation-level; only M26 wired whole-OS, none double-signed) |
| **Chemistry (validated foundation / jumping board)** | solvent / catalyst screening, full dry–wet–agent loop; domain-swap existence proof with the same kernel/loop byte-unchanged — this is what proved the runtime can swap to biology | ✅ done & countersigned (M16–M22) |
| **Real hardware (parallel engineering track)** | transaction-safe semantics for real physical actions — recoverable / re-readable / committable / non-replayable (Real-Wet Readiness Contract) | ✅ ready against a fake physical backend / ❌ real hardware pending; real wet-lab validation ❌ |

> **Honest boundary (please note).** The biology loop now **closes decisively on
> both certification paths** (M24-B, **double-signed**): a single claim head
> separates into three states under pure phenotype (fluorescence), wet fluorescence
> migrates the knowledge fingerprint, and **both the raw readout path and the
> percent-of-control path certify** — the controls path landed via a scale-aware
> `w_min` fix (effective_w_min 83.33 on the percent scale, claim SUPPORTED,
> e=102→1034, CI widths ≪ w_min). One caveat is kept **exactly** (it survives the
> double-sign): criterion ④ — the changed knowledge changing the next round's
> construct — is **mechanism-proven** (flipping the knowledge direction fully
> reorders the proposal) but does **not self-trigger inside a single low-signal
> loop** (a real explore/exploit limit, not a bug). All of this is at
> **simulation level** — the biology domain runs on in-silico sequence proxies and
> simulated plate reads (no real wet-lab). The chemistry loop, the domain-swap
> proof, and the real-wet readiness contract (against a fake backend) remain
> **things that actually happened**.

**Architecture hard gate (the project's core design principle).** Whether the
primary direction is chemistry or biology, `kernel / planner / evidence-compiler /
ledger / knowledge-compiler` must stay **domain-neutral (biology-agnostic)**. If
onboarding biology forces a change to any of these, that proves the domain
abstraction is not yet clean enough — that is a *finding to report honestly*, not
something to sneak in. The entire cost of changing domain is confined to the
domain / provider / adapter / QC layers.

---

## Two loops and the core idea

expos has two loop drivers:

1. **Dry–Wet–Agent closed loop (`expos/mcl.py::run_mcl_loop`, the current
   scientific core).** A two-legged pipeline: dry compute leg (chemistry = PySCF;
   biology = sequence-feature proxy) → Dry→Wet promotion gate → wet measurement
   leg (plate-reader simulator) → QC/trust adjudication → evidence aggregation and
   claim decision → ledger update → knowledge recompilation → next proposal.
   solvent_screen / catalyst_screen run on this, and the biology domain does too.
2. **Single-leg materials loop (`expos/loop.py::run_loop`, the original
   foundation).** One synchronous executor (crystal / coating simulator) with
   **structured artifact injection** and three-tier QC / trust routing — this is
   where the project first proved "measurement untrustworthy vs parameters
   infeasible" classify-and-route; still real, still in use.

```
┌──── Agent Orchestrator (proposes; never adjudicates) ────┐
│ goal translation · priors/rationale · QC narration       │
│ action proposals → DecisionRecord; read-only view + queue │
└───────────────┬──────────────────────────────────────────┘
                ▼ propose / explain (never writes observations, evidence or knowledge)
┌────────────────────── Kernel (domain-neutral) ──────────────────────┐
│ objects    two schemas + DecisionRecord (seven concepts: candidate/  │
│            obs/trust/evidence(claim)/knowledge/decision)             │
│ store      append-only event log + object store + run checkpoint     │
│ lifecycle  round state machine + trust routing                       │
│ claims     ClaimRecord / ClaimDelta / Ledger (evidence ledger)       │
│ knowledge  compile_knowledge (knowledge = compiled from the ledger)  │
└──┬─────────┬──────────┬───────────┬────────────┬───────────┬────────┘
 design/   adapters/   planner/    qc/          (dry leg)   (wet leg)
 sampling  dry+wet     promotion   3-tier +     PySCF /     plate-reader
 layout    providers   gate +      attribution  sequence    simulator
 budget                certification +stats     proxy
Round state machine:   DESIGNED → EXECUTED → QC_DONE → ROUTED → CLOSED
Observation lifecycle: PENDING ─QC→ TRUSTED → evidence aggregation
                                  │ SUSPECT/FAILED → failure model (+ next_action)
```

Two claims we are prepared to defend (see `docs/DEEP_REVIEW.md §1`):
1. **A methodological gap.** No public benchmark injects *structured* systematic
   bias (spatial fields, drift, batch effects) into a closed-loop optimization
   comparison — prior work stops at iid noise. Our "simulator + six artifact
   injectors + naive-vs-OS comparison" lands squarely in that gap.
2. **Provenance-driven failure attribution.** Where A-Lab–style pipelines conflate
   synthesis failure with characterization failure on one decision chain, expos
   classifies and routes "measurement is untrustworthy" apart from "parameters are
   infeasible" (the capability is demonstrated; the "first-class kernel service"
   packaging is a V2 proposal, see `docs/ARCHITECTURE_V2_PROPOSAL.md`).

---

## Quick start

```bash
# Install (Python >=3.11; or run straight from the repo root — conftest.py makes
# `import expos` work without installing).
pip install -e .            # for UI/LLM: pip install -e ".[ui]" / ".[llm]"
```

### Single-leg materials loop (crystal / coating)

```bash
# Run the closed loop — all five arms wired (strawman control → full OS); swap arm via --mode
python scripts/run_loop.py --domain crystal --mode os --rounds 6 --seed 7 --out runs/demo
#   --mode in {naive, robust, rcgp, os, os-soft, compare}; resume with --resume
#     naive    trust-everything baseline (strawman control)
#     robust   trust-blind + replicate-median aggregation (robustness outside routing)
#     rcgp     model-layer robustness (RobustResponseModel: Plateau-IMQ posterior soft-trimming)
#     os       three-tier QC + trust routing (the full OS)
#     os-soft  os + soft-downweight of quarantined observations (soft-trust control)
#     compare  forwards to expos.eval.compare for three-arm orchestration + headline plot
```

### Dry–Wet–Agent loop (solvent / catalyst; biology domain uses the same path)

The two-legged MCL loop is currently exposed as a Python API
(`expos.mcl.run_mcl_loop`):

```python
from expos.mcl import run_mcl_loop

# Chemistry: solvent screening, two rounds, default template agent (LLM is an optional plugin slot)
summary = run_mcl_loop(
    "domains/solvent_screen.yaml", rounds=2, seed=7, out_dir="runs/solvent_demo",
)
# catalyst_screen.yaml uses the same path — same kernel/loop, byte-unchanged domain swap
# (the M20 domain-swap existence proof).
```

Per-round data flow: `compile_knowledge → agent proposal → dry leg → promotion
gate → wet leg → QC/trust adjudication → certification decision →
apply_claim_deltas → next round`. Evidence produces a claim automatically, the
claim rewrites the knowledge fingerprint, and the knowledge changes the next
round's candidates — this "data self-derives new knowledge" causal chain is the
core demonstrated in the chemistry domains, and now in the first biological loop
too (M24-B is double-signed — raw + controls paths both certify; three-state
separation + knowledge-fingerprint migration; criterion ④ mechanism-proven,
simulation-level).

### CLI v2 and evaluation

```bash
# read-only query surface over runs/ + override drop box (after install, `expos` ≡ python3 -m expos.cli)
expos status   runs/demo                          # one-screen run state
expos verdicts runs/demo --trust suspect          # verdict table (filter by trust level)
expos inspect  runs/demo obs <obs_id>             # object & event query (what in events/obs/exp)
expos override runs/demo --obs <id> --trust trusted --reason "…"  # human override (audit event; never touches the store)
expos domains  validate domains/solvent_screen.yaml  # domain-config preflight
expos ui       --runs-root runs                   # read-only Streamlit panel (needs .[ui])

# One-command three-act demo; script narrative in docs/DEMO_SCRIPT.md
python scripts/make_demo.py --out runs/demo

# M9 three-arm comparison (idempotent cells — completed campaigns are never recomputed)
python -m expos.eval.compare --domain domains/crystal.yaml --scenario S0.demo \
    --seeds 1,2,3 --rounds 8 --out-root runs/m9 --arms naive,robust,os

# Gate-12 decision-chain verification (three-tier + decision-chain diff)
python scripts/verify_run_chain.py runs/<name>

# Run the tests
pytest -q
```

Run artifacts (`runs/<name>/`, gitignored):

```
runs/demo/
├── config.json          # domain config snapshot + mode + seed (reproducible)
├── events.jsonl         # append-only event log: transitions/verdicts/reroutes/claim decisions
├── checkpoint.json      # run checkpoint (current round / budget / ledger snapshot) → resume
├── experiments/         # exp_r<k>.json
├── observations/        # obs_*.json
├── truth/               # simulator ground-truth sidecar (OS cannot read it; scoring only)
├── models/              # response-model training-set fingerprints
└── report/              # comparison plots + summary.json (from M9)
```

**Pilot numbers (S0.demo · crystal, single-leg materials loop).** In round 3 an
edge-evaporation injector lifts a mediocre edge well to the highest reading on the
plate — across the seed sweep the **fake-optimum hit rate is naive 1.00 vs os
0.20**; **os**'s three-tier QC flags it SUSPECT and refutes it on re-measurement.
Full sweep (**1,450 cells = calibration set A 450 + evaluation set B 1,000**, five
arms) headline numbers are highly significant credibility indicators:
fake-optimum rejection (paired permutation **exact p≈3.1e-5**) and training-set
contamination os **0.004** vs naive **0.146** (paired permutation **exact
p≈1.9e-6**), both recomputed in `runs/full_sweep/report/headline_stats.json`.
**Regret is honestly flagged as not significant / scenario-dependent** (os-vs-naive
p=0.0668, and it trails the robust baseline on most structured-bias scenarios) —
os's edge is in contamination protection and fake-optimum rejection; see the
decoupled discussion in the paper outline (`docs/PAPER_OUTLINE.md`).

---

## Repository layout

```
dry_wet_agent_os/
├── README.md  README_EN.md  CHECKPOINTS.md  CHANGELOG.md  pyproject.toml  conftest.py
├── docs/
│   ├── ARCHITECTURE.md            authoritative blueprint (axioms/domains/schemas/layer specs)
│   ├── ROADMAP_BIOLOGY_PRIMARY.md authoritative roadmap for biology-as-primary (2026-07-14 ruling)
│   ├── M24_CELL_FREE_EXPRESSION.md / M24_CONTRACT_V3.md / M24_REPO_MAP.md  biology-domain charter & seam map
│   ├── BUILD_PLAN.md              milestone definitions and acceptance criteria
│   ├── DEEP_REVIEW.md             validity review (two claims + three threats)
│   └── REFERENCE_MAP.md / PAPER_OUTLINE.md / MCP_SURFACE.md …  survey / paper outline / audit surface
├── expos/
│   ├── kernel/{objects,store,lifecycle,claims,knowledge,overrides}.py  # domain-neutral kernel: two objects + event log + trust routing + evidence ledger + knowledge compile
│   ├── design/{space,sampler,layout,budget}.py
│   ├── planner/{promotion,certification,arbiter,stages,policy}.py       # promotion gate + evidence decision + failure-aware planning (pure-function red line)
│   ├── qc/{checks,attribution,failure_model,certification_stats,replicate_collapse,stats,policy}.py
│   ├── adapters/dry/{adapter,compute,catalysts,solvents,constructs,sequence_adapter,sequences,ingest,worker}.py  # dry leg: PySCF (chemistry) + sequence-feature proxy (biology)
│   ├── adapters/wet/{screen,sim_reader,bio_readout,action_ledger,recovery,differential_gate,orchestration,…}.py  # wet leg: plate-reader simulator + real-wet readiness transaction surface
│   ├── adapters/providers/{solvent_screen,catalyst_screen,cell_free_expression_screen}.py  # domain providers (incl. biology)
│   ├── adapters/{base,sim_base,sim_crystal,sim_coating,domain_provider,bench_manual,artifacts,content_store}.py
│   ├── models/{response_gp,robust_gp}.py   # response GP (trusted) + RCGP robust GP
│   ├── cli.py                              # CLI v2
│   ├── mcl.py                              # Dry–Wet–Agent two-legged loop (run_mcl_loop)
│   └── domain.py  loop.py                  # domain assembly + single-leg materials loop
├── expos_mcp/                              # FastMCP read-only audit surface (expos-audit skill)
├── domains/{crystal,coating,solvent_screen,solvent_screen_flipped,catalyst_screen}.yaml  # change domain = change config
├── scripts/{run_loop,make_demo,verify_run_chain,gen_sweep,expos_report,…}.py
├── tests/                                 # kernel/design/adapters/qc/planner/mcl/e2e …
└── runs/                                  # run artifacts (gitignored)
```

(`CHECKPOINTS.md` = build ledger with per-milestone status/verification/deviations;
`CHANGELOG.md` tracks releases.)

---

## Milestone status

| # | Milestone | Status |
|---|---|---|
| M0–M10 | Single-leg materials OS: kernel / design / adapters / 3-tier QC / attribution+failure model / failure-aware planner / agent layer / naive-robust-os three-arm eval / CLI v2 + UI | ✅ done |
| M16 | Executable Minimum Dry–Wet–Agent Control Loop (solvent_screen, simulated-wet) | ✅ done (countersigned) |
| M17+M18 | Evidence-to-Claim Compiler + knowledge feedback loop → named **Adaptive Dry–Wet–Agent Scientific Loop**; LLM three stages passed (default still template) | ✅ done (countersigned) |
| M20 | Domain-swap existence proof (catalyst_screen, same kernel byte-unchanged, gate-12 COMPLETE) | ✅ done (countersigned) |
| M21–M22 | Domain contract v2 + provider five-hook + provenance completion + property-testing culture | ✅ done (countersigned) |
| M23 | Real-Wet Readiness Contract (real-wet transaction surface, against fake physical backend; real hardware pending) | ✅ done (countersigned) |
| **M24-A** | **Biology execution surface: Domain Contract v3 (compute_targets/ComputeTarget) + sequence dry proxies + three truth faces** | **✅ in place** |
| **M24-B** | **Adaptive biological closed loop (cell-free expression: phenotype → evidence → claim → knowledge)** — three-state separation + fingerprint migration; raw + controls paths both certify (controls via scale-aware w_min fix, effective_w_min 83.33, e=102→1034); criterion ④ mechanism-proven (not self-triggered in-loop); simulation-level | **✅ double-signed (raw + controls both certify)** |
| M25–M29 | **Biology Program breadth-first, deepened to fuller v0.1**: five organs fleshed out from skeletons to **fuller v0.1 vertical slices** of the Biology-Primary OS — **135 tests green incl. chemistry regression**, kernel biology-blind. M25 Design (5 auditable mutation operators + PROV lineage + diversity acquisition; discriminative dry-overturned-by-wet case, effect −0.322; 24 tests); M26 Program (circuit family 2→5 incl. repressilator oscillator + oscillation-frequency phase; **integration owner landed M26 seams 1–5 → whole-OS dry+wet loop runs through `run_mcl_loop`**; 20 tests + 9 landed mcl e2e); M27 Perturb (5-backend tournament + baseline-gate grounded in a real published Perturb-seq benchmark enforced as a test; 26 tests); M28 Understand (four discovery agents driving the real claim ledger, kernel gate certifies; 8 tests); M29 Execute (typed protocol → device_ir → fake liquid-handler/plate-reader through the M23 COMMITTED gate; 19 tests). All simulation/retrospective/fake-backend level, honestly labelled; **only M26 wired whole-OS**, the other four run domain-local e2e with whole-OS wiring the remaining seam (authoritative: `docs/BIOLOGY_PROGRAM_2026.md` §1.5, `docs/bio_seams/`) | 🔨 fuller v0.1 (simulation-level; M26 whole-OS, none double-signed) |

> The authoritative milestone ledger is `CHECKPOINTS.md` (verification commands +
> deviations); the biology roadmap is `docs/ROADMAP_BIOLOGY_PRIMARY.md`.

---

## Design red lines

- **Domain-neutrality hard gate.** `kernel / planner / evidence-compiler / ledger /
  knowledge-compiler` are domain-neutral (biology-agnostic); all chemistry/biology
  semantics live in the domain/provider/adapter/QC layers. If a new domain forces a
  change to these kernel files, report it as an abstraction finding — never sneak it in.
- **Ground-truth isolation.** Simulator truth is written only by `adapters/sim_*` /
  the reader server into the `truth/` sidecar; `qc/models/planner/agent` may never
  read it, and `loop.py` only passes it through opaquely. This is the precondition
  for *quantitatively proving* the system was not fooled by artifacts.
- **The agent cannot adjudicate.** The agent gets only a read-only view and a
  proposal queue; every output is a DecisionRecord validated by planner/kernel
  before it takes effect. It holds no write handle to observations, evidence or
  knowledge (enforced by gatekeeper tests).
- **Immutable evidence + write-strict/read-lenient.** Evidence is an append-only
  hash chain; schema evolution goes through validation semantics, not data
  migration (the ADDITIVE_SINCE registry). All progress must be recoverable and
  auditable.
- **No silent degradation.** Missing constraint variables, budget overruns,
  out-of-range parameters, missing units — all fail loudly with clean domain
  exceptions, never silently pass or degrade.

---

## Safety & execution-mode statement

The primary actuator is a **simulator with structured artifact injection**
(controlled ground truth → quantitative naive-vs-OS comparison); `BenchAdapter`
provides a protocol-isomorphic real-bench path (human-readable worklists +
CSV/image ingestion), and the real-wet readiness transaction surface (M23) is
validated **against a fake physical backend** — **real hardware and real wet-lab
validation are still pending.** The chemistry domains use common
food-/education-grade safe salts (alum, potassium nitrate) in aqueous
evaporation-crystallization etc.; the biology domain is currently confined to
**in-silico sequence proxies and simulated plate reads** — no real wet experiments,
no live-material handling.

---

## Docs & contributing

- **Docs site** (MkDocs Material, `mkdocs.yml`): `python3 -m mkdocs serve` to
  preview locally. Authoritative blueprint `docs/ARCHITECTURE.md`, biology roadmap
  `docs/ROADMAP_BIOLOGY_PRIMARY.md`, paper outline `docs/PAPER_OUTLINE.md`, demo
  script `docs/DEMO_SCRIPT.md`, audit surface `docs/MCP_SURFACE.md`.
- **Read-only audit:** `expos_mcp/` provides a FastMCP audit surface (seven
  read-only tools, incl. gate-12 verify_gate12 / diff_runs); paired with the
  `expos-audit` skill.
- **Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md) — policy-injection-point
  conventions, red lines, and adversarial-test requirements; engineering norms in
  `docs/ENGINEERING.md`.
```
