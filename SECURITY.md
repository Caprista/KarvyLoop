# Security Policy

KarvyLoop is a local-first personal agent runtime: it executes model-driven
actions on *your* machine, against *your* data. We treat security as the floor
the product stands on, not a feature we advertise — and this policy tells you
exactly how to report a problem and what to expect from us.

For the full self-assessment against OWASP LLM Top 10 (2025) and OWASP Agentic
Top 10 (2026), including our threat model and an honest list of known gaps, see
[`docs/SECURITY-POSTURE.md`](docs/SECURITY-POSTURE.md). The adversarial test
suite behind it is runnable: `pytest -m security`
(catalog: [`tests/security/README.md`](tests/security/README.md)).

## Supported versions

KarvyLoop uses calendar versioning (`YYYY.M.D`) with a rolling release model and
a single maintainer. Security fixes land in a new release; we do not backport.

| Version | Supported |
|---------|-----------|
| Latest release ([Releases](https://github.com/Caprista/KarvyLoop/releases)) | ✅ |
| Anything older | ❌ — please upgrade (`karvyloop update`) |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately through **GitHub's private vulnerability reporting**:
[Report a vulnerability](https://github.com/Caprista/KarvyLoop/security/advisories/new).
This reaches the maintainer privately without exposing any contact address, and
keeps the details out of public view until a fix is ready. (No email address is
published on purpose — the GitHub channel is the single point of contact.)

Please include: affected version (`karvyloop --version` / `__version__`), a
reproduction (proof-of-concept input, config, platform), and the impact as you
understand it. Reports in English or Chinese are both fine.

### What to expect

KarvyLoop is a **single-maintainer open-source project**, not a commercial
product with an on-call team — so this section is honest about that rather than
promising a service-level agreement it can't guarantee. Expect a look as soon as
the maintainer reasonably can, a genuine attempt to reproduce and fix confirmed
issues, and a heads-up when a fix ships. Please practice **coordinated
disclosure**: give a reasonable window before publishing details publicly. You'll
be credited in the advisory and release notes unless you prefer to stay anonymous.

### Bug bounty

**We do not run a bug bounty program at this stage** — KarvyLoop is a
one-maintainer open-source project and we would rather be honest about that than
promise rewards we can't sustain. Reports are still genuinely wanted; credit and
a fast, serious response are what we can offer.

### Scope notes

In scope: the `karvyloop` package (runtime, console, tools, sandbox, capability
system), the relay server, the pairing/E2E protocol, and the install scripts.
Out of scope: vulnerabilities requiring an already-compromised host OS or OS
user account; issues in third-party model providers; volumetric DoS against
your own local instance.

## Security design in one paragraph

Security here is architectural, enforced in the execution path: **local-first**
(your data, keys, and memory stay on your machine; the optional relay is
end-to-end encrypted and blind), **the human approves** (consequential actions
go through decision cards; irreversible actions — send / delete / pay — are
never auto-approved, ever), **provenance-based injection defense** (instructions
come only from you and the framework; web pages, MCP output, imported agents,
and agent-to-agent messages are fenced as data), and **sandboxed execution**
(real OS sandboxes on Linux / macOS / Windows, default-deny mounts, no network
by default, deterministic sensitive-path denial). These floors are held by an
adversarial test suite (`pytest -m security`, 342 cases as of 2026.7) that runs
in CI. We do **not** claim external certification or audit — see the posture
document for the honest gap list.
