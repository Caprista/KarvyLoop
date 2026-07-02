# Changelog

All notable changes to KarvyLoop are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/). Versioning is
**date-based (CalVer)** — `YYYY.M.D`, the date a release is cut (e.g. `2026.7.1`).
The single source of the version number is `karvyloop/__init__.py:__version__`.
Releasing is described in [RELEASING.md](RELEASING.md).

## [Unreleased]

_Work in progress toward the GA bar — see [ROADMAP.md](ROADMAP.md)._

### Security
- **Same-origin gate** on the console: cross-origin browser requests are rejected on both HTTP and the
  WebSocket handshake (closes cross-site WebSocket hijacking and file-endpoint CSRF; non-browser clients
  and the console's own frontend are unaffected). Applies even on loopback.
- **Skill integrity lock enforced on the production paths**: a tampered `trust: untrusted` skill is refused
  at the recall index, at every disk-scan fallback, and again before sandbox execution.
- **Deterministic context ceiling** at the LLM gateway choke-point: a request whose assembled context
  (messages + system + tools schema, CJK-aware estimate) exceeds the model's window is refused fail-loud
  instead of being sent to fail.

### Fixed
- H2A decision cards: every proposal kind now lands in the decision column (reject button + payload intact);
  multiple pending cards no longer overwrite each other; pending cards (incl. deferred) survive restart.
- Domain deontic rules (`forbid`/`oblige`/`permit`) now reach the runtime guardrail (previously only
  `value.md` did — and it was dropped entirely when `value.md` was blank); no double-injection with compiled
  per-role prompts.
- Decisions made over REST now feed the preference flywheel and the decision log exactly like WebSocket ones.
- Task terminal states (done/error) are recorded into Trace, so the async evaluators see task-level outcomes.
- Backend Chinese reason/detail strings are translated in the English UI (contract-tested: a new backend
  reason without a translation fails the suite).

### Added
- **Out-of-workspace access, governed**: roles are confined to the workspace by default; when one needs
  a path outside it, the denial surfaces as a decision card ("grant this folder?") — approve once and
  the grants ledger opens exactly that path (tool boundary + capability chain + sandbox mounts all honor
  it; revocable in the Capability overview). **Sensitive paths (API keys, ssh, credential stores) are a
  hard floor: never grantable, immune even to bypass mode.**
- **Open a company (starter templates)**: five staffed, working domains — personal research, finance
  research, job hunt, content studio, home ops — each with values, hard deontic rules, and roles with
  souls; one click in the Domains panel, yours from the first use.

- **Upgrades now snapshot state, verify the install, and auto-roll back on failure** (one click back on
  the console): before switching versions the updater records the current commit
  (`~/.karvyloop/update_rollback.json`) and backs up your instance state files
  (`~/.karvyloop/backups/`, last 3 kept, honest scope in each `manifest.json`); after install it
  smoke-checks that the new code actually imports, and a broken build is rolled back to the previous
  known-good commit automatically — with the reason stated out loud, never a silent broken restart.
  `POST /api/update/rollback` + `rollback_available`/`prev_version` in the update status payload.
- **`karvyloop export`** — your instance is a folder, now with a button: packs `~/.karvyloop`
  (skills, knowledge, preferences, history) into one portable archive with a self-explaining
  `MANIFEST.txt`; secrets (`config.yaml` with your API keys, `console.runtime.json`, `*.lock`)
  are deliberately left behind. Unpack on the new machine, add your key, `karvyloop console` — home.
- **Idle = 0 LLM calls is now a tested contract** (`tests/test_idle_zero_llm.py`): when nothing
  changed, the daily slow side burns nothing — knowledge consolidation and skill tagging hit their
  watermarks without touching the gateway, and the daily loop's idle path provably exits before any
  LLM work. No overnight heartbeat bills.
- **Windows is now a supported (degraded) platform**: the runtime, console and your own crystallized
  (knowledge-only) skills run fully on Windows; only third-party skill scripts are refused — fail-closed,
  with a clear message explaining the degraded mode (no sandbox on Windows yet; Linux/macOS keep the full
  sandbox). Ships a one-line PowerShell installer mirroring `install.sh`
  (`irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex`): dedicated
  venv under `%LOCALAPPDATA%\karvyloop`, a `karvyloop.cmd` shim on the user PATH, Python 3.11+ guard with
  `py -3.11` fallback.
- **Edit, then accept** on decision cards: kinds with an actionable text field let you fix the proposal
  in place and approve your version — the original→edited contrast feeds decision-preference
  crystallization (the richest taste signal there is), and an edit counts as real judgment for the
  high-stakes gate.
- **Under-the-hood drill-down** on task details: expand any task to see the real actions beneath the
  narration (tool calls and outcomes, projected from the Trace).
- **Kinder first-run**: model-connection failures now say what's actually wrong (bad key / wrong
  endpoint / unreachable) before the raw error, and a local **Ollama** install is auto-detected and
  offered as a one-click, no-API-key path.
- **Semantic tag layer for skill recall** (`tags:` in SKILL.md, matched alongside token overlap — no vectors),
  with a daily slow-side backfill that tags untagged own skills once (untrusted skills untouched).
- **Capability overview** (`/api/capability/overview` + a card in the Skills panel): one table for
  tools × mode floors and skills × trust/network/integrity-lock.

### Changed
- Internal restructuring, no behavior change: the workflow / distill / roundtable engines moved out of
  `console/routes.py` into their own console modules, and the core loop moved from `cli/main_loop.py`
  to the new `karvyloop/runtime/` package (`karvyloop.cli.main_loop` remains as a compatibility shim).

### Removed
- Six unreachable packages from an earlier architecture cycle (`ethos`, `syntonos`, `instance`,
  `onboarding`, `l0`, `bus`, ~2.6k lines): superseded by the current design (verify gate + evaluators,
  per-role paradigm compiler, H2A decision cards, `a2a/` transport) or parked concepts whose designs
  live in the design docs. No live code imported them; recoverable from git history.

### Planned
- **Ingest-time knowledge reconciliation** (fully automatic): new knowledge merges/extends
  near-duplicates, inserts the genuinely new, and meshes the related at ingest — patiently, off the
  hot path, no vectors. (Today the same tidy-up runs as an explicit H2A "consolidate" action.)

## [2026.7.1] — 2026-07-01

**First release.** A local-first, loop-native AI agent runtime you can clone → install → point at a model →
drive in ~15 minutes: run one full **execution loop** (intent → run → verify → crystallize a skill → the fast
brain reuses it) and one full **decision loop** (proposal → decision card → you decide → the preference
crystallizes → it pre-aligns the next proposal). Everything below is what's in this first cut.

### Runtime & safety
- Local web **console** (FastAPI REST + `/ws` WebSocket + static SPA) and a terminal TUI, on a fast/slow-brain
  **MainLoop**; a **Forge** coding executor; a multi-provider **LLM gateway** that meters every token at one
  choke-point (any path that talks to a model is counted).
- Entity model **L0–L4** (tool/skill · atom · role · domain), mirrored field-for-field in `schemas/`.
- **Safety is foundational** — every task carries a capability token (zero-permission start); all
  file/network/process access is checked against it; third-party skills run in a **bubblewrap** (Linux) /
  **Seatbelt** (macOS) sandbox, below the agent's trust boundary. macOS adversarially verified on Apple Silicon.
- **Deterministic self-check** — `doctor` / `status` run **without a model** (config / key / port / deps /
  version / data integrity) and tell you the exact fix in your terms; `doctor --fix` auto-heals the reversible
  cases (e.g. corrupt persisted JSON → backup + reset so it boots). An LLM **ops agent** (`/api/ops/diagnose`)
  reasons on top of doctor's real findings but **never executes** — only the deterministic repair auto-applies.

### Roles, domains & collaboration
- **Roles** (a 7-file soul: identity / character / user / commitment / verify / …), **business domains** (like
  companies) with **sub-domain inheritance**, and **value.md + deontic** governance — hard guardrails (top-down,
  un-overridable) + soft defaults (most-specific-wins) — injected into every route / workflow / scheduled /
  roundtable, no opt-out.
- Domain membership is a **dynamic `member_query`** (weak reference resolved at access). A domain member role
  has a **read-only merged view**: its native paradigm plus the value.md / deontic it inherits from the domain.
- Every role is born a **"resourceful subordinate"** — a default, editable collaboration contract in its
  COMMITMENT layer: pursue feasible goals, exhaust your own resourcefulness before coming back, and bring
  evidence, not "what do I do?". The hard safety floor (budget ceiling / infra-dead stop / fail-loud / verify
  gate) is enforced by the runtime and can't be edited away.
- **Karvy 🦫**, the global assistant, turns plain language into a single hand-off, a **roundtable** (roles think
  in parallel → converge), a **workflow** (multi-step DAG), or an **ops** check — always surfaced as an **H2A
  decision card**, never auto-run. Vague instructions are LLM-decomposed over *real* domain members (never
  fabricated). Up to 50+ participants per roundtable / workflow.
- **Durable workflows** survive a console restart (steps checkpointed; completed steps replay instantly); a
  **full-screen Drawflow canvas** for human orchestration. **Scheduled tasks** in plain language (NL→cron),
  Karvy-only (one audit surface), with a ⏰ dashboard.

### The wedge — crystallization (the moat)
- **Skill crystallization stores the _method_, not the answer** — a recall hit re-executes the learned method
  on the current inputs (CBR-style Revise), never replays a stale answer; only semantically-stable results are
  cached. The payoff is fewer tokens (the slow brain is guided), not a cached reply.
- **Decision-interface crystallization** — the **decision card** translates a proposal into your terms, keeps a
  *verified* region (✓/✗, traceable to a gate) visibly separate from Karvy's narration, pre-aligns the standards
  you've crystallized, and forces judgment before a high-stakes commit. ACCEPTing a dispatch runs the independent
  checker and its real verdict becomes a grounded report card (`inconclusive` is shown honestly, never a fake ✓).
- **Two layers, judged along the accountability chain** — a role answers to *you*, so your decisions evaluate it
  (decision-preference crystallization, RLHF-shaped); an atom answers to its *role*, so the role's objective
  measures (achievement × efficiency, past its verify gate) evaluate it (RLVR-shaped). Evaluation is **off the
  hot path**: a drive only executes and writes facts to the **Trace** (run/eval split); a patient, idempotent
  evaluator reads the Trace to score and writes back — learning never competes with the live task.

### Cognition & knowledge
- **Personal knowledge base** — feed a link or notes; a distill flow (fetch → analyze → refine with Karvy →
  *you* decide → compile to Belief) sinks it. It **never silently writes 0** (a thin/failed fetch says so and
  keeps the todo), folds the key points you add in chat into the material, and **re-feeding the same source
  supersedes** the old version. Every knowledge point shows its **real source** (the link or file).
- **Cognition graph** — an Obsidian / map-style mesh: laid out **by connected component** (compact clusters
  packed together, unconnected notes in a tidy grid), drawing only *real* links (semantic + each node's
  strongest) so it branches instead of hairballing, with map-style zoom-level labels (LOD), hover tooltip, big
  hit targets, and a **click-to-select detail card** (title, full content, source, clickable related nodes).
- **No vector DB** — recall is grep + CJK-bigram + LLM semantic tags + spreading activation over the mesh;
  near-duplicate knowledge is tidied via an **H2A consolidate** (suggest → you confirm), off the hot path.

### Execution & models
- **Fast brain** (crystallized-skill hit → re-run with the method) vs **slow brain** (explore from zero);
  **atoms** (task/daemon) are the one reused ReAct loop; **`create_atom`** lets a role mint a new sub-agent when
  nothing fits — it searches the shared pool first, is born on trial, is merged if a near-duplicate exists, and
  can never silently poison the pool (strict-JSON synthesis, duplicate gate, earn-by-reuse lifecycle).
- A delegated role **pursues its goal within a budget** — re-plans on a failed attempt, fixes-and-retries a
  rejected result, stops immediately and says so when the model/network/sandbox is down (infra-dead vs
  replannable, classified end-to-end), and returns an evidence-carrying infeasibility card, never a silent stall.
- **Providers** — Anthropic, OpenAI-compatible, DeepSeek, Kimi/Moonshot (Global + For-Coding) — config-driven
  (`extra_headers` onboards a header-gated endpoint with no code). **Multimodal input** — attach images or
  text/Markdown files, threaded as content blocks.

### Updates, quality & housekeeping
- **By-version releases** (CalVer) with a **detect-and-notify** update path: a dismissible console banner +
  `karvyloop update`; a **one-click upgrade** that runs stop → install → restart for you and reconnects the page
  (localhost-only, CSRF-guarded, single-flight) — it **never auto-upgrades**. Your data in `~/.karvyloop/`
  survives upgrades (config, beliefs, skills, roles/atoms, decision log).
- **Concurrency-safe** — role-registry and memory writes are lock-guarded, so parallel roles/atoms can't lose a
  write. **Bilingual** (en/zh) throughout, with a parity test. Static assets are served `Cache-Control: no-cache`
  so a deployed frontend change shows on a normal refresh. A wiring test fails CI on any orphan endpoint / dead
  call. **MIT**-licensed; the test suite is self-contained (~1880 passed, optional infra skipped cleanly).
