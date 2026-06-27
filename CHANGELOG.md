# Changelog

All notable changes to KarvyLoop are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/). Versioning is
**date-based (CalVer)** — `YYYY.M.D`, the date a release is cut (e.g. `2026.6.26`).
The single source of the version number is `karvyloop/__init__.py:__version__`.
Releasing is described in [RELEASING.md](RELEASING.md).

## [Unreleased]

_Work in progress toward 1.0 — see [ROADMAP.md](ROADMAP.md)._

### Added
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
