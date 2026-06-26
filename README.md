# KarvyLoop

> 🌐 **Language**: **English (current)** · [中文](README.zh-CN.md)

**A local-first, loop-native AI runtime that keeps you in the driver's seat.**

KarvyLoop runs on your own machine. It runs your repetitive work, verifies its own output, and **crystallizes every use into "yours"** — skills, knowledge, decision preferences — while **you stay the one who decides**. The companion chat surface is **KarvyChat**; the global assistant living inside is **Karvy 🦫**.

> Most AI tools sideline you, burn tokens, and feel the same for everyone. KarvyLoop keeps you in the driver's seat, stays affordable, and turns every use into a version of *you* that can't be copied.

---

> ⚠️ **Early and under active development.** KarvyLoop is pre-1.0 and moving fast. Many features
> aren't fully tested yet and rough edges are expected — we're opening it up early to sharpen it
> together. Please bear with us, and **bug reports are gold**. 🙏

### Supported platforms

| OS | Status |
|----|--------|
| **Linux** | ✅ First-class — full security sandbox (bubblewrap). |
| **macOS** | ✅ Supported — native Seatbelt (`sandbox-exec`) sandbox; the same fail-closed contract as Linux (writes confined to the workspace, no network unless granted), verified on Apple Silicon / macOS 26. Newer than the Linux path, so rougher. |
| **Windows** | ⛔ Not yet. |

KarvyLoop is a cross-platform user-space runtime (pure Python; it doesn't ride on the Linux
kernel). The only platform-specific piece is the sandbox.

---

## Why "Loop"

The industry climbed a ladder of paradigms: **prompt engineering → context engineering → harness engineering**. Each rung made a *single* LLM call more reliable. Today's agent runtimes are *harness-native*: they wrap one call in scaffolding — tools, ReAct, retries, guardrails — to make one agent dependable.

KarvyLoop is **loop-native**. The unit of design isn't a call or an agent — it's the whole self-running cycle that finds work, runs it, checks itself, and compounds:

```
discover work → run it → verify independently → COMPOUND (crystallize) → (you decide) → repeat
        ▲                                                                      │
        └──────────────── the loop gets cheaper & more "you" each turn ────────┘
```

That shift sounds incremental. It isn't — because of one uncomfortable fact about loops:

> **The same loop, in two people's hands, produces opposite outcomes.** For one it compounds into leverage. For the other it quietly takes over their judgment, one accepted suggestion at a time, until they can no longer tell whether the output is right — they've been automated out of their own work without noticing. A loop is not neutral. What decides which outcome you get is whether you stay the *engineer* of the loop or slide into being its *spectator*.

Everything below is how KarvyLoop is built so you stay the engineer.

### Two loops, not one

Look closely and a running loop is really *two* loops with opposite natures:

- **The execution loop** — *how to do X.* An atom (L1) does one job and can write a verify gate for its own output. This loop is fully automatable, and it's becoming a commodity — everyone's execution loop converges to roughly the same thing.
- **The decision loop** — *whether, and on whose terms.* This runs between a role (L2) and you. It **can't** be automated, because automating it *is* the failure mode above. It's where your intent, taste, and accountability live.

So KarvyLoop automates the execution loop hard (cheap, fast, self-verifying) and **refuses** to automate the decision loop. The role/atom split in the architecture *is* this cut. **H2A** (the AI proposes, you decide) isn't a setting you can switch off — it's the structural guarantee that the decision loop stays yours.

### Staying the decider takes more than a yes/no button

A veto you can't exercise *intelligently* is theater. To really keep the wheel you have to **understand** the decision — but understanding can't mean "go read the tool logs." The distance between what the system did and what you can tell from its output is what Don Norman called the **Gulf of Evaluation**, and the obvious fixes make it worse:

- **The overtrust trap.** A large review of AI explanations (Microsoft, ~60 studies) found that *more* explanation increases reliance **regardless of whether the answer is correct** — a fluent rationale earns trust it hasn't deserved. "Just explain more" doesn't keep you in control; it lulls you out of it.
- **Over-judgment = no judgment.** Ask for a decision too often and the human either kills the loop or rubber-stamps everything — both are surrender. There's a narrow band between asking too little (silent autopilot) and too much (decision fatigue).

KarvyLoop's answer is a **translation layer** with two hard rules:

1. **Grounded, not trust-inducing.** What the loop claims to have *solved* may be shown as solved **only** when it passed a deterministic verify gate. Everything else is presented as Karvy's *narration* and visibly marked "not verified." Your phone isn't "slow because storage is full" — you're told what was actually checked, and what wasn't.
2. **Decision-forcing, not explanation-dumping.** The interface doesn't try to make you *trust* it; it makes you *judge*. It surfaces rarely (pure verified success doesn't interrupt you), and when it does it puts your own prior standards next to the call, asks you to keep / edit / drop the criteria, and **won't let a high-stakes accept be rubber-stamped**.

Concretely, this is the **decision card**: the problem and approach in your terms; a *verified* region (✓ / ✗, traceable to the gate) kept visibly separate from Karvy's narration; your own crystallized standards pre-aligned beside it; and a gate that stops a high-stakes "accept" you were about to wave through. That — not a checkbox — is what "you stay the decider" is actually made of.

> The verified ✓ region appears for a step that passed an **automatic verify gate**; many steps have nothing to auto-verify, so they show honestly as narration marked "not verified" rather than a fake ✓. That region lights up for more of your work as verify gates get wired into more flows — we'd rather show you less green than green you can't trust.

### The compounding wedge

Two things compound, turn over turn:

- **Skills** — *how to do X.* Every run is observed; once it passes a verify gate and is used enough, it **crystallizes into a persistent skill**. Next time a "fast brain" hits it directly: cheaper, and shaped to you.
- **Decision preferences** — *how you decide.* Your accept / reject / edit choices crystallize into standards that **pre-align** future proposals, so you're asked less and re-explain yourself less. These are deliberately easy to revoke — a contradicting decision weakens or retires one — because the goal is to *fit* you, never to lock you in.

The second kind is the part almost no one else builds. It's why the loop becomes **yours**, not merely good.

### The moat: image vs instance

The code (the **image**) is open and copyable — it's this repo. The **instance** you grow on it — your memory, your skills, your decision style — is yours and can't be copied. Open source and the moat are consistent: we publish the image, never your instance.

---

## Quickstart

**Requirements**: Python 3.11+. Sandboxed skill execution needs Linux + `bubblewrap` or macOS (built-in `sandbox-exec`); everything else is cross-platform.

```bash
# 1) Install (editable)
pip install -e .

# 2) Configure a model (keys live OUTSIDE the repo)
mkdir -p ~/.karvyloop
$EDITOR ~/.karvyloop/config.yaml   # see "Minimal config" below

# 3) Run the local console (web UI)
karvyloop console --host 127.0.0.1 --port 8766
# open http://127.0.0.1:8766
```

**Minimal `~/.karvyloop/config.yaml`** (replace `${ANTHROPIC_KEY}` with an env var or literal key — **never commit a real key**):

```yaml
lang: en
models:
  providers:
    anthropic:
      base_url: https://api.anthropic.com
      auth_header: x-api-key
      messages_path: /v1/messages
      api_key: ${ANTHROPIC_KEY}
      models:
        - id: anthropic/claude
          name: Claude
          api: anthropic-messages
          context_window: 200000
          max_tokens: 8192
agents:
  defaults:
    model: anthropic/claude
embedding:
  model: anthropic/claude
```

> You can also manage models in the console (left nav 🤖 Models). Any Anthropic-compatible endpoint works; OpenAI-compatible endpoints use `api: openai-completions`.
> Just want to see the UI without a model? `karvyloop console --no-llm` (read-only, no key needed).

---

## Your first 15 minutes

No need to understand agents — here's the whole loop, end to end:

1. **Start & connect a model (~5 min).** `karvyloop console` → the setup screen asks where your AI comes from; pick a provider, it shows a "get a key (30s)" link, paste the key, it verifies it works. (Prefer running locally? Pick the local option and follow the install hint.) Your key lives in `~/.karvyloop/config.yaml`, never in any repo.
2. **Talk to Karvy.** In the private chat, ask for something small and concrete. It runs it and returns — that's the **execution loop**.
3. **Make a team.** Left nav → create a business **domain** (like a company) and give it a **role** (e.g. domain "Data", role "Analyst").
4. **Hand off work.** Back in the private chat with Karvy, say something like *"hand the monthly report to the Analyst."* Karvy doesn't barge into the domain — it proposes routing it, and a **decision card** appears under 🤝.
5. **Decide.** The card tells you, in your terms, what it's about and on what basis; it shows a verified region (✓/✗) separate from Karvy's narration; your own standards are pre-aligned beside it. Accept / edit a criterion / reject — your call. It then shows up in 🗳 **Recent calls** so you can look back.
6. **It compounds.** Your accept/reject/edit choices crystallize into **decision preferences** that pre-align future proposals (you're asked less, re-explain yourself less); repeated tasks crystallize into **skills** a "fast brain" reuses — cheaper and more *you* each time.

---

## Updating

KarvyLoop ships **by version** ([CHANGELOG](CHANGELOG.md)), and it tells you when there's a newer one — but it **never upgrades itself**. Detect → notify → *you* decide (upgrading is your call, the same way every decision inside KarvyLoop is).

- **How you find out**: the console shows a dismissible banner when a newer release exists; or run `karvyloop update` anytime. (It's a plain version check against GitHub Releases — no telemetry, no data sent. Turn it off with `KARVYLOOP_NO_UPDATE_CHECK=1`.)
- **How you upgrade**: from a clone → `git pull && pip install -e .`; from PyPI (once published) → `pip install -U karvyloop`. The notifier prints the right one for your install.
- **Your data survives.** Everything you grow lives in `~/.karvyloop/` (config, beliefs, skills, decision log) — outside the repo — and stays across upgrades. A breaking data change always ships with a migration and is called out loudly in the release notes.

---

## Architecture at a glance

**Entity model (an OS-like ladder, mirrored field-for-field in `karvyloop/schemas/`):**
- **L0 Tool / Skill** — a stateless capability unit. Ephemeral one-off tools and crystallized skills both live here.
- **L1 Atom** — the smallest "thinking unit": a single-responsibility agent you can write a verify gate for. This is the **execution loop** (fully automatable).
- **L2 Role** — atoms + a soul (identity/preferences). The role↔human boundary is the **decision loop** (not automatable — where the value compounds).
- **L3/L4 Domain / Sub-domain** — a long-lived "company/department" of collaborating agents, with shared values and guardrails.

**Runtime spine** — a request flows: `console (FastAPI REST + WebSocket) → MainLoop.drive → fast-brain recall (hit = zero LLM) or slow-brain Forge (ReAct) → gateway (multi-provider LLM) → sandbox (bubblewrap) + capability token → streamed back to the UI`.

**Safety is foundational** — every task gets a capability token; all file/network/process access is checked against it; third-party skill scripts run in a sandbox with minimal grants (workspace only, no network unless you explicitly authorize). It sits below the agent's trust boundary — it can't be bypassed.

For the full picture, read the source — it's documented. The map below tells you where to look.

---

## Repository layout

```
README.md / README.zh-CN.md   ← you are here (en / zh)
LICENSE                       ← Apache-2.0
pyproject.toml                ← install / build
karvyloop/                    ← all source
  schemas/        data contracts (single source of types)
  gateway/        LLM gateway + model registry (multi-provider; keys only here)
  context/        token/context governance (compaction, budget, circuit breaker)
  atoms/          L1 atom runtime: the one ReAct loop everything reuses
  coding/         Forge coding executor (delegates to atoms; no second loop)
  capability/ sandbox/ platform/   safety: capability tokens + bubblewrap sandbox
  registry/       tool/skill registry + third-party skill import/sandbox-exec
  crystallize/    ⭐ the wedge: skill crystallization + decision-interface crystallization
  cognition/      Belief / Pursuit / Trace + recall (agentic, no vector DB)
  domain/ a2a/ karvy/ l0/   collaboration: business domains / A2A protocol / Karvy / big group
  roles/ paradigm/ wizard/ adapter/ ethos/ syntonos/ instance/ onboarding/   identity & paradigm layers
  console/        local web console (FastAPI REST + WebSocket + static SPA)
  workbench/ cli/ terminal TUI + main-loop orchestration (fast/slow brain)
  i18n/           bilingual (en/zh) presentation layer
tests/                        ← pytest suite (also the best usage examples)
```

---

## Running the tests

```bash
pip install -e ".[dev]"      # installs pytest / pytest-asyncio / respx / psutil
pytest -q                    # full suite — no flags needed
```

The suite is **self-contained**: it doesn't depend on any non-shipped docs, and optional infrastructure (e.g. the `mcp` package, redis, Linux+bubblewrap) is **skipped cleanly** rather than failing. Expect roughly **1550+ passed / a dozen skipped**. To run everything, on Linux: `pip install -e .` (puts the `karvyloop` CLI on PATH), `pip install mcp`, install `bubblewrap` and `redis`.

> A meta-guard test (`tests/test_suite_self_contained.py`) forbids any test from reading non-shipped docs, so "code-only checkouts stay green" is enforced, not hoped for.

---

## Front-end / back-end

Classic separation: the back end is FastAPI (`/api/*` REST + one `/ws` WebSocket, with auto-generated OpenAPI at `/docs`); the front end is a static SPA under `karvyloop/console/static/` that talks only via `fetch` + WebSocket. You can rebuild the front end in any framework against the same contract with zero back-end changes.

> To expose a hosted/public web front end, you'll first need to add CORS and authentication on the back end — KarvyLoop is local-first by default (bound to `localhost`/LAN is the security boundary).

---

## Contributing

Pull requests welcome. Before submitting: `pytest -q` must be green; user-facing strings go through the bilingual i18n tables (`karvyloop/console/static/i18n.js`). Real API keys never go in the repo — only `~/.karvyloop/config.yaml` (outside the repo); test fixtures use obviously-fake keys.

## License

[Apache-2.0](LICENSE). The code is open; your instance (the data and skills you grow) is yours.

---

🦫 **Karvy** is waiting in the seat next to yours.
