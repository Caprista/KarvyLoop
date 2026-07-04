# Concepts — the KarvyLoop vocabulary in one page

> 🌐 **Language**: **English (current)** · [中文](CONCEPTS.zh-CN.md)

Ten-ish words carry this whole product. Each entry below says what the thing *is* and — more importantly — *why it exists*. For how they fit together mechanically, see [ARCHITECTURE.md](ARCHITECTURE.md); for the worldview behind them, [PHILOSOPHY.md](PHILOSOPHY.md).

---

### Loop (and why "loop-native")

The unit of design: the whole self-running cycle — *discover work → run → verify → compound → (you decide) → repeat*. Most agent tooling optimizes one LLM call or one orchestration; KarvyLoop optimizes the cycle that *repeats*, because your report is due again next Monday. A loop that repeats can compound — and compounding is the entire point.

### Execution loop / Decision loop

One loop is actually two, split by a single question: **does this carry your responsibility?** *How* to fetch and diff the numbers is the **execution loop** — automated as hard as possible, self-verifying, retryable. *Whether* the report goes out under your name is the **decision loop** — deliberately never automated. The split exists because collapsing the two is how AI tools quietly automate people out of their own judgment.

### Atom

The smallest thinking unit (L1): an agent with exactly one responsibility, narrow enough that a **verify gate** can be written for it (*"fetch two CSVs and diff them"*). Atoms exist because of an honest rule: if you can't check it, you can't safely automate it. Atoms answer to their role, are judged by objective outcomes, and are usually minted at runtime when no existing atom fits — born provisional, kept only if they prove out.

### Role

An agent with a soul (L2): identity, preferences, and its own domain-scoped memory — an *Analyst*, a *Reviewer*. The role is the interface that carries responsibility toward *you*: it plans, delegates to atoms, and replans when they fail. Roles exist so that accountability has an address — you judge roles by your feedback; roles judge their atoms by results. Your Analyst gets sharper *at being your Analyst*, not just generically smarter.

### Domain

A long-lived "company" or "department" (L3/L4) that roles belong to: shared values, hard rules, private memory. Domains exist because real collaboration needs a boundary — knowledge that belongs to *Data Team* shouldn't leak into *Family Finances*, and a rule like *"never place a trade"* must outlive any individual task. Domain hard rules are enforced as deterministic gates on tools, not as prompt suggestions.

### Skill

Your method, written down: a readable `SKILL.md` distilled from repeated, verified work. **A skill stores the method, never the cached answer** — replaying a six-month-old result is poison, so a recalled skill re-runs its method on today's inputs (only skills explicitly marked semantically-stable replay). Skills are why the 40th run is cheaper and more *you* than the 1st — and they live in your instance, where no one else can copy them.

### Crystallization

The process that turns *use* into *assets* — the product's wedge. It runs twice: repeated runs crystallize into **skills**, and your accept/reject/edit choices crystallize into **decision preferences**. Promotion is earned through two gates in strict order — (1) *eligibility*: a verify gate exists and has passed; (2) *value*: used enough, recently enough, with a ≥80% success rate. The gates exist because saving every fluke would poison the library — an unverified "skill" is just a superstition with a filename.

### H2A

*Human-to-Agent*: the structural rule that **the AI proposes, you decide**. Its core invariant is enforced in code, not in a prompt: the system can generate `REJECT` or `DEFER`, but an `ACCEPT` can only originate from you. H2A exists because a system that acts on your behalf without your sign-off doesn't augment your judgment — it replaces it, one silent default at a time.

### Decision card

The concrete artifact of H2A: a card stating what's proposed and **on what basis** — with machine-verified claims marked ✓/✗ *separately* from the model's own narration (unverified stays honestly "not verified"), and your past standards pre-aligned beside the call. It exists because research shows more AI explanation increases trust *whether or not the answer is right* — so the card separates evidence from eloquence. Every card you decide lands in an append-only audit log.

### Decision preference

The quieter crystallization: a durable record of *how you decide* — *"never email a client directly"*, *"keep summaries under 200 words"* — distilled from your accepts, rejects and edits, and shown beside future cards. Preferences exist so you stop re-explaining yourself: the 10th proposal arrives pre-aligned with what you corrected on the 3rd.

### Earned silence

The counterweight to H2A: per kind of decision, the system can *earn* permission to stop asking — but only past statistical gates designed to be hard to game (Wilson 95% lower bound ≥ 0.90, both approve- and reject-direction accuracy, unannounced audits, 30-day expiry, hard blast-radius caps — and irreversible actions *never* qualify). It exists because a system that interrupts you forever gets ignored — but silence must be earned with evidence, granted explicitly by you, and revoked on a single miss.

### Trace

The append-only record of everything that runs: tasks, tool calls, verifications, evaluation facts. Trace is the **single source of every judgment** in the system — crystallization gates, satisfaction scores, growth curves all *derive* from it, off the hot path. One scorekeeper exists so there's no second ledger to disagree with — and so "why did the system decide that?" always has a replayable answer.

### Tool

A fixed, primitive action (L0): six built-ins (`run_command`, `read_file`, `write_file`, `edit_file`, `web_search`, `web_fetch`) plus whatever MCP servers you connect. Tools are identical for every user and deliberately never grow — the floor of what can physically happen. The contrast with skills is the point: tools are the same for everyone; what you *build from them* is yours.

### Image vs instance

The **image** is this repo — code, open, copyable by anyone. The **instance** is what grows when *you* run it: your skills, beliefs, decision preferences, role memory, history. Someone can clone the image in a minute; they cannot clone the instance, because it's made of your use. This distinction is why the code can be open while the value stays yours — *your agent, your data, your rules*.

### Fast brain / slow brain

Two speeds of the same runtime. The **fast brain** is recall: matching your ask against crystallized skills with local text matching (no vector database) — a stable hit costs zero LLM calls. The **slow brain** is Forge, the system's single ReAct reasoning loop, used when there's nothing to recall — and its successful work is what the fast brain crystallizes from. The split exists so repetition gets *cheaper*: pay for thinking once, reuse the method for pennies.

### Capability token & sandbox

The deterministic floor under everything: every task carries a **capability token** stating what it may touch (a tool not in the policy table is denied by default), and third-party code runs inside an OS-level **sandbox** (bubblewrap+Landlock on Linux, Seatbelt on macOS, restricted-token + Job Object + AppContainer network-deny on Windows — fail-closed where a mechanism is missing). It sits *below* the model's trust boundary because safety that depends on the model behaving isn't safety — it's hope.
