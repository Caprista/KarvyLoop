# KarvyLoop Security Posture — Self-Assessment

> **What this document is.** A self-assessment of KarvyLoop against two recognized
> industry baselines — the **OWASP Top 10 for LLM Applications (2025)** and the
> **OWASP Top 10 for Agentic Applications (2026, ASI01–ASI10)** — with every
> "covered" claim backed by a `file:line` pointer into this repository and an
> adversarial test you can run yourself. It also contains a one-page threat model.
>
> **What this document is not.** It is **not a certification, an audit report, or a
> compliance claim**. KarvyLoop has not undergone an external security audit or a
> third-party red team. Where we have gaps, they are named below and tracked as
> roadmap items — not papered over.

**The honest one-paragraph posture:** the *execution-layer* foundations are solid —
OS-level sandboxing on all three platforms, deterministic capability floors,
sensitive-path denial, credential hygiene, and a hard rule that irreversible actions
are never auto-approved — and they are held in place by an adversarial security
suite (`pytest -m security`, **342 test cases across 18 modules** as of 2026.7).
The *content-injection* layer (treating all untrusted text as data, never as
instructions) was unified more recently behind a single deterministic fence at
every ingress. We consider the design sound and the floors tested; we do **not**
claim "standard compliant" or "certified" — see [Known gaps](#known-gaps--roadmap)
for exactly what stands between the current state and that claim.

**Verify it yourself:**

```bash
pip install -e ".[dev]"     # + ".[dev,relay,mcp]" for full coverage
pytest -m security          # the entire adversarial suite
```

The per-attack-vector catalog (26+ vector classes, each with the test that
exercises it) lives in [`tests/security/README.md`](../tests/security/README.md).

---

## 1. Security design principles

Four principles, each enforced in the execution path (a floor, not a feature):

1. **Local-first = data sovereignty.** Your instance, memory, skills, and keys live
   on your machine (`~/.karvyloop/`, outside the repo —
   `karvyloop/llm/config.py:85`). Nothing is uploaded by default; the optional
   cross-device relay is end-to-end encrypted and sees only ciphertext
   (`karvyloop/relay/server.py:207-209`).
2. **H2A — the human approves.** Consequential actions surface as decision cards
   and execute only on your ACCEPT (`karvyloop/console/proposals.py:34`).
   Auto-approval must be *statistically earned* (Wilson 95% lower bound ≥ 0.90,
   `karvyloop/karvy/silence.py:67-71`) and **irreversible actions — sends,
   deletes, payments — are permanently excluded from it**
   (`karvyloop/karvy/silence.py:125,173`).
3. **Provenance-based injection defense.** Legitimate instructions come only from
   the user and the system framework. Web pages, MCP results, imported agents,
   and agent-to-agent messages are *data*, wrapped in a deterministic fence with
   fake-tag scrubbing before they ever reach a model
   (`karvyloop/cognition/fence.py:155`).
4. **Sandboxed execution, default-deny.** Model-driven code execution is confined
   by a real OS sandbox on all three platforms (Linux bubblewrap, macOS Seatbelt,
   Windows restricted token + AppContainer), with token-gated mounts, no network
   by default, and resource caps (`karvyloop/sandbox/mounts.py:11,48`).

---

## 2. OWASP Top 10 for Agentic Applications (2026) — self-assessment

Status legend: **covered** = deterministic mechanism in the execution path *plus*
adversarial tests; **partial** = real mechanism exists but coverage or depth is
honestly incomplete; **gap** = not addressed yet. Line numbers are starting
points, current as of 2026.7.

| # | Risk | Status | Evidence & honest notes |
|---|------|--------|-------------------------|
| **ASI01** | Agent Goal Hijack | **covered** | Unified untrusted-content fence at every ingress: web content `karvyloop/coding/tools/web.py:233`, MCP results `karvyloop/coding/tools/mcp_tool.py:35` + `karvyloop/mcp_client.py:265`, agent-to-agent / workflow upstream `karvyloop/console/workflow_engine.py:279`; provenance rule + fake-tag scrubbing `karvyloop/cognition/fence.py:107-155`. Even a hijacked model hits deterministic floors it cannot talk its way past: the deontic gate (`karvyloop/capability/decision.py:177`) and H2A cards. Tests: `tests/test_untrusted_fence.py`, `tests/test_deontic_gate.py`. *Residual:* "is the model still persuaded despite the fence" is real-model red-team territory → roadmap G4. |
| **ASI02** | Tool Misuse & Exploitation | **covered** | Per-tool least-privilege floor with strictest-by-default (an undeclared tool requires FULL mode → denied at lower tiers) `karvyloop/capability/policy.py:35`; single authorize chokepoint `karvyloop/capability/decision.py:131`; SSRF floor on tool-driven fetch (cloud metadata, loopback, private ranges, schemes, redirect re-validation) `karvyloop/coding/tools/urlguard.py:76`; sensitive-path floor denies secrets to every tool, immune to bypass, `karvyloop/capability/fs_grants.py:46` + `karvyloop/capability/decision.py:89-92`, re-checked at the tool boundary `karvyloop/coding/tools/bash.py:93`. Tests: `tests/security/test_ssrf.py`, `tests/test_fs_grants.py`, `tests/test_run_command_sensitive_floor.py`, `tests/test_capability_web_mcp.py`. |
| **ASI03** | Identity & Privilege Abuse | **covered** *(single-user scope)* | KarvyLoop is a single-user, local-first runtime — there is deliberately no cross-user identity plane. Within that scope: per-execution capability tokens (`karvyloop/capability/token.py`); independent checkers get a write-stripped read-only token `karvyloop/sandbox/mounts.py:27` (`tests/test_readonly_token.py`); MCP-server self-declared metadata never participates in authorization (`tests/test_mcp_remote.py`); non-loopback console access requires a per-start minted token; read-scope remote sharing is default-deny allow-list (`tests/test_external_audience_gate.py`); real keys live only outside the repo `karvyloop/llm/config.py:85` and are scrubbed from logs/exceptions/repr (`tests/test_mcp_remote.py`). *Residual:* agents run as your OS user — isolation is the sandbox, not separate OS identities. |
| **ASI04** | Agentic Supply Chain | **partial** | Third-party skills are untrusted by default — no network without an explicit user grant, corrupt grants fail-safe deny (`karvyloop/capability/skill_grants.py:24-38`, `tests/test_skill_net_grant.py`); a full-directory sha256 **integrity lock** rejects tampered third-party skills at load, fail-loud (`karvyloop/registry/skill_lock.py:82,105`, enforced at `karvyloop/crystallize/skill_index.py:91` and `karvyloop/crystallize/recall.py:23`; `tests/test_skill_lock.py`); MCP servers are untrusted by default. **Gap:** the lock proves *post-import integrity*, not *origin authenticity* — no publisher signature / signed provenance for imported skills or agents → roadmap G2. |
| **ASI05** | Unexpected Code Execution | **covered** | All model-driven execution is confined by a real OS sandbox on three platforms: Linux bubblewrap + Landlock + fail-closed egress-allowlist proxy (`karvyloop/platform/linux/bubblewrap.py`, `landlock.py`, `egress_proxy.py:17`); macOS Seatbelt default-deny profile mirroring the sensitive-path floor (`karvyloop/platform/darwin/seatbelt.py:65`); Windows restricted token + Job caps + no-net AppContainer/LowBox (`karvyloop/platform/win/restricted.py`, `appcontainer.py:6-16`). Token-gated mounts, no-net default, fork/memory/timeout caps (`karvyloop/sandbox/mounts.py:11,48`). Tests: `tests/test_sandbox.py`, `tests/test_win_sandbox.py`, `tests/test_seatbelt_profile.py`, `tests/test_egress_allowlist.py`. *Honest note:* when Windows can't even provide a restricted token, degraded mode refuses to run third-party skill scripts at all rather than run them uncaged, and honestly reports "no isolation available" (`karvyloop/platform/win/degraded.py`). |
| **ASI06** | Memory & Context Poisoning | **partial** | Recalled memory passes the same fence + fake-tag scrub before re-entering context (`karvyloop/cognition/fence.py`, `tests/test_cognition_memory.py`); LLM-output parsers feeding persistent stores refuse garbage (strict JSON, empty-on-failure — never prose-scrape into the knowledge base); an update that would overturn pinned / human-reviewed memory escalates to an H2A conflict card instead of silently superseding (`karvyloop/console/proposals.py:191`); **no vector DB / embeddings by design** — the RAG-poisoning surface does not exist here. **Gap:** no adversarial poisoning corpus yet; poisoning tests not yet `security`-marked → roadmap G3. |
| **ASI07** | Insecure Inter-Agent Communication | **covered** | Internal agent-to-agent / workflow upstream outputs pass the neutral data fence (`karvyloop/console/workflow_engine.py:279`; contagion negative-tests `tests/external_runtime/test_a2a_contagion_negative.py`). Cross-device: X25519 handshake + ChaCha20-Poly1305 AEAD, strictly-increasing seq (replay/rollback rejected), AAD binds the frame header (`karvyloop/relay/e2e.py:12-18`); the relay is a stateless blind forwarder that only ever sees ciphertext (`karvyloop/relay/server.py:207-209`); pairing codes are one-time and burn on use (`karvyloop/relay/pairing.py`). Tests: `tests/test_relay.py`, `tests/test_relay_pairback.py`. |
| **ASI08** | Cascading Failures | **partial** | Fail-loud discipline: task failures push to the human decision surface instead of dying silently (`karvyloop/console/task_events.py:1`); deterministic infra-dead detection stops blind retry loops; runaway loops hit the token spend brake and context ceiling at the single gateway chokepoint (`karvyloop/gateway/client.py:24,114-117`); earned-silence batch watermark caps auto-approval blast radius (`tests/test_silence.py`). **Gap:** no dedicated cascade / chaos scenario suite (multi-agent failure propagation under adversarial conditions) → roadmap G5. |
| **ASI09** | Human-Agent Trust Exploitation | **covered** | The H2A decision card is the spine of the product: consequential actions are ACCEPT-gated with evidence shown (`karvyloop/console/proposals.py:34-99`); auto-approval must be statistically *earned* — Wilson 95% lower bound ≥ 0.90 at n ≥ 35, with unannounced spot checks (`karvyloop/karvy/silence.py:67-71`); irreversible semantics (send / delete / pay / go-live) are doubly excluded and **never** auto-approved (`karvyloop/karvy/silence.py:125,173,362`); the "farm a fake approval streak to earn silence" attack is adversarially tested (`tests/test_silence.py`). *Residual:* no systematic review of card presentation against UI dark patterns → roadmap G6. |
| **ASI10** | Rogue Agents | **partial** | Every agent action funnels through the one authorize chokepoint — there are no side doors (`karvyloop/capability/decision.py:131`); an append-only Trace records every run with provenance and a threaded run-id (`karvyloop/cognition/trace.py:1-7`); scheduled tasks can only be created through the system's single scheduler surface (one audit face); budgets and timeouts bound a runaway agent. **Gap:** no behavioral anomaly detection or dedicated rogue-agent kill switch beyond budgets + H2A; imported-agent origin authenticity shares the ASI04 gap. |

**Agentic tally: 6 covered · 4 partial · 0 gap.**

---

## 3. OWASP Top 10 for LLM Applications (2025) — self-assessment

This table extends the test-oriented mapping in
[`tests/security/README.md`](../tests/security/README.md) with code evidence.

| # | Risk | Status | Evidence & honest notes |
|---|------|--------|-------------------------|
| **LLM01** | Prompt Injection | **covered** | Same unified fence as ASI01 (`karvyloop/cognition/fence.py:155` and the ingress chokepoints listed there); MCP tool descriptions treated as data, control chars stripped, length-capped (`tests/test_mcp_remote.py`); the deontic gate emits no prompt text so there is nothing to double-inject (`tests/test_deontic_gate.py`). *Residual:* model persuasion despite the fence → roadmap G4. |
| **LLM02** | Sensitive Information Disclosure | **covered** | Sensitive-path floor — secrets (`~/.karvyloop/config.yaml`, `.ssh`, `.aws`, `.env`, …) are never granted, immune to bypass, never even surfaced as an approval card (`karvyloop/capability/fs_grants.py:46,116-142`, `tests/test_fs_grants.py`); credential scrubbing in logs/exceptions/repr (`tests/test_mcp_remote.py`); E2E relay never sees plaintext (`tests/test_relay.py`); external read-scope sharing is default-deny (`tests/test_external_audience_gate.py`). |
| **LLM03** | Supply Chain | **partial** | Same as ASI04: untrusted-by-default skills + integrity lock (`karvyloop/registry/skill_lock.py`) — tamper detection is in place; **origin signature verification for imported agents/skills is the remaining gap** → roadmap G2. |
| **LLM04** | Data & Model Poisoning | **partial** | Same as ASI06: refuse-garbage parsers + H2A conflict cards + no vector store; **no adversarial poisoning corpus** → roadmap G3. |
| **LLM05** | Improper Output Handling | **covered** | Model output that becomes an action always hits a deterministic floor first: SSRF guard on URLs (`karvyloop/coding/tools/urlguard.py:76`), sandbox on exec (`ASI05` row), deontic gate on forbidden tool actions (`karvyloop/capability/decision.py:177`), sensitive-path floor on file access. |
| **LLM06** | Excessive Agency | **covered** | Least-privilege capability tokens with strictest-default policy (`karvyloop/capability/policy.py:35`); domain-level deterministic forbids immune to FULL mode (`karvyloop/capability/deontic_gate.py:296`); irreversible actions never auto-approved (`karvyloop/karvy/silence.py:125,173`); the human stays on the decision loop (H2A). |
| **LLM07** | System Prompt Leakage | **gap** | No dedicated test that the system prompt / persona cannot be exfiltrated. → roadmap G1. |
| **LLM08** | Vector & Embedding Weaknesses | **n/a (by design)** | KarvyLoop uses no vector DB and no embeddings (recall = grep + token-overlap + LLM tags). The attack class has no surface here. |
| **LLM09** | Misinformation | **partial** | Web tools return honest failure instead of fabricating results (`tests/test_web_tools.py`); knowledge-base writes are quality-gated. Not yet `security`-marked → roadmap G7. |
| **LLM10** | Unbounded Consumption | **covered** | Sandbox resource caps (fork/memory/timeout — `tests/test_sandbox.py`, `tests/test_win_sandbox.py`); token spend brake + context ceiling at the gateway chokepoint (`karvyloop/gateway/client.py:24,114-117`, `tests/test_spend_budget.py`, `tests/test_gateway_ceiling.py`); earned-silence blast-radius cap (`tests/test_silence.py`). |

**LLM tally: 5 covered · 3 partial · 1 gap · 1 n/a.**

**Combined: 11 covered · 7 partial · 1 gap · 1 n/a (out of 20).**

---

## 4. Threat model (one page, STRIDE-lite)

**Trust boundary in one sentence:** *instructions* may only originate from the
user and the system framework; **everything else that enters the system is data**,
and every action derived from data must pass a deterministic floor the model
cannot override.

**In scope:** the `karvyloop` runtime, its console, its tools, the relay, and
everything a model or third-party content can reach through them.
**Out of scope (stated, not hidden):** a compromised host OS or OS user account
(the runtime runs as you — host compromise is game over, as for any local
software); social engineering of the user outside the product surface; the LLM
provider seeing the context you choose to send it (local-first minimizes what
that is, and what to send remains under your control).

| Untrusted input source | What an attacker can try | Countermeasures (evidence) | Residual risk |
|---|---|---|---|
| **Fetched web pages / search results** | Prompt injection ("ignore previous instructions"), fake system tags, SSRF pivot to internal/cloud-metadata endpoints | Fence + fake-tag scrub before context (`karvyloop/coding/tools/web.py:233`); SSRF floor incl. per-hop redirect re-validation (`karvyloop/coding/tools/urlguard.py:76`) | Model persuasion despite fencing (G4) |
| **MCP servers** (descriptions, results, metadata) | Instruction smuggling in tool descriptions; privilege claims via self-declared metadata; credential exfiltration | Descriptions are data: control chars stripped, capped; metadata never enters authorization; results fenced (`karvyloop/mcp_client.py:265`, `karvyloop/coding/tools/mcp_tool.py:35`); tokens scrubbed everywhere (`tests/test_mcp_remote.py`) | A malicious server can still lie *within* its granted scope |
| **Imported agents / third-party skills** | Malicious code; instruction smuggling in skill bodies; post-import tampering | Untrusted by default; sandboxed, no-net without explicit grant (`karvyloop/capability/skill_grants.py:24`); sha256 directory integrity lock, fail-loud (`karvyloop/registry/skill_lock.py:105`); degraded-mode Windows refuses to run them uncaged (`karvyloop/platform/win/degraded.py`) | No origin signature (G2) |
| **Agent-to-agent messages** (internal workflows) | Injection contagion — one compromised upstream steering downstream agents | Upstream outputs pass the neutral data fence (`karvyloop/console/workflow_engine.py:279`; `tests/external_runtime/test_a2a_contagion_negative.py`) | Same persuasion residual (G4) |
| **Other paired devices / remote clients** | Spoofing, frame tampering, replay, scope escalation | E2E X25519 + ChaCha20-Poly1305, strict seq anti-replay, AAD-bound headers (`karvyloop/relay/e2e.py:12-18`); one-time pairing codes; read-scope requests hit a default-deny allow-list (`tests/test_external_audience_gate.py`) | A genuinely compromised paired device legitimately holds its granted scope |
| **The relay server** (ours or self-hosted) | Reading or modifying forwarded traffic | Blind forwarding — the relay only ever sees ciphertext (`karvyloop/relay/server.py:207-209`); tampering fails the AEAD tag | Traffic metadata (timing, sizes) visible to the relay |
| **The LLM itself** (hijacked or hallucinating) | Dangerous tool calls, runaway loops, resource burn | Every action passes authorize (`karvyloop/capability/decision.py:131`); deontic gate + sensitive-path floor are immune to FULL mode; sandbox contains exec; spend brake + context ceiling (`karvyloop/gateway/client.py:114-117`); irreversible actions require a human | Approved-scope mistakes — mitigated by H2A evidence display, not eliminated |

STRIDE cross-check: **S**poofing → pairing + minted access tokens; **T**ampering →
AEAD + skill integrity lock; **R**epudiation → append-only Trace
(`karvyloop/cognition/trace.py`); **I**nformation disclosure → sensitive-path
floor + scrubbing + E2E + external-audience gate; **D**oS → sandbox caps + spend
budgets; **E**levation of privilege → capability tokens + deontic gate + sandbox.

---

## 5. Known gaps & roadmap

What stands between "the floors are tested" and "we would say this meets the
standard end-to-end". In rough priority order:

| ID | Gap | Maps to |
|----|-----|---------|
| **G1** | No dedicated system-prompt-leakage test | LLM07 |
| **G2** | No origin signature / signed provenance for imported agents & skills (integrity lock detects tamper, not malicious origin) | ASI04, ASI10, LLM03 |
| **G3** | No adversarial poisoning corpus for memory/knowledge ingestion; poisoning tests not `security`-marked | ASI06, LLM04 |
| **G4** | No real-model red-team campaign (fence persuasion resistance; public prompt-injection benchmark suites) | ASI01, LLM01 |
| **G5** | No cascade / chaos scenario suite for multi-agent failure propagation | ASI08 |
| **G6** | No systematic dark-pattern review of decision-card presentation | ASI09 |
| **G7** | Misinformation / honest-failure tests exist but are not `security`-marked | LLM09 |
| **G8** | No external security audit or third-party red team yet (see §6) | all |

## 6. Team & process (the single-maintainer question, answered honestly)

KarvyLoop is built by one maintainer pairing with AI. We do not pretend that is a
security team. What compensates, concretely:

- **Determinism over vigilance.** The floors that matter (sandbox, capability,
  sensitive paths, deontic forbids, irreversible-action exclusion) are
  deterministic code in the execution path — they do not depend on anyone
  reviewing model output in real time.
- **Adversarial tests as the regression net.** `pytest -m security` (342 cases)
  runs in CI on every change, on Linux and Windows. A floor that regresses turns
  the build red.
- **Independent adversarial verification discipline.** Non-trivial changes are
  acceptance-tested by an independent checker running the real path with a
  write-stripped read-only token (`karvyloop/sandbox/mounts.py:27`) — the author's
  own green checkmark is not trusted.
- **Security lands in design, not after.** The fence, the deontic gate, and the
  earned-silence exclusions were designed against the OWASP catalogs cited here
  (grep the codebase for `OWASP` — the mappings are written at the mechanism,
  not retrofitted in docs).
- **Vulnerability reports are welcome** — see [`SECURITY.md`](../SECURITY.md).
  An external audit is an explicit roadmap item (G8), not something we claim to
  have had.

*Last reviewed: 2026-07 · against `pytest -m security` = 342 cases, 18 modules ·
line numbers are starting points and may drift; the test suite is the ground truth.*
