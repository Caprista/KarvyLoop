---
name: security-review
description: >-
  Dev-time security audit of KarvyLoop's OWN source (twin of /code-review, but for
  security). Use when asked to "audit the security of these changes", "审这轮改动的安全",
  "security review this endpoint/diff", or before landing anything that touches the
  untrusted-input frontier (new API endpoints, URL/file fetch, tool authorization,
  sandbox, LLM output that reaches a persistent store or the shell). Runs a
  multi-agent adversarial audit — parallel discovery agents, then ≥3 refutation
  passes per finding (keep only if ≥2/3 say REAL) — and REPORTS ONLY; it never
  edits code. Not a KarvyLoop product feature.
---

# security-review — adversarial security audit of KarvyLoop itself

A dev tool. It finds security seams in KarvyLoop's own code and reports them; the
human decides and fixes. Modeled on the industry "多代理对抗审计" pattern (parallel
finders → independent refuters → synthesis, recording false positives) — that pattern
is worth using precisely because ~60% of naive findings are false positives that
adversarial verification filters out.

## 0. Read ground-truth first (a tested vector is NOT a finding)

Before auditing, read:
- `tests/security/README.md` — the catalog of already-tested attack vectors (SSRF /
  sandbox escape / credential leak / path traversal / …) **and** the honest
  OWASP-LLM-Top-10 coverage table (which rows are `covered` vs `gap`).
- `CLAUDE.md` hard rules (安全是地基不是招牌 / 凭证只在 config.yaml 仓外 / 来源判定注入防御 /
  沙箱三平台 / 宁空勿毒).

If a vector is already tested in `tests/security/`, it is **not** a finding — verify the
coverage actually reaches the code path in question (the SSRF floor, for example, is
tested for `web_fetch` but a *different* fetch path can still bypass it — that gap is a
real finding, the "covered" label is not a blanket).

## 1. Scope

- The pending diff (`git diff`, `git diff --stat`) + the untrusted-input frontier it
  touches. Wire-trace where request/user/tool/web content enters and where it reaches a
  sink (shell, filesystem, network egress, SQL, `innerHTML`, a persistent store, the model).
- Untrusted = anything crossing the access-token/same-origin gate, tool-returned content,
  fetched web pages, imported agents, pasted "material", stored memory read back.
  Legitimate instructions come only from the user's own message + the system framework.

## 2. Discovery — parallel finders, one dimension each

Fan out discovery agents (or, in a single pass, cover each dimension explicitly). Ten
dimensions, each mapped to OWASP LLM Top 10:

| # | Dimension | OWASP LLM |
|---|---|---|
| 1 | Prompt injection (direct + indirect via tool/web/import/stored-memory) | LLM01 |
| 2 | Sensitive-info / credential leak (keys, headers, Authorization in logs/responses) | LLM02 |
| 3 | Excessive agency / authorization (tool can do more than the task; missing authz) | LLM06 |
| 4 | Sandbox escape / arbitrary exec (bwrap/seatbelt/win bypass, shell metachar) | LLM06 |
| 5 | SSRF (URL fetch → cloud metadata 169.254.169.254 / loopback / private nets; redirect→internal) | LLM05 |
| 6 | Path traversal / arbitrary read-write (`../`, symlink, skill-zip resolveKey) | LLM06 |
| 7 | Deserialization / injection (JSON/YAML/pickle, f-string SQL, schema poisoning) | LLM05 |
| 8 | DoS / unbounded consumption (ReDoS, no size cap, memory exhaustion, cost DoS) | LLM10 |
| 9 | Improper output handling (model output → innerHTML/shell/SQL without sanitize) | LLM05 |
| 10 | System-prompt / memory leakage; stale-memory false confidence | LLM07 |

For each candidate: `file:line`, the untrusted source, the sink, the reachability path.

## 3. Adversarial verification (the load-bearing step)

For every candidate finding, run **≥3 independent refutation passes** — each prompted to
*try to prove the finding is FALSE* (default to `FALSE_POSITIVE` when uncertain). Keep the
finding only if **≥2 of 3** conclude `REAL`.

- **Verdict = last-match.** A refuter often restates the author's claim ("the code claims
  to sanitize…") before its own conclusion; a first-match parse gets fooled into a false
  PASS. Take the *last* REAL/FALSE_POSITIVE token as the verdict.
- Common valid refutations that kill a finding: the value is developer-supplied (a call
  param from our own code), not attacker-controlled; a genuine always-on control mitigates
  it (the access-token + same-origin gate; a deterministic hard gate); the sink is
  parameterized; the input is bounded/validated upstream; the vector is already tested.
- A present mitigation *downgrades* severity; it only *kills* the finding if it fully
  closes the path.

## 4. Severity — weighted by real-deployment reachability

`Critical` (remotely reachable, no auth, high impact) / `High` (reachable by any
token-holder or via untrusted content, real impact — e.g. SSRF to metadata, cred leak) /
`Medium` (needs specific conditions) / `Low` (defense-in-depth). KarvyLoop is local-first
single-user: "LAN is not a boundary, the token gate is" — factor that into reachability,
but do not use it to dismiss floors that are meant to hold regardless of who's inside.

## 5. Report format (report-only — never edit code)

```
# Security Review — <scope>
## FINDINGS (survived adversarial verification)
[SEVERITY] <one-line> — file:line
  - Why it's real: <the path from untrusted source to sink>
  - OWASP/catalog: <LLM id; is it a claimed-covered vector being bypassed?>
  - Refutations attempted: <(a)… (b)…> → survives / downgraded
  - Mitigations present: <what limits it> — closes it? no
  - Fix direction (do NOT apply): <bounded, mirror an existing safe pattern>
## REJECTED (false positives — recorded, mandatory)
Rejected — <claim>. Why rejected (N/3): <the refutation that held>.
## Honest coverage statement
Examined: <dimensions>. Found: <N real>. What this pass does NOT cover: <…>.
This narrows where to look; it does not replace a human security review.
```

## 6. Discipline

- **Report only. Never edit code** — the human reviews findings and decides the fix.
- Every claimed finding must survive adversarial verification; record the false positives
  you rejected and why (honesty guards against "automation replaces humans").
- Never print secrets (keys / headers / Authorization) even to demonstrate a leak.
- A tested vector (in `tests/security/`) is not a finding unless coverage genuinely
  misses the path in question.
- No external project names in the report (write "业界做法" / OWASP references).
