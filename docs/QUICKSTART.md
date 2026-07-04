# Quickstart — install to your first crystallized skill, in about 10 minutes

> 🌐 **Language**: **English (current)** · [中文](QUICKSTART.zh-CN.md)

This is the shortest honest path from `install` to the moment KarvyLoop starts becoming *yours*: a repeated task turning into a written-down **skill**, and the console showing you the receipt.

**What you need before starting — no surprises:**

- **Python 3.11+** on your machine.
- **Your own model API key.** KarvyLoop ships no bundled model and proxies nothing through anyone's cloud — it talks directly from your machine to a provider *you* choose. Any Anthropic-compatible or OpenAI-compatible endpoint works (`base_url` + key is all it takes). Prefer zero keys? A local [Ollama](https://ollama.com) works too — smaller models mean rougher results, but your data never leaves the house.
- **A supported OS.** Linux is first-class (full sandbox: bubblewrap, hardened with Landlock where the kernel supports it). macOS is supported (built-in Seatbelt sandbox, same fail-closed contract). Windows is supported with honest limits: a restricted-token sandbox isolates writes and caps resources, third-party skills that need network fail-close rather than run unsandboxed, and where the sandbox can't initialize it degrades to first-party-only. Everything outside the sandbox is pure cross-platform Python.

Just want to look around without a key? `karvyloop console --no-llm` starts a read-only console.

---

## Minute 0–2 — Install

One command, isolated from your system Python (safe on PEP 668 "externally managed" distros):

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex
```

The installer creates a dedicated virtualenv and puts a `karvyloop` command on your PATH — nothing else to configure. Re-running it upgrades in place. (Developing against a clone instead? `pip install -e .` — and if `karvyloop` isn't found afterwards, `python -m karvyloop` always works.)

## Minute 2–5 — Connect a model

Start the console:

```bash
karvyloop console --host 127.0.0.1 --port 8766
# open http://127.0.0.1:8766
```

On first run the console shows a **setup screen**: pick where your AI comes from, paste a key, and it **verifies the key actually works** before letting you through.

Built-in presets (each with a "get a key" link): **Anthropic (Claude)** · **OpenAI** · **DeepSeek** · **Kimi / Moonshot** · **OpenRouter** (many models, one key) · **Ollama** (local, no key). Anything not listed uses the generic adapter — any OpenAI-compatible endpoint runs with just a `base_url` + key (unusual endpoints can add `extra_headers`).

Your key is written to `~/.karvyloop/config.yaml` — **on your disk, outside any repo, never uploaded**. Terminal person? `karvyloop init` runs the same wizard in your shell, and hand-editing the YAML is documented in the [README](../README.md#quickstart).

## Minute 5–7 — Your first task: one click on the journey bar

You land in a private chat with **Karvy 🦫**, the built-in assistant — and on a fresh install, a **"✨ Your first 10 minutes"** journey bar sits right above the input with two demo chips. It's the fastest honest way to *see* the flywheel, and nothing in it is pre-recorded: both tasks really run on the model you just configured. (No model yet? The bar says so and sends you to setup first.)

Click the first chip — **📊 Try: analyze quarterly_sales.csv**. It attaches a small bundled sample CSV (exactly the way you would with 📎) and runs it for real.

**What you should see:** it runs — actually runs, in a sandbox, with the tool calls visible — and streams a per-category analysis back. That's the **execution loop**: discover → run → verify → return. Behind the scenes the run was recorded in **Trace**, the append-only log every later judgment is derived from.

## Minute 7–9 — The second chip, and the receipt that matters

The bar now offers step 2 — **🔁 Once more: fastest-growing category** — a *similar* task on the same data. This is where KarvyLoop differs from tools that forget you overnight:

- Watch the chat for the **♻ method-reuse receipt**: the second run didn't start from zero. The method used on run 1 (the bundled `data-analyst` skill) was recalled and guided your new question through the same steps — that receipt is the proof, not a claim.
- The finale message points at your **growth curve**, which just got its first data points — every use accumulates. The **🧩 See my growth curve** chip takes you straight there.

The journey is skippable, and replayable anytime from 🎬 in the top bar.

**Skipped the journey (or upgraded an existing install)?** The manual path shows the same loop. Say something small and concrete — *"list the 5 largest files in my workspace"* — then do the same *kind* of task again ("…in my Downloads folder"):

- **As you type**, related skills and knowledge surface unprompted in a **🧲 Related** panel — pure local matching, zero extra LLM calls.
- **After enough repeats** — as early as the third run for a simple task, once it clears the promotion gates (a verify gate passed, ≥80% success rate, used enough or generalized across variants) — you'll see **🔔 Crystallized: {skill}**. Your repeated ask is now a written-down *method* in a readable `SKILL.md`.
- **On later runs**, the recall receipt: skills marked *stable* replay instantly (**⚡ Fast-brain hit**, zero LLM cost); skills marked *dynamic* (the default) never replay a stale answer — the saved method guides a fresh re-run on today's inputs, and the rerun is logged on the skill's timeline.

Honest note: crystallization is earned, not instant. It deliberately refuses to save one-off flukes — no verify gate, no promotion.

## Minute 9–10 — Open the skills panel

Left nav → **Skills**. This is the "wow" that compounds:

- **📈 Skill-library growth — more like you with every use**: a real curve (skills, promotions, average success rate, reuse hits) replayed from Trace — not a vanity counter. Unused skills honestly decay and are eventually archived; the curve goes down as well as up.
- Each skill has a **Lifeline**: crystallized → revised → rerun, so *"why did my skill change"* always has an answer.
- The **Capability overview** card shows exactly what every skill and tool is allowed to touch.

That's the loop closed once: run → verify → crystallize → recall. From here it compounds on its own.

## Where to go next

- **Build a team** — create a *domain* (like a company) with *roles* (like teammates), then tell Karvy to hand work off. Nothing moves until you accept a **decision card**: the AI proposes, *you* decide, always. The [README's guided first 15 minutes](../README.md#your-first-15-minutes-guided) walks this.
- **[Architecture](ARCHITECTURE.md)** — how the two loops, the entity ladder, crystallization, decision cards, earned silence, Trace and the sandbox actually fit together.
- **[Concepts](CONCEPTS.md)** — the vocabulary in one page.
- **[Philosophy](PHILOSOPHY.md)** — why "loop-native" at all.

## If something breaks

- `karvyloop doctor --fix` — diagnoses the setup, repairs the safe deterministic breakages itself, and probes whether your model endpoint is actually reachable.
- `karvyloop: command not found` → `python -m karvyloop console` (same Python you installed with), or see the [README's PATH notes](../README.md#front-end--back-end).
- Key rejected on setup? Check it's copied in full (no spaces/newlines/placeholder text) and that the provider matches the key's format.
- Still stuck → [open an issue](https://github.com/Caprista/KarvyLoop/issues). Bug reports are gold — this is a pre-1.0 project moving fast.

---

🦫 *Your agent, your data, your rules.*
