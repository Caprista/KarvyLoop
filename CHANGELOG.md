# Changelog

All notable changes to KarvyLoop are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/). Versioning is
**date-based (CalVer)** — `YYYY.M.D`, the date a release is cut (e.g. `2026.6.26`).
The single source of the version number is `karvyloop/__init__.py:__version__`.
Releasing is described in [RELEASING.md](RELEASING.md).

## [Unreleased]

_Work in progress toward 1.0 — see [ROADMAP.md](ROADMAP.md)._

### Added
- **The atom's quality is now judged by an LLM on a slow Trace-reading cadence — off the hot
  path.** Each day the console reads the runs that already passed the cheap deterministic
  evaluation *and* cleared correctness (做对站住), asks the model "how well was this done", and
  folds a graded quality score + a one-line critique into that run's existing satisfaction
  (no new sample, so no double-count). It is wired through the existing daily-poll consumer and
  gateway bridge — it never adds latency to your turn. Hardened against the failure modes that
  matter for a learning signal: a gateway hiccup (no judgment) is *not* recorded as "judged with
  no quality" — it's left to retry when the model is reachable again (so one bad day can't
  silently poison the signal); the per-day work is capped so a backlog never becomes a burst of
  LLM calls; quality survives restart (replayed from the Trace). (Independent adversarial
  verification caught the silent-poison and cost-spike modes before commit; both fixed with tests.)
- **Execution and evaluation are now separated (run/eval split).** A drive used to *score*
  the run on the hot path; now `drive()` only executes and writes the evaluation *facts*
  (sig, success, verified, steps) into the Trace, and a Trace-derived evaluator
  (`crystallize/trace_eval.py`) computes the satisfaction signal off the hot path (in
  maintenance), so learning never competes with the real-time task for resources. The
  evaluator is idempotent (watermarked by the run's trace ref) and writes its results back
  into the Trace — which both realizes the self-reflexive "what did the system learn" record
  and makes the watermark survive restarts: a new process rehydrates the watermark + samples
  from the Trace, so historical runs are never re-scored / double-counted. It scans all
  pending runs (not just the latest task), so a skipped or interleaved maintenance pass never
  orphans a run. (Trace stays the single source of truth; the satisfaction store is a derived
  projection of it. Independent adversarial verification caught the restart-double-count and
  task-orphan failure modes before commit; both fixed with cross-process tests.)
- **Atom-layer crystallization now has a role-as-critic signal** (the first slice of the
  two-layer-by-accountability redesign, [docs/02 §14]). A skill's runs are scored by a
  *multi-dimensional, graded* satisfaction — `achievement` (did the sub-goal complete and
  pass its verify gate), `efficiency` (steps vs the sub-goal's median baseline), and a
  `quality` dimension reserved for the next slice — aggregated `achievement × (base + good)`
  so that **doing it well can never rescue a run that wasn't done right** (anti reward-hacking),
  and credit is isolated per sub-goal signature (a role's own outcome can't leak into an
  atom's score). New `crystallize/atom_critic.py` (AtomSatisfaction / SatisfactionStore),
  recorded on the live drive path with the run's own verify verdict (no lag) and fail-loud
  on error. This is the seam that turns "越用越记得你重复过什么" (memory) into "越用越对你管用"
  (learning); wiring it into promote/recall comes next.
- **Atom improve is now driven by the role's judgment, not the human's** (the second slice).
  The skill `improve` path used to write the *human's* mid-task corrections into `SKILL.md`
  (`steered_by_user`) — training the atom with human feedback, which inverts the accountability
  chain (an atom answers to its role, not to you) and was in any case dead code (nothing ever
  populated it). It now writes the **role's quality critique** instead. Added the quality
  dimension itself: an LLM judge (`judge_quality`) scores *how well* a verified sub-goal was
  done with a strict 宁空勿毒 parser (`parse_quality` — rejects non-finite/bool/garbage numbers,
  takes only the first balanced JSON object) and a critique that is sanitized to a single safe
  line before it can touch the skill library (no markdown/frontmatter injection). By design the
  quality judge runs **off the hot path** — execution and evaluation are separate: a drive only
  records the cheap deterministic signal, and the LLM judging rides the existing async Trace
  consumer pipeline (the same daily-poll / distiller cadence and gateway bridge that habit
  distillation already uses) rather than adding latency to your turn. **That async consumer is
  the next slice**; the deterministic achievement+efficiency signal above is live today.
- **One-click upgrade from the console.** The "new version" banner now has an *Upgrade*
  button: you click it (so it's never a silent auto-upgrade — you decide), and the
  console runs the whole pipeline for you — stop the service → install (`git pull
  --ff-only --autostash && pip install -e .`, or `pip install -U`) → restart — then the
  page reconnects automatically. No terminal needed. A detached runner does the work so
  it survives the console restart; restart goes through `python -m karvyloop` (never
  bricks a `python -m` launch); guarded by a localhost-only check, a CSRF header, and an
  exclusive lock (no double-fire); install failures are surfaced, not silently swallowed.
- **Console handles a busy port intelligently.** If `8766` is taken by a *foreign*
  process, the console binds the next free port and prints the real URL — a port
  collision never blocks you. If it's taken by *another KarvyLoop* (e.g. an old version
  still running during an upgrade), it does **not** silently move; it reports the running
  instance's version + URL and tells you to stop it first — so you never end up looking at
  the old UI on 8766 while the new one hides on 8767. The free-port probe matches uvicorn's
  bind (POSIX `SO_REUSEADDR`), so a quick kill-and-restart reclaims 8766 instead of drifting.

## 2026.6.27

### Added
- **Imported agents are now LLM-decomposed into a role + reusable atoms** (was a flat skill
  copy). The import reads the agent and produces a real-persona role plus atoms in the shared
  pool, referenced by the role's COMPOSITION — and it costs tokens.
- **Honest atom tool-reality labels** — each atom is `executable` (its tools resolve to real
  registered tools) or advisory (persona-reasoning only), with the unresolved tool names listed;
  surfaced in the import response and `/api/atoms`.
- **Agent-vs-skill import classification** — an import with no executable atoms is reported as
  `advisory_persona` (a persona/skill, not a tool-agent) with a hint to use skill import,
  instead of silently force-fitting it as an agent.
- **Atom semantic consolidation** — `/api/atoms/consolidate/suggest` (LLM clusters near-duplicate
  atoms) + `/apply` (merge into one canonical atom, rewrite-before-delete so no role is ever left
  with a dangling reference). Never a silent merge: suggest proposes, you confirm.
- **Fuzzy-instruction orchestration** — vague instructions ("去X域找几个人分析Y") are
  LLM-decomposed into a roundtable/delegate/ops plan over *real* domain members (never fabricates
  participants); plus a deterministic ops-intent fast-path.
- **50+ participants in one roundtable / workflow** — seat count decoupled from concurrency
  (batched waves); API caps raised (roundtable 12→64, workflow mentions 8→64).
- **Time-bucketed token stats** — `TokenLedger.buckets(interval)` + `/api/tokens` `by_hour`/`recent`
  + `/api/tokens/buckets`: see *when* tokens were spent.
- **macOS sandbox (Seatbelt).** Native `sandbox-exec` adapter via the PAL, mirroring the Linux
  bubblewrap fail-closed contract (deny-default; writes confined to the token's workspace; no
  network unless granted). Adversarially verified on real Apple Silicon hardware. macOS is now a
  supported platform with working agent execution, not just chat.

### Fixed
- **The token ledger now records every LLM call.** Recording lived only on the forge path, so
  direct `gateway.complete` calls (import decomposition, fuzzy dispatch, ops diagnose, roundtable
  goal-summary) were invisible and `by_source` was a single "forge". Moved to the one choke point
  (`GatewayClient.complete`) with the contextvar source — usage is now attributed per feature.

## 2026.6.26

First dated public release — a snapshot of the current runtime.

### Runtime
- Console (FastAPI REST + `/ws` WebSocket + static SPA), MainLoop fast/slow brain,
  Forge executor, multi-provider gateway, model management UI.
- Entity model L0–L4 (tool/skill · atom · role · domain), mirrored in `schemas/`.
- Safety: capability tokens + bubblewrap sandbox below the agent's trust boundary.

### The wedge
- **Skill crystallization — stores the *method*, not the answer.** A recall hit
  re-executes the learned method on the current inputs (CBR-style) instead of replaying
  a stale answer; only semantically-stable results are cached.
- **Decision-interface crystallization** — the decision card translates a proposal into
  your terms, keeps a *verified* region separate from Karvy's *narration*, pre-aligns the
  standards you've crystallized, and forces judgment before a high-stakes commit.

### Collaboration & orchestration
- Roles, business domains, group chat. Global Karvy recognizes multi-role orchestration
  ("open a roundtable with these roles on X") and proposes it as an H2A confirm card.
- **Durable workflow execution** — a long multi-agent workflow survives a console restart
  (steps checkpointed; completed steps replay instantly).
- **Scheduled tasks** — recurring tasks described in plain language (NL→cron), Karvy-only
  (one audit surface), with a ⏰ dashboard.

### Input & models
- **Multimodal chat input** — attach images or text/Markdown files; threaded as content
  blocks through the gateway (Anthropic native / OpenAI `image_url`).
- **Providers** — Anthropic, OpenAI-compatible, DeepSeek, Kimi/Moonshot (Global +
  For-Coding), with config-driven `extra_headers` so header-gated endpoints onboard
  without code.

### Updates & quality
- Single version source; `karvyloop update` + a dismissible console banner that detects a
  new release and tells you the upgrade command — **never auto-upgrades** (zero telemetry;
  off via `KARVYLOOP_NO_UPDATE_CHECK`).
- Frontend↔backend wiring contract test (orphan endpoints / dead calls fail CI).
- Bilingual (en/zh) presentation layer; MIT licensed; self-contained test suite.
