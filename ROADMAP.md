# Roadmap

KarvyLoop ships **by version** (see [CHANGELOG](CHANGELOG.md) / [RELEASING](RELEASING.md)).
This file is the cut line: what a version is *for*, and — just as important — what
it deliberately leaves out. A roadmap with an explicit "not now" list is a sign the
product has a plan; "perfect" would just mean we'd run out of ideas.

Current: **2026.6.26**.

## Release strategy (decided)

The repo stays **private** through the 0.x runway and **goes public at v0.9.0** (a
release-candidate gate); **v1.0.0 is the public launch**. So 0.3 → 0.9 is the build
runway, 0.9 is "open + harden in the open", 1.0 is the debut. Consequence: the
update-check banner reads GitHub's **anonymous** Releases API, so it stays dormant
(404 on a private repo) **until the repo is public — this is expected, not a bug**.

## The cut principle

**Sharpen the wedge; everything else is good-enough-and-on-camera.** A release has
to let a stranger *experience the thesis* — "I'm in the driver's seat, it works for
me, every use compounds into a more-me version of me." Experiencing it = done. Every
corner being perfect is not the bar.

## v1.0 — "you can feel the thesis"

**Ship criterion:** a stranger can clone → install → configure a model → in ~15
minutes run one full **execution loop** (intent → run → verify → crystallize a skill
→ next time the fast brain hits it) **and** one full **decision loop** (proposal →
decision card → you decide → preference crystallizes → it pre-aligns the next
proposal) — with tests green and no dead/half-wired surfaces.

### In (must be true)
- Runs: install / configure (incl. no-key guided setup) / console + TUI. ✅
- Execution loop compounds: Forge → verify gate → skill crystallization → fast-brain reuse. ✅
- Decision loop (the moat): decision card (verified vs narrated, keep/edit/drop,
  pre-aligned standards, judgment forced before high-stakes commit) → decision-
  preference crystallization → pre-align. ✅
- Local-first + safety: keys outside the repo, sandbox + capability tokens, third-
  party skills sandboxed. ✅
- **Versioning & updates**: by-version releases, changelog, detect-and-notify update
  path (never auto-upgrade). ✅ (0.2.0)
- Open-source hygiene: Apache-2.0, bilingual README with the real philosophy,
  self-contained green test suite. ✅

### v1.0 punch-list — **all 5 done ✅** (shipped to `main`, verified on VM)
1. ✅ **Decision card — honest scope (option A).** v1.0 sells what is *already true*:
   pre-aligned standards, rare surfacing, no rubber-stamping, honest "not verified".
   The "verified ✓" region is presented as a capability that **lights up as verify
   gates get wired into more flows** — not faked with fixtures. _(The natural
   producer of grounded cards — the execution-result report card — is **1.1**.)_
2. ✅ **First 15 minutes — guided onboarding + the wedge walkthrough.**
   - **Onboarding (decided):** zero-barrier means *no agent/engineering expertise* to
     use or maintain — **not** zero setup. The one unavoidable step for a sovereign
     tool is your own key; minimize it to a one-screen, hand-held flow: detect no key →
     pick provider → **per-provider "get a key in 30s here" guidance (incl. where/how to
     buy)** → paste → **live-validate** → in. Promise: **"own your AI in 5 minutes."**
     A key is a one-time toll, not the permanent operator-tax OpenClaw charges.
   - **Local model: supported & guided, just never the default.** A weak local model on
     a low-spec machine makes the must-be-flawless cold-start *worse*, not better — and
     our thesis needs a strong model — so it's **not** auto-bundled/default. But it's
     fully **supported**: if a user wants to run locally, onboarding offers a **guided
     install** path (detect/help set up Ollama etc.). Opt-in, never forced.
   - **Walkthrough:** a written README path (create a domain + role → "hand it to
     <role>" → see the decision card → decide → see Recent calls) so a stranger triggers
     the wedge without guessing.
3. ✅ **Dead-button audit** — swept; deterministically verified every frontend
   `/api/*` call resolves to a real route, no dead handlers, the `未接 registry`
   returns are honest graceful-degradation guards (not dead buttons). Domain/role
   edit (a prior gap) is wired.
4. ✅ **Empty instance isn't sad** — the cockpit's fresh-load empty states now guide
   ("decisions for you show up here — try handing a task to a role") instead of "—"
   / jargon, bilingual.
5. ✅ **`doctor` + `status` — deterministic self-check (zero-barrier *repair*, Layer 0).**
   Zero-barrier isn't only about *using* — it's about *fixing*. A no-model diagnostic
   that always runs (config / key / port / deps / version / data-dir integrity) and
   tells the user **what's wrong and the exact fix, in their terms**. It must work
   *without* the model, because the most common breakage (no key, model unreachable,
   bad config) is exactly when an agent can't help. Ties up #3/#4.

## Committed direction: the self-healing ops agent (Layer 1)

**Zero-barrier maintenance is a first-class goal, not a nice-to-have.** As long as the
model is alive, KarvyLoop should carry a precise ops agent that *diagnoses and repairs
itself* — not operator tooling a human reads and acts on (that's OpenClaw's model),
but a system that maintains itself and explains/fixes in your terms. This is the
conservation-of-complexity move applied to upkeep: the maintenance burden moves off
the user onto (the ops agent = rented model) + (the deterministic doctor = our code).

Two non-negotiable constraints:
- **Under H2A.** An agent with write access to fix the system is the most dangerous
  thing we'll build. Reversible / low-stakes repairs may auto-apply; risky /
  irreversible ones **surface as a decision card** — the ops agent is just another
  caller of the decision-card mechanism. "Auto-fix" must never mean "silently changes
  your system."
- **Layered on the deterministic doctor (#5).** The agent needs a live system + model
  to run; the doctor below it covers exactly the cases where that's gone.

_Which slice lands in which version is TBD — but the direction is committed. It must exist._

**Progress:**
- ✅ **Slice 1 — `doctor --fix` (deterministic auto-repair).** The reversible/low-stakes
  subset auto-heals (today: corrupt persisted JSON → backed up to `<name>.corrupt.bak`
  + reset so the system boots; the most common silent breakage). Risky fixes (no key,
  missing dep, port conflict) stay listed for *you* — never auto-applied. Zero-model,
  so it works when the model's down. This is the L0→L1 bridge the LLM agent plugs into.
- ✅ **Slice 2 — the LLM ops agent** (`ops_agent.diagnose` + `GET /api/ops/diagnose` +
  a "🩺 Diagnose this" affordance on system_error). Grounded on doctor's real findings,
  uses the live gateway, strict-parse (refuse garbage), and **diagnoses/proposes but
  never executes** — the only auto-execution stays the deterministic `repair_finding`.
  No model → graceful no-model fallback (the bootstrap paradox: L0 covers that). Proven
  live on the VM with real minimax (plain-terms cause + stepwise fix + risk tag).
- ▢ **Slice 3** — surface diagnoses as proper H2A proposal/decision cards (not just on
  system_error); capture richer runtime-error signals (scrubbed) as input.

## Post-1.0 (deliberately deferred)

- ✅ **Execution-result report card** (done, brought forward from 1.1) — grounded ✓
  now has a natural producer: ACCEPTing a route_to_role/run_task proposal runs the
  **independent checker**, and its real Verdict becomes a read-only report card.
  **The honesty constraint is satisfied by construction:** only the dispatch path runs
  the rigorous checker, and only *its* verdict feeds the card — `passed&¬inconclusive`
  → ✓ solved; a real fail → ✗ with the reviewer's note; `inconclusive` → unverifiable,
  never a faked ✓. (The non-rigorous "slow-brain success" auto-mark never reaches a
  card.) **Proven live on the VM** with a real model: a genuine grounded ✗ + the
  checker's actual critique. `should_surface` compaction (pure ✓ → one line) is in.
- **Unverifiable-card engagement signal** — today only criterion edit/drop counts as
  "engaged"; unverifiable cards have no criteria, so they can't register engagement.
- **Group collaboration depth** (roundtable / workflow) and proactive prediction —
  present and good-enough; not deepened for 1.0.
- **Render polish** — token-by-token streaming, diff views, syntax highlighting.
- **Distribution** — PyPI auto-publish CI; one-click in-app upgrade; automated data-
  schema migration framework (beyond today's tolerant load).
- **Hosted / multi-user / public web** — conflicts with local-first; explicitly out
  for the foreseeable roadmap.
