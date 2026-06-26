# Changelog

All notable changes to KarvyLoop are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/). Versioning is
**date-based (CalVer)** — `YYYY.M.D`, the date a release is cut (e.g. `2026.6.26`).
The single source of the version number is `karvyloop/__init__.py:__version__`.
Releasing is described in [RELEASING.md](RELEASING.md).

## [Unreleased]

_Work in progress toward 1.0 — see [ROADMAP.md](ROADMAP.md)._

### Added
- **macOS sandbox (Seatbelt).** Native `sandbox-exec` adapter via the PAL, mirroring the Linux
  bubblewrap fail-closed contract (deny-default; writes confined to the token's workspace;
  no network unless granted). Adversarially verified on real hardware (Apple Silicon, macOS 26):
  writes outside the workspace / to `$HOME` and ungranted network are blocked; granted network
  reaches. macOS is now a supported platform with working agent execution, not just chat.

## 2026.6.26

First dated public release — a snapshot of the current runtime.

### Runtime
- Console (FastAPI REST + `/ws` WebSocket + static SPA), MainLoop fast/slow brain,
  Forge executor, multi-provider gateway, model management UI.
- Entity model L0–L4 (tool/skill · atom · role · domain), mirrored in `schemas/`.
- Safety: capability tokens + bubblewrap sandbox below the agent's trust boundary.

### The wedge
- **Skill crystallization — stores the *method*, not the answer.** A recall hit
  re-executes the learned method on the current inputs (CBR-style) instead of replaying
  a stale answer; only semantically-stable results are cached.
- **Decision-interface crystallization** — the decision card translates a proposal into
  your terms, keeps a *verified* region separate from Karvy's *narration*, pre-aligns the
  standards you've crystallized, and forces judgment before a high-stakes commit.

### Collaboration & orchestration
- Roles, business domains, group chat. Global Karvy recognizes multi-role orchestration
  ("open a roundtable with these roles on X") and proposes it as an H2A confirm card.
- **Durable workflow execution** — a long multi-agent workflow survives a console restart
  (steps checkpointed; completed steps replay instantly).
- **Scheduled tasks** — recurring tasks described in plain language (NL→cron), Karvy-only
  (one audit surface), with a ⏰ dashboard.

### Input & models
- **Multimodal chat input** — attach images or text/Markdown files; threaded as content
  blocks through the gateway (Anthropic native / OpenAI `image_url`).
- **Providers** — Anthropic, OpenAI-compatible, DeepSeek, Kimi/Moonshot (Global +
  For-Coding), with config-driven `extra_headers` so header-gated endpoints onboard
  without code.

### Updates & quality
- Single version source; `karvyloop update` + a dismissible console banner that detects a
  new release and tells you the upgrade command — **never auto-upgrades** (zero telemetry;
  off via `KARVYLOOP_NO_UPDATE_CHECK`).
- Frontend↔backend wiring contract test (orphan endpoints / dead calls fail CI).
- Bilingual (en/zh) presentation layer; Apache-2.0; self-contained test suite.
