# The idea — why "loop-native"

> 🌐 **Language**: **English (current)** · [中文](PHILOSOPHY.zh-CN.md)
>
> This is the full version of the *why* behind KarvyLoop's design. If you just want to run it, the [README](../README.md) is enough — come back when you're curious what makes it different.

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

> **Everyone is building agents that need you less. KarvyLoop builds the agent that learns how you decide — and proves it by never deciding for you.**

## Two loops, not one

Look closely and a running loop is really *two* loops with opposite natures:

- **The execution loop** — *how to do X.* An atom (L1) does one job and can write a verify gate for its own output. This loop is fully automatable, and it's becoming a commodity — everyone's execution loop converges to roughly the same thing.
- **The decision loop** — *whether, and on whose terms.* This runs between a role (L2) and you. It **can't** be automated, because automating it *is* the failure mode above. It's where your intent, taste, and accountability live.

How do you tell which loop something belongs to? One knife, and it never wavers: **"does it carry your responsibility?"** Direction, trade-offs, sign-off → decision loop, stays with you. Purely getting the work out → execution loop, goes to the machine.

So KarvyLoop automates the execution loop hard (cheap, fast, self-verifying) and **refuses** to automate the decision loop. The role/atom split in the architecture *is* this cut. **H2A** (the AI proposes, you decide) isn't a setting you can switch off — it's the structural guarantee that the decision loop stays yours.

One more consequence of taking the decision loop seriously: **if you ever have to ask "how's that task going?", the decision loop has already collapsed** — you've become the system's heartbeat monitor. Roles here work under a *resourceful subordinate* contract: hit a wall, exhaust self-help first (swap plan, swap atom, find or forge a skill); come back only when it's genuinely infeasible — **with evidence** ("tried these three routes, stuck here, need you to decide this"), never a silent hang, never a bare "failed". Blocked work surfaces itself; your attention is spent on *deciding*, not *checking up*.

## Staying the decider takes more than a yes/no button

A veto you can't exercise *intelligently* is theater. To really keep the wheel you have to **understand** the decision — but understanding can't mean "go read the tool logs." The distance between what the system did and what you can tell from its output is what Don Norman called the **Gulf of Evaluation**, and the obvious fixes make it worse:

- **The overtrust trap.** A large review of AI explanations (Microsoft, ~60 studies) found that *more* explanation increases reliance **regardless of whether the answer is correct** — a fluent rationale earns trust it hasn't deserved. "Just explain more" doesn't keep you in control; it lulls you out of it.
- **Over-judgment = no judgment.** Ask for a decision too often and the human either kills the loop or rubber-stamps everything — both are surrender. There's a narrow band between asking too little (silent autopilot) and too much (decision fatigue).

KarvyLoop's answer is a **translation layer** with two hard rules:

1. **Grounded, not trust-inducing.** What the loop claims to have *solved* may be shown as solved **only** when it passed a deterministic verify gate. Everything else is presented as Karvy's *narration* and visibly marked "not verified." Your phone isn't "slow because storage is full" — you're told what was actually checked, and what wasn't.
2. **Decision-forcing, not explanation-dumping.** The interface doesn't try to make you *trust* it; it makes you *judge*. It surfaces rarely (pure verified success doesn't interrupt you), and when it does it puts your own prior standards next to the call, asks you to keep / edit / drop the criteria, and **won't let a high-stakes accept be rubber-stamped**.

Concretely, this is the **decision card**: the problem and approach in your terms; a *verified* region (✓ / ✗, traceable to the gate) kept visibly separate from Karvy's narration; your own crystallized standards pre-aligned beside it; and a gate that stops a high-stakes "accept" you were about to wave through. That — not a checkbox — is what "you stay the decider" is actually made of.

> The verified ✓ region appears for a step that passed an **automatic verify gate**; many steps have nothing to auto-verify, so they show honestly as narration marked "not verified" rather than a fake ✓. That region lights up for more of your work as verify gates get wired into more flows — we'd rather show you less green than green you can't trust.

## The compounding wedge: two loops, two crystallizations

The two loops don't just run — each one **sediments**, and each sediments a different thing:

- **The execution loop crystallizes into skills** — *how you work.* Every run is observed; once it passes a verify gate and is used enough, it **crystallizes into a persistent skill**. Next time a "fast brain" hits it directly: cheaper, and shaped to you.

  And a rule we consider load-bearing: a skill stores **the method, never the answer**. Caching last quarter's competitor list and replaying it six months later isn't memory — it's **poisoning your own library** with stale conclusions. So a skill records *how you do it* (where to look, what to check, what counts as done), and every hit **re-runs the method on fresh inputs**. What compounds is your way of working — answers expire, methods don't.

- **The decision loop crystallizes into taste** — *how you decide.* Your accept / reject / edit choices crystallize into standards that **pre-align** future proposals, so you're asked less and re-explain yourself less. These are deliberately easy to revoke — a contradicting decision weakens or retires one — because the goal is to *fit* you, never to lock you in.

The first kind, others will eventually build. The second is the part almost no one else builds — and it's why the loop becomes **yours**, not merely good.

## The flywheel underneath: how it actually compounds

Crystallization and "you stay the decider" only work if something underneath keeps score honestly. That something is one substrate — the **Trace** — and a deliberate split of speed:

- **One source of truth.** An append-only Trace records everything: what each role and atom *did*, what you and the system *said*, what you and a role *decided*, the *method* an atom used, and what the system *learned* from all of it (learning writes back into the Trace too). Every evaluation derives from the Trace — nothing is scored off to the side. The Trace stays *readable* (so you, and the safety/verify machinery, can audit it); the promise is "your instance can't be lifted out," never "you can't read your own data".
- **Fast and slow, separated.** Running a task is urgent and must not pay for evaluating itself — so a drive only *executes and writes facts to the Trace*, and a patient learner reads the Trace **off the hot path** to score how things went. The fast side never waits on the slow side; the slow side (the patient "becoming more *you*" work) can take its time. (We borrow the *shape* of large-scale RL's actor/learner split — fast actor, patient learner, Trace as the log — **not the mechanism: nothing here trains model weights.**)
- **Evaluated along the chain of accountability.** A role answers to *you*, so **your decisions evaluate the role** (that's the decision-preference half of the wedge). An atom answers to its *role* — you don't even see what it did — so **the role's own objective measures evaluate the atom**: did the sub-goal land, faster, better, past its verify gate? Each layer is judged by the one it's responsible to, all of it read from the Trace.

This is the difference between *remembering what you repeated* (memory) and *learning what actually works for you* (what's truly yours). The methods that prove out start to surface first; each turn the loop gets a little cheaper and a little more yours. The wedge is the promise — this flywheel is the proof it can keep it.

## Your assets, not someone else's agents

Software here is disposable — you describe what you want, it gets written, used, and discarded. What *stays* is the reusable shape underneath, crystallized into a library that's yours: skills (*how to do X*), decision preferences (*how you decide*), and the **roles & atoms** your work is built from.

Already invested in agents elsewhere? Bring them. An imported agent isn't flattened into a file — a model **decomposes it into a role** (its persona) **plus reusable atoms** (its capabilities), dropped into a shared pool any of your roles can compose. Near-duplicate atoms across roles get merged into one canonical atom — *you* confirm the merge, since it's your asset — and each atom is labeled honestly: *executable* (its tools are real and wired) or *advisory* (persona reasoning only), so "it ran" never masquerades as "it works".

That library — your roles, atoms, skills, and decision style — is the part that compounds into leverage and **can't be lifted out**.

## Karvy runs the team; you set the rules

Living inside is **Karvy 🦫**, the global assistant. Tell it in plain language — *"get a few people in Product R&D to analyze the competitor"* — and it works out the shape: who to pull in, and whether this is a single hand-off, a **roundtable** (several roles think in parallel, then converge), a **workflow** (a multi-step pipeline with hand-offs), or an **ops** check on the system itself. It never *does* the work directly: every orchestration becomes a decision card waiting for your confirmation. Execution fans out and self-verifies; the decision stays with you.

## Your instance, your data: image vs instance

The code (the **image**) is open and copyable — it's this repo. The **instance** you grow on it — your memory, your skills, your decision style — is yours and can't be copied. Open source and this promise are consistent: we publish the image, never your instance.

---

🦫 Back to the [README](../README.md) — Karvy is waiting in the seat next to yours.
