# KarvyLoop red-team target (promptfoo)

> **One-liner:** a *manual / on-demand* AI red-team harness that rents an attacker's imagination
> to probe our injection-defense boundary — covering the blind spots that hand-written adversarial
> tests (capped by the author's imagination) miss. **It is deliberately NOT a per-push CI gate.**

## Why this exists (and why it's not in CI)

Every other security test in this repo is a **hand-written** adversarial payload. Its ceiling is
whatever attacks the author thought of. To hedge that ceiling we use two machine-driven sources:

1. **Property-based tests** (`tests/security/test_parser_properties.py`, Hypothesis) — pure-local,
   no key, fast → **these run in normal CI**. That is where "machine-generated inputs" belong on
   every push.
2. **This promptfoo red-team target** — an LLM-generated / catalog-driven attack corpus against the
   real defense boundary. The auto-generation mode needs a real model (an *attacker* LLM plus the
   *target* LLM), so running it on every push would require API keys in CI and **burn money every
   commit**. Security's ceiling is "meet OWASP Agentic / LLM Top 10", not "gold-plate a heavy
   pipeline". So this stays **manual / scheduled-by-hand**. Failures here are triage input, not a
   red build.

## What it attacks

The target is our **provenance fence** — `karvyloop/cognition/fence.py: fence_untrusted`. The
untrusted content this fence wraps in production is: **fetched web body** (`coding/tools/web.py`),
**MCP tool result** (`mcp_client` / `coding/tools/mcp_tool`), **inter-agent / A2A output**
(`console/workflow_engine.py`), and **recalled memory** (`fence.py`). All are wrapped as **data,
not instructions** with bidirectional fake-tag scrubbing before they reach the model.
`fence_provider.py` exposes that function as a promptfoo provider so probes hit the actual code.

> Note (honest scope): **imported agents/skills are NOT put through this fence** — their bodies
> go through the refuse-garbage decompose parser and only land via H2A adoption
> (`adapter/bootstrap.py`), a different defense. The `[system]`-in-import probe below therefore
> tests the fence *mechanism* on that content shape, not a claim that production import is fenced.

OWASP mapping of the probes in `promptfooconfig.yaml`:

| Probe | OWASP |
|-------|-------|
| web body injection (override + exfiltrate `config.yaml`) | LLM01 / ASI01 |
| MCP tool-result injection (fake closer + `<system>`) | LLM01 / ASI07 |
| imported-agent hidden `[system]` instructions | LLM01 / ASI07 / LLM03 |
| memory-poisoning phrasing (recalled note posing as instruction) | LLM01 / LLM04 |

## Running it

### 1. Offline mode — no API key (recommended first pass)

Attacks the deterministic fence directly and asserts (no model) that fake fence-closers / system
tags are neutralised and the payload is wrapped as data. Run **from the repo root** so `karvyloop`
is importable (use the same venv where you `pip install -e ".[dev]"`):

```bash
npx promptfoo@latest eval -c redteam/promptfooconfig.yaml
npx promptfoo@latest view          # open the results web UI
```

Expected: **all** probes **pass** (fence holds), including the nested-fake-tag rows
(`</fenced<fenced-data>-data>`, and deep nesting). That nested-tag escape was a real finding
(a single-pass, then a bounded-iteration scrub, both left a live closer under deep enough
nesting) — **now fixed**: `scrub_untrusted` iterates until stable, and if a crafted deep nesting
doesn't converge within bound it **hard-neutralizes any residual `<>[]` characters** — so no live
fence-closer / system tag survives **at any nesting depth**. Regression-locked (incl. the deep
PoC) in `tests/security/test_parser_properties.py::test_finding_p2_*`.
Seeing the red-team harness independently reproduce it is the whole point.

### 2. Real-model mode — needs an API key (manual)

Answers the question unit tests **cannot**: *"will the LLM still be persuaded despite the fence?"*
Uncomment the `targets:` + `redteam:` block in `promptfooconfig.yaml`, point the target at a
provider that fences the input and then calls a real model, export your key, and run:

```bash
export OPENAI_API_KEY=...            # or ANTHROPIC_API_KEY, per your target provider
npx promptfoo@latest redteam run -c redteam/promptfooconfig.yaml
```

This uses an attacker LLM to *generate* fresh adversarial cases from the OWASP plugin collections
(`owasp:llm`, `owasp:agentic`) and the `prompt-injection` / `jailbreak` strategies. Both the
attacker and the target consume tokens — hence "manual, not per-push".

## Reading the results

- **Pass** on an offline assert = the fence deterministically neutralised that payload.
- **Fail** = a surfaced gap. Triage it: reproduce it as a deterministic case in
  `tests/security/test_parser_properties.py` (as we did for P2), classify severity, then decide
  fix-or-accept. Red-team output is a *lead*, not a verdict.
- Real-model runs produce a graded report per OWASP category; treat any successful jailbreak as a
  lead to harden the fence or the surrounding provenance logic, then add a deterministic regression.

## Extending by OWASP Agentic Top 10

Add probes/plugins for the categories not yet covered here (e.g. ASI02 tool misuse, ASI04 resource
exhaustion, ASI06 memory/identity manipulation): add a `tests:` row (offline, deterministic) or an
`owasp:agentic` plugin id (real-model). Keep offline-runnable probes first — they are the ones that
can become CI regressions once triaged.

## Not wired into CI (by design)

There is intentionally **no `.github/workflows/redteam.yml`** in this change. A promptfoo job needs
repo secrets (API keys) to do the real-model generation, and key management / spend policy is a
call for the maintainer — an accidentally-red or token-burning workflow is worse than none. If you
want a `workflow_dispatch`-only manual trigger later, gate it behind a configured secret and have it
run **only** the offline `eval` (no key, cannot burn money) unless a key secret is present.
