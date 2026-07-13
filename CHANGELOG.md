# Changelog

All notable changes to KarvyLoop are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/). Versioning is
**date-based (CalVer)** — `YYYY.M.D`, the date a release is cut (e.g. `2026.7.1`).
The single source of the version number is `karvyloop/__init__.py:__version__`.
Releasing is described in [RELEASING.md](RELEASING.md).

## [Unreleased]

_Work in progress toward the GA bar — see [ROADMAP.md](ROADMAP.md)._

## [2026.7.13] — 2026-07-13

_A big one: your KarvyLoop is no longer tied to one machine or one desk. Your
devices become one mesh, your phone reaches home from anywhere, your memory is
yours to lock and rewrite, and a decision card is now something you can question
before you decide._

### Added
- **Your devices are one KarvyLoop (same-owner device mesh).** Register your machines
  into a roster (`karvyloop devices`), each advertising a capability fingerprint, and
  what you learn on one flows to the others over one shared, causally-ordered log (HLC,
  not vector clocks): **crystallized skills** and **memory beliefs** sync between devices
  (outbox on the write choke + idempotent replay — never resurrects a belief you
  invalidated), and a **decentralized task board** (lease/claim + reclaim-on-death) means
  losing a device loses the *machine* a job runs on, never the job. A **My Devices** panel
  surfaces the roster with capability chips, an online light, and informed-consent removal
  (it warns before you delete a device that provides a capability no other device has).
- **Reach your home console from anywhere (away-from-home).** Your phone on cellular, a
  café network — a phone and a home machine both behind NATs meet at *your own* public
  relay (`karvyloop relay-serve`; blind-forwarding, sees only ciphertext). Pair a device
  once, from the `/m` phone page, and it gets its own key — end-to-end encrypted (X25519 +
  ChaCha20-Poly1305), the browser tunneling the app home over the relay. Pairing is a
  one-time code, never a raw token in a URL or QR; management (issue/list/**revoke**) is
  local-only — a stolen phone can use the surface but can never mint access or revoke
  another device. The relay is **bring-your-own-server**: its address is config
  (`relay:` in config.yaml), never hardcoded, with a one-command setup script and a
  self-host guide.
- **A phone page that does one thing well (`/m`).** Open it on your phone: the decisions
  waiting on you, three thumb buttons, and a chat strip to start work — a fluid layout
  that reflows from phone to foldable to tablet, no coined jargon on the first screen.
- **Ask about a decision before you decide.** A decision card now has an opt-in "💬 Ask
  about this" — question Karvy about *this* card ("why now?", "what if I decline?") and it
  answers grounded only in that card's own evidence, staying neutral (it won't argue you
  toward accept). The two-button fast path is untouched; asking never touches the decision.
- **Your memory is yours to lock and rewrite.** In the knowledge library you can now **pin**
  a belief (never auto-archived) and **edit** it as a ledger-style supersede — the new
  version becomes active, the old one moves to the history layer with an auditable "edited
  by you" trail. Nothing is silently rewritten.

### Changed
- **The README leads with the story, not the category.** First screen is now the 40th-run
  value curve and three contrarian bets, not "another agent runtime."
- **Honest egress + honest away-from-home labels.** Domain-level network egress is positioned
  as an opt-in enhancement — the default binary fail-closed floor is the honest, universal
  guarantee; nothing bypassable is ever labeled "enforced."

### Fixed
- **LAN boot no longer hangs on a busy instance.** Decision cards load lazily — an instance
  with dozens of backed-up cards opens fast instead of firing every card's LLM at once.
- **The keyword gate no longer locks out orchestration.** Karvy can decompose and route a
  no-keyword task instead of falling through to a literal reply.
- **Preference crystallization has a deterministic floor.** "You keep deciding X the same
  way" is counted from the evidence, not gated solely on an LLM flag that can wobble.

### Added
- **Bring your own AI runtime into the driver's seat (M1).** You can now attach an external
  headless-CLI AI runtime as a channel citizen, @-dispatch a task to it, and get its real reply
  back — while it stays an opaque external executor whose output is always **untrusted data** that
  never auto-enters your memory. Federate capability, not trust, not memory: the external runtime's
  token spend is metered under its own independent `ext:<name>` ledger source (never mixed into your
  main gateway buckets), its stdout/stderr are treated as possibly-carrying-secrets and scrubbed
  before touching any log/Trace, and credentials are never placed in the subprocess environment (a
  hard first line of defense, not a redaction afterthought). The global assistant orchestrates this
  (attach / dispatch / list) through capability-gated tools; failures are fail-loud (nonzero exit,
  timeout, empty-success, or an approval request all surface instead of hanging). Adding another
  runtime is a recipe file, not new code.
- **Manage your external runtimes from the console — and connect one on demand.** A new
  **External Runtimes** panel (🔌 in the left nav) lists every connected external citizen with a
  distinct, off-colored **external** badge (so an opaque, untrusted external executor is *never*
  mistaken for a native role), a live online/offline/unreachable status light you can refresh per
  citizen, a remove (detach) button, and a **direct-chat** button — external citizens get their own
  l0 conversation line, addressed as `(domain, external, citizen_id)` so they never blend into the
  native role space. New management endpoints (`GET /api/external/citizens`,
  `GET /api/external/liveness`, `POST /api/external/detach`) live in their own `routes_external.py`
  and go through the same local-first origin gate as one-click install (writes only from
  loopback/LAN). **On-demand onboarding:** Diagnose and the panel deterministically detect whether a
  compatible headless CLI runtime is installed and, if not, walk you through connecting one **from
  its own official source** — the same degradation-guidance pattern as the `[asr]`/`[ocr]` unlocks
  and the MCP registry links. The hard boundary is spelled out in the UI and the README: an external
  runtime is **third-party software you bring yourself — KarvyLoop does not bundle, host, download,
  or `git clone` it.** We ship the bridge, adapters, and management UI; you bring the runtime.

### Fixed
- **The one-click update now tells you what happened instead of leaving you guessing.** A failed
  upgrade (e.g. `git pull` couldn't reach GitHub) used to silently restart the old version and
  re-show the same ambiguous "a newer version is available" banner — the user was stranded on "did
  it update or not?" (the exact *怎么样了?* status-anxiety anti-pattern this product exists to
  kill). Three fixes: (1) the upgrade **outcome survives a normal reopen** — the result window went
  from 10 minutes to 24 hours, so closing the tab and coming back still shows *why* it didn't take;
  (2) **any** failed upgrade now surfaces a fail-loud red banner with the reason and a **Retry**
  button — previously only an auto-rollback did, a plain `git pull` failure showed nothing; the
  banner is suppressed once you're actually on the target version (no stale false alarms); and
  (3) the **current running version is now always visible** in the top bar next to the logo, so
  "what am I on?" is answerable at a glance without reverse-engineering the banner.

### Added
- **🧾 Receipt Reader (票据员) — the fourth resident: receipt/invoice recognition → one checked
  structured line.** Hand it a receipt or invoice — pasted text or a photo — and it returns a clean
  structured record (merchant, date, currency, total, tax id, itemised amounts) with the items
  **actually summed and checked against the stated total**, the usual OCR mess (O↔0, l↔1, misplaced
  decimals) calibrated from context, and any unsure field left `null` rather than guessed.
  Recognition and structuring **only**: it suggests a category as a *hint* from your human-owned
  company category sheet (the growth soul, like the scribe's glossary), never rules on
  reimbursability and never files anything. Image input rides the **same `file_extract` path as
  audio**: photos are OCR'd on-device via the optional `[ocr]` extra (PaddleOCR), and without it
  (and no vision model) it asks for the pasted text instead of faking it — no bespoke pipeline, no
  special UI, it's just a resident you drop a receipt on. Real-model verified including the hard
  case: given a receipt whose items don't add up, it flagged `sum_mismatch` with both numbers and
  refused to fudge the figures to balance.
- **📚 study-buddy and 🎙️ meeting-notes promoted from skills to full residents.** Both now ship as
  complete 7-file paradigm images (identity / soul / user / commitment / verify / memory +
  composition), read-only, each carrying a **growth soul** — study-buddy's concept map & weak
  spots, the scribe's team glossary — the reason each is a role you can watch learn, not a static
  skill. The empty house now offers four tenants (file-butler, study-buddy, meeting-notes,
  expense).
- **Internal agents use our own paradigm (知行合一).** A new `AgentSpec` declares an internal
  agent's *engineering core* — identity, principles, contract, verify, tools — with the persona
  layer deliberately omitted for stateless internals; `converge` is the first adopter. This
  dogfoods the paradigm on our own code, for developers reading the source; it is **not** surfaced
  in the user UI (that would clutter and create a see-but-can't-use asymmetry).
- **Knowledge chat「追问」(converge flow A).** On the sediment card you can now **settle the rest
  while keeping the questioned point open** for discussion, instead of an all-or-nothing accept.
- **Onboarding intake: 4 questions whose answers immediately change behavior.** At the start of
  the first-10-minutes journey (after the key is configured, before the first demo chip), the
  journey bar asks 4 quick questions — conclusion-first vs process-first output, ask-me vs
  draft-first when unsure, direct vs measured tone, file-by-type vs file-by-time. Each answer is
  seeded into the **existing decision-preference mechanism** (an explicit, confirmed
  `decision_pref` Belief with `origin=user_explicit` + `intake_q/intake_opt` provenance, persisted
  to `beliefs.json`) — no new storage, and the seeds flow into pre-alignment and the violation
  gate the moment they land. Skipping plants nothing and costs nothing; replaying the journey
  re-opens the intake, and re-answering **replaces** the old seed for that question. Old
  instances never see it (it lives inside the journey's existing fresh-stage gate). Copy
  discipline: the receipt says your standards are *written down and kept at hand, pre-aligned* —
  it never claims "I understand you" (locked by test).
- **File Butler's first lesson: referral → read-only scan → preview card → your call → real
  execution.** Accepting the resident-referral card now hands you a first-task chip: let the
  butler scan your Desktop & Downloads (read-only, whitelist-enforced via the fs-grants ledger,
  sensitive-path floor immune) and draft a tidy-up plan. The scan/plan/dedup chain is
  **deterministic, zero LLM**: type or time buckets (the grouping mode is decided by your intake
  answer — the first place an intake seed visibly changes behavior), duplicates verified by
  content hash (reported only, never deleted), space hogs reported only. The plan arrives as an
  H2A preview card with the spotlight treatment — nothing moves until you approve, and "just
  looking" (reject) is an explicitly legitimate choice. On accept it executes exactly the plan:
  moves only, never a delete, never an overwrite (conflicts are skipped and reported), every move
  journaled in `butler_moves.json` so the whole job is reversible. Empty folders get an honest
  "nothing to tidy" instead of an invented plan. Verified end-to-end in a real browser
  (Playwright: referral ACCEPT → chip → plan card → ACCEPT → files actually moved,
  out-of-whitelist canary untouched) and on the real-key rig (R3).
- **A demo instance that's already lived a week — a readable daily timeline + a declining
  participation curve.** A read-only bundled instance ("Lin", a freelance writer) whose skills,
  growth curve, decision preferences and role experience are the **real output of a real
  seven-workday run** (compressed in wall-clock, not faked) — so a first-time visitor can watch
  what "more like you with every use" looks like at day 7 instead of starting from zero. The panel
  now leads with **the person** (Lin's persona is the headline; the honest "fictional demo" notice
  is a footnote, not the star) and its centerpiece is a **declining effort curve**: hands-on turns
  fall from 5 to 2 a day, corrections from 2 to 0, and the decision mode shifts from cold
  deliberation to a pre-aligned glance — the from-heads-down-doing-to-a-glance arc rendered as a
  mini bar chart plus a six-cell before→after strip. Below it, **seven day-by-day cards** show what
  Lin did / talked about / produced / deposited each day (draft snippets open inline; H2A cards
  show the ACCEPT/REJECT and her reason). The earned-silence gate is shown honestly as *9/35, 26 to
  go — never auto-pilot* (H2A never breaks). The old unreadable belief-fragment dump is gone;
  beliefs/knowledge/skills are collapsed detail sections you can expand on demand, not the first
  screen. The panel also enlarges/fullscreens (⤢ three-state, reusing the desktop body-class
  seam). Browsable via a 👀 entry (GET-only API, 405 on any write; the new timeline/effort-curve
  fields are read-only, zero new accounting); guaranteed zero-pollution of your own instance (a
  tree-hash test asserts the package files never mutate on browse); every growth number is derived
  read-only from the bundled Trace by the same production `build_curves`, not hand-typed; a
  disclosure footnote marks it as a demo throughout. Verified in a real browser (Playwright:
  persona headline dwarfs the disclosure, 7 daily cards, the curve's first bar taller than its
  last, beliefs stay folded until opened, ⤢ three-state, zero console errors). Ships zh first
  (Lin); the English translation is a planned next step.
  LLM tagging fragments the vocabulary over time — one memory gets tagged "夜间模式", a synonymous
  one "深色主题", and tag-overlap matching goes blind between them. Tag assignment is now
  **reuse-first**: the existing tag vocabulary is pre-filtered (content token overlap + frequency
  top-up, zero LLM) into a top-K candidate list that rides along in the tagging prompt with the
  instruction *reuse an existing tag when one fits; creating a new one requires a reason*. New tags
  are determined deterministically (never by the model's say-so) and each one is an explicit
  `tag_created` Trace event — the vocabulary can grow, but never silently sprawl. The comparison
  happens at the discrete symbol level: no vectors, inspectable, hand-editable.
  - **Daily synonym convergence.** A slow-lane tick (`tag_merge_tick`; vocabulary-fingerprint
    watermark + judged-pair cooldown; zero LLM when nothing changed) finds synonym candidates by
    tag-name overlap and **second-order co-occurrence** (true synonyms almost never co-occur on one
    note — the model picks one phrasing per note — but they orbit the same neighbor tags), has one
    cheap LLM call judge them, and merges automatically into an **alias table**. Tags are derived
    data, not user data, so auto-merge is safe: no belief's stored tags are rewritten, the old tag
    survives as an alias that still matches, and the audit trail is the alias file (via/ts) plus a
    `tag_merged` Trace event. Recall seeds, graph-edge keys and supersede candidates all read the
    alias-expanded view, so converged phrasings become mutually visible — fragment scenario
    measured: paraphrase recall@8 **0.67 → 1.00** at N=1k/5k with latency unchanged; the typing hot
    path stays zero-LLM, zero-IO.
  - **Ingest-time reconciliation shipped (was Planned).** The single supersede LLM call at write
    time now also judges `duplicate` and `extends`, adding no calls: a high-confidence duplicate
    (LLM verdict **plus** deterministic lexical/tag corroboration — the model's word alone never
    touches the store) is auto-merged by invalidating the losing copy (invalidate-don't-delete,
    reason on record, provenance authority still wins); an `extends` (same topic, new information)
    raises the existing merge-knowledge decision card with the model's proposed merged text —
    adding information stays your call.
- **`[asr]` extra — meeting recordings become minutes, locally.** Audio files (mp3/wav/m4a) now
  ride the same attachment pipeline as PDFs: upload → transcribed on-device → the text flows into
  the existing channels (files-panel preview, `read_file` for roles, the meeting-notes skill).
  Selection was a real bake-off (verdict in the internal docs): **faster-whisper** (MIT,
  CTranslate2) wins on pip-only install (bundled PyAV — no system ffmpeg), CPU int8 viability,
  one model for zh+en, and active maintenance; SenseVoice (stronger Chinese, custom model license,
  manual model download) is recorded as the challenger. Honesty is wired in: the speech model is
  **not** in the wheel (first use downloads it — default `small` ≈480 MB, `KARVYLOOP_ASR_MODEL` to
  choose); fake-extension/corrupt audio is refused empty with a clear error (never garbage in the
  context); a failed model download reports "model load failed", not "your file is broken"; and
  without the extra, audio returns a clear install hint — the meeting-notes skill's contract now
  says "audio via optional `[asr]`" instead of over-promising ASR. Unlock panel got the 🎙️ row
  (status + the honest model-download note), i18n en+zh.
- **Residents fed more method, not more answers.** The File Butler's method library grew the
  parts a real butler is judged on: the full duplicate decision order (size → hash →
  "same name, different hash = versions, not duplicates"), a hot/cold archiving default
  (~180 days untouched → `Archives/<year>/`, finance files exempt by rule not by timer),
  per-file-type conventions (screenshots by month, installers as 30-day deletion candidates,
  camera filenames kept), and an explicit "never silent" operations list — all in the skill +
  seeded into the resident's memory as *candidates to confirm, not facts about the owner*.
  meeting-notes gained per-meeting-type templates (weekly sync leads with last week's items,
  reviews lead with the verdict, 1-on-1s record outcomes not personal discussion, brainstorms
  keep ideas ungraded), a SMART bar for action items, a decided-by/basis/options decision-record
  block, and glossary entries shown as *clearly-marked fictional examples* — shapes to imitate,
  never content to copy in.
- **📚 study-buddy system skill** (pure asset, third alongside data-analyst/meeting-notes; the
  resident role comes later): retrieval-first studying grounded in the two techniques rated
  high-utility by the evidence (practice testing + distributed practice, Dunlosky et al. 2013) —
  quiz from the learner's own material, an SM-2-lineage review ladder (1→3→7→14→30 days),
  Feynman teach-back, Cornell cues, Bloom's ladder for question depth. Answers are graded
  against the material ("your notes don't settle this — check the source" is a valid grade),
  and the growth story is a **human-owned study ledger** template (intervals stretching + old
  mistakes not resurfacing = the progress report; no invented mastery percentages).
- **Residents: the empty house gets its first tenant.** A fresh install used to greet you with an
  empty role library. Now, on the first visit with zero roles, Karvy raises a **referral decision
  card** introducing the first resident — the **📁 File Butler** — and nothing happens until you
  say so: ACCEPT actually creates the role (its identity, temperament, verification gates and the
  seeded collaboration contract are all plain files you can open and edit — a working example of
  how to constrain an agent); REJECT means it never asks again; DEFER just keeps the card. No
  preset subsystem was built: residents are read-only in-package mirrors
  (`karvyloop/system_residents/`), and moving in is a normal `RoleRegistry` instantiation — your
  instance is never overwritten by upgrades.
  - **📁 File Butler** ships with a *method*, not canned answers: a `file-butler` system skill
    (PARA / GTD inbox-zero / Johnny.Decimal / ISO naming / size-then-hash dedupe, sources named)
    plus a human-owned `filing-preferences` template that grows into *your* filing rules. Safety
    lives in the deterministic layer, not in prose: folder access is a hard whitelist
    (Desktop / Downloads / Documents) recorded in the fs-grants ledger (visible, revocable),
    deletion always requires explicit confirmation with a backup first, and sensitive paths are
    immune to any grant. Deliberately absent: vector indexes, OCR, self-owned cron.
  - **🎙️ meeting-notes system skill** (pure asset, alongside data-analyst): transcript → minutes
    with the three-bucket method (decisions / action items as who-what-by-when / open questions),
    and a glossary gate — unknown terms are checked against a **human-owned team glossary**
    template and flagged "needs confirmation", never expanded by guessing. Honest input contract:
    it consumes text; audio only via the optional `[asr]` extra (see above). Growth stays real: the
    glossary file getting longer is the metric — no invented percentages.
- **"Unlock more capabilities" panel — degraded features now guide instead of hiding.** Graceful
  degradation had a blind spot: if you never configure MCP, push channels or attachment parsing,
  nothing ever tells you they exist ("if you don't guide users, they genuinely don't know the
  config is there"). A new deterministic endpoint (`GET /api/capability/unlocks`, zero LLM, never
  echoes a secret) reports each optional capability's live status — ready / not set up / needs
  install — and the console renders it as a checklist: one line of value, the exact command or
  `config.yaml` snippet (copyable), a one-click jump to the MCP presets / paste-URL screen, and
  where to *find* MCP servers (the official registry at registry.modelcontextprotocol.io plus
  community directories PulseMCP and Glama — links verified live). Voice input is probed
  browser-side, so the silent "no 🎤 in this browser" case now explains itself. Entrances where
  you'd actually look: a Skill Library card, the Diagnose panel (the health card says what's
  broken; this says what's dormant), the end of the first-10-minutes journey, and right where a
  PDF preview fails for the missing `[files]` extra. i18n en+zh; pattern: setup-checklist /
  connectors-directory, one clear action per row.
- **Webhook inbound approval (v2).** The webhook channel can now carry your decision back:
  set `channels.webhook.reply_url` to a polled reply source (e.g. a private ntfy topic's
  `/json?poll=1` endpoint — the console *polls* it outbound, still no listening port) and reply
  `ACCEPT <code>` / `REJECT <code>` / `DEFER <code>` from your phone. Codes reuse the email
  channel's HMAC single-use, time-limited mechanism (same secret, same mint/verify) and land on
  the same decide path as the console and email; strict-parse only — anything else in the reply
  source is data, never instructions; high-risk cards remain notify-only (no code is ever minted
  for them, and the poller rejects them again as a second fence); a persisted watermark plus a
  processed-id ring prevents re-consumption across restarts. Unset `reply_url` = v1
  outbound-only behavior, unchanged.
- **Observability, grown on the Trace (external suggestion, converged).** No parallel event
  stream, no second ledger — three additions that live on what's already there:
  - **`run_id` threads one run together.** Each drive (and each daily/background entry) opens a
    per-run scope (contextvar, same pattern as the deontic gate); every Trace entry and every
    token-ledger row written on that chain is stamped with the same short id. Old records without
    the field read back unchanged; existing DBs migrate in place.
  - **`karvyloop replay --run <run_id>`.** Filter the replay to a single run across tasks, with a
    stderr summary line (entries / duration / tokens — computed read-only from existing data).
  - **Real causes surface, not misdiagnoses.** "Infra dead" (model/network unreachable) is now a
    *whitelist* — network/timeout/auth/rate-limit/5xx only. Code defects (`TypeError`,
    `AttributeError`, bad-request 4xx…) fail loud with the original exception chain, and the true
    cause (exception class + traceback) is recorded into the Trace; budget/context-ceiling gates
    report as budget stops instead of "network down". This fixes the week's real pain: a swallowed
    `TypeError` was reported as "model unreachable" and sent debugging in the wrong direction.

### Changed
- **Desktop view redesigned for a calm, single-focus layout.** The desktop used to open with every
  sticky note auto-spread across the right half — busy, no hierarchy, visually crowded. It now
  opens **empty and single-focus**: a large centered **clock** anchors the top with a **lightweight
  "waiting on you" list** beneath it (minimal one-line entries for pending decisions and tasks —
  visible but not shouting, never full cards); a **compact centered chat** sits between the clock
  and the dock — a single-window chat with no conversation sidebar, like a small chat app. A `⤢`
  button in the chat titlebar cycles three states: **compact → expanded** (full chat with the
  conversation list and history) **→ full** (fills the whole console viewport) → back. The four
  sticky notes (decisions / intel / ideas / who's-busy) default to **collapsed and parked** in a
  tidy strip (title only, click to open); the **board** is folded into a single dock icon (📋) with
  a **badge** that lights up when there's a new decision or new data — click it to fan out all four
  full tabs, click again to tuck them away. The dock, the day/night wallpaper (auto/day/night/off),
  and the "no fake theater" soul layer (zero engine timers, real-event-driven only) are all
  preserved; the app's default view is still the conversation (the desktop is a place you switch
  into). Saved layouts you'd rearranged are still honored; an incompatible old layout falls back to
  the new default without breaking. Verified in a real browser (Playwright) with screenshots of the
  empty / expanded / full states, and the pending list, board badge, and three chat states are
  locked by smoke + browser tests.

### Fixed
- **Knowledge chat: real back-and-forth, no self-contradiction, links actually fetch.** Chat now
  renders as separated **turns** (question vs answer) instead of one wall of text; the same module
  no longer offers to sediment X while another part says "nothing to sediment" — *one referent, one
  source of truth*, enforced structurally rather than by prompt; and librarian fetches of a pasted
  link now send **real browser headers** and allow the fake-ip proxy range (`198.18.0.0/15`) so
  paste-a-link works behind Clash/Surge/V2Ray fake-ip mode (an earlier SSRF hardening had blocked
  it). Starting a new knowledge session no longer loses the previous one.
- **Management modals now scroll when content runs past one screen.** Atom and role edit modals
  clipped anything below the fold with no scrollbar — `.modal-body` was missing `flex: 1` +
  `min-height: 0`, so the flex child wouldn't shrink and the overflow never engaged.
- **Capability-grant card now covers the delegated execution path**, the decision-card popup no
  longer overlaps the role-edit modal, and the paradigm form is discoverable.
- **Image receipts wired into `file_extract` without crashing when `[ocr]` is absent** — a missing
  OCR extra degrades honestly (`missing_dependency`), and a fake/corrupt image is refused
  (`bad_file`) rather than spilling bytes into context.
- **Desktop view: five real layout bugs from the calm single-focus screenshots.** All caught
  visually (the previous Playwright tests asserted existence/z-index but never *saw* occlusion) and
  now locked with rect-level visual assertions + screenshots:
  (1) **The big clock is no longer squeezed.** The weekly-memento tile used to sit dead-centre on
  top of the clock and the compact chat crept up under it — the clock is the desktop anchor and got
  clipped top and bottom. The memento moved to the top-left corner and the chat's default top is
  measured to land clearly *below* the clock, so the anchor keeps its own clear vertical space.
  (2) **The compact chat shows the conversation, not a squished card over a blank void.** The chat
  body is a `200px + 1fr` grid; hiding the conversation list with `display:none` left the 200px
  column reserved, so `chat-log` was crushed to ~200px with a huge blank area beside it. Compact
  mode now collapses the body to a single column so the message flow fills the window, and an empty
  `chat-log` shows a proper "Say something to Karvy" placeholder instead of blank.
  (3) **Pop-up windows no longer hide under the dock.** The Lin demo panel (and any window) could
  extend its bottom beneath the floating dock. Window `max-height` now reserves the dock band, and
  clamp/positioning keep every window's bottom above the dock.
  (4) **Windows drag by their title bar again.** The injected minimise/expand/close buttons were
  spread across the whole management title bar by `space-between`, so grabbing the bar usually
  landed on a button and refused to drag. The buttons now group tight on the right, leaving a large
  clean drag handle; dragged positions still persist to `karvyloop_desk.v1`. (Mobile keeps no drag.)
  (5) **Collapsed side notes stack cleanly.** The docked-note lane stepped by a stale 40px constant
  while the real collapsed cards are ~65px tall, so each card overlapped the next and text bled
  together. The lane step is now measured from the real collapsed height, giving clean vertical
  spacing. Locked by browser tests (clock ∩ memento/chat empty, demo bottom ≤ dock top, title-bar
  drag moves left/top, collapsed cards pairwise non-overlapping) and archived day/night screenshots.
- **The global Karvy (bottom-right capybara) can no longer be covered by a panel.** On the desktop
  it used to sit *below* windows and panels (z-index 210), so an open management panel or the board
  could hide it. It now stays pinned on top (z-index 9550 — above the dock and every window/note),
  so the one place you always talk to Karvy is always reachable. It still sits below its own
  "carrying a card" walk-on and speech bubble (those are its theater). Regression-locked by a
  browser test that opens a panel over the mascot's corner and asserts the mascot is still the
  top-most element at that point.
- **Onboarding guidance is now impossible to miss.** First-10-minutes feedback: the guidance
  bubbles were easy to overlook. Guidance now uses the standard spotlight treatment — a black
  semi-transparent mask (0.7) covers everything else, the target is cut out with a pulsing accent
  ring, and the guide popover got a high-contrast restyle (larger title, accent CTA button). The
  journey's action moments (first demo-task chip, run-it-again chip, and the method-reuse receipt
  on a real recall hit) get the same spotlight — one mask per moment, never re-popped by polling.
  The mask only exists while guidance is active: Esc or clicking the mask dismisses it, the
  spotlit button stays clickable through the cutout, and `prefers-reduced-motion` disables the
  animations. Locked with a real-browser regression test across both views (chat + desk).

## [2026.7.4] — 2026-07-04

**The see-it release.** "More like you with every use" stopped being a promise you take on faith:
you can now *watch* it happen in your first ten minutes, *read* it as a growth curve, and *trust* it
behind statistical gates that refuse to bluff.

### Added
- **The first ten minutes.** A fresh install now opens with a guided journey: run one real demo
  task (a bundled sample CSV, executed by **your** configured model — nothing pre-recorded), run a
  second one, and watch the ♻ method-reuse receipt appear as the runtime recalls how it solved the
  first. The finale lights up only when your growth curve has real points. Skippable, replayable
  (🎬 in the top bar), and it never pops for existing instances upgrading in.
- **Growth you can see.** `GET /api/skills/curve` replays your Trace (read-only, same scoring code
  as production) into per-skill score sparklines and a library-wide growth chart at the top of the
  skills panel — usage, success rate, promotion progress over calendar days.
- **Webhook push channel.** Decision cards can now reach you wherever you actually are: a generic
  outbound webhook with presets for ntfy / Bark / Slack-compatible endpoints (plus a body template
  for anything else). Same dispatch point and card-selection semantics as the email channel;
  secrets never logged — including a redaction filter for the HTTP client's own request logs.
  Outbound-only in v1 (decide via the console link).
- **Attachments, really parsed.** PDF / docx / xlsx now flow down the same lane CSV always had —
  preview, truncation labels, one-click "have them analyze it" — via the optional `[files]` extra
  (`pypdf`, `python-docx`, `openpyxl`). Parse-empty-not-poison: corrupt or mislabeled files yield
  an empty result and a clear error, never binary garbage in your context.
- **Earned silence, fully gated.** All six statistical gates behind auto-handled decisions are now
  real code: irreversible actions never enter the pool; Wilson 95% lower-bound ≥ 0.90 with
  pre-registered evaluation windows; accept- and reject-direction accuracy each gated separately
  (with an honest correction: a 0.90 reject-side bar is mathematically unreachable at a ~93%
  approval base rate — the gate that can't be passed is decoration, so it's now a
  better-than-chance bound); 15% unannounced audits; renewals require reviewing evidence, not
  rubber-stamping; a rolling blast-radius cap. And the silenced path itself now runs the same
  violation checks as human-facing cards — fail-closed when the checker can't run.
- **Windows joins CI.** A `windows-latest` gate runs the full suite (the sandbox's AppContainer /
  RestrictedToken code is real Windows code — the cross-platform claim needed a non-Linux leg).
  The last shell-`grep` subprocess tests migrated to an OS-portable scanner.
- **Public docs, layer one.** `docs/QUICKSTART.md`, `docs/ARCHITECTURE.md`, `docs/CONCEPTS.md`
  (English + 中文) — the ten-minute journey in text, the two-loops architecture with real
  thresholds verified against the code, and a concepts dictionary.

### Changed
- **Console IA converges to one home + one mode.** The top-bar view switch is now two options —
  💬 Chat (the home: talk + decide, always one click back) ⇄ 🖥 Desk (watch your team at work).
  The former Board view stepped down from a top-level "home" to a rail gesture: a ⛶ button on the
  decision column temporarily expands the four quadrants into the familiar 2×2 full-screen
  (every panel kept, nothing removed); Esc or ✕ returns to chat. The zoom is a transient state —
  never remembered as a startup view — and a stored "board" startup preference migrates smoothly
  back to chat. The first-10-minutes journey always lands in the chat view and introduces the Desk
  as a reward moment at the finale instead of a cold-start three-way choice.
- **The desk mascot is the official capybara.** Hand-drawn pixel frames retired; the real artwork
  now renders at native resolution with CSS state animations (breathing, typing, card-carry,
  sleeping, a happy hop). Role accents became chest badges. The no-fake-idle red line stands:
  states change only on real events.

### Fixed
- **Slow-brain total outage on `main`.** A prompt-cache contract change had evolved the gateway's
  `to_blocks(cache=...)` signature without its duck-typed twin in the coding executor — every model
  call raised `TypeError`, was misdiagnosed as "model/network unreachable", and 2,900 green unit
  tests couldn't see it (their mock never calls the real body builder). Caught by the real-model
  end-to-end journey; fixed with a signature-parity contract test and a test that exercises the
  real request-body path, both verified to go red on drift.
- The journey's model-readiness poll no longer yanks you back to the chat view (or steals focus)
  every 15 seconds while you're exploring unconfigured; the forced view replay happens once, on
  first mount.
- Wheel builds from a dirty tree can no longer package stray files from the sample-data directory;
  the release test-bench writes to a temp workspace instead of the source tree.

### Security
- **`web_fetch` egress guard (SSRF).** Outbound fetches now resolve the target and refuse private,
  loopback, link-local, and cloud-metadata ranges, plus `file://` and credentialed URLs — with
  every redirect hop re-validated. Closes a credential-theft vector (a page could previously point
  the fetcher at `169.254.169.254` or your own loopback console).
- **Skill-library poisoning via corrections closed.** Reviewer feedback written back into a skill's
  `SKILL.md` is now sanitized — no header/frontmatter injection can hijack a skill's method.
- **Adversarial tests are a first-class suite**: `pytest -m security` runs 25 attack-vector classes
  (SSRF, poisoning, traversal, injection provenance, sandbox escape probes …) with an OWASP
  LLM Top-10 cross-reference in `tests/security/README.md`.
- **Deeper sandbox floors**: the Windows network gate is now OS-enforced (AppContainer/LowBox WFP
  deny — no admin required), and Linux gained a Landlock kernel layer under bubblewrap (older
  kernels degrade gracefully).

## [2026.7.3] — 2026-07-03

**The trust release.** One theme: prove it, don't claim it — the taste hit-rate score, measured cost before you spend, evidence drill-downs, governed out-of-workspace access with a hard floor for secrets, upgrades that verify themselves and roll back, and Windows joining as a supported (degraded) platform.

### Security
- **Same-origin gate** on the console: cross-origin browser requests are rejected on both HTTP and the
  WebSocket handshake (closes cross-site WebSocket hijacking and file-endpoint CSRF; non-browser clients
  and the console's own frontend are unaffected). Applies even on loopback.
- **Skill integrity lock enforced on the production paths**: a tampered `trust: untrusted` skill is refused
  at the recall index, at every disk-scan fallback, and again before sandbox execution.
- **Deterministic context ceiling** at the LLM gateway choke-point: a request whose assembled context
  (messages + system + tools schema, CJK-aware estimate) exceeds the model's window is refused fail-loud
  instead of being sent to fail.

### Fixed
- H2A decision cards: every proposal kind now lands in the decision column (reject button + payload intact);
  multiple pending cards no longer overwrite each other; pending cards (incl. deferred) survive restart.
- Domain deontic rules (`forbid`/`oblige`/`permit`) now reach the runtime guardrail (previously only
  `value.md` did — and it was dropped entirely when `value.md` was blank); no double-injection with compiled
  per-role prompts.
- Decisions made over REST now feed the preference flywheel and the decision log exactly like WebSocket ones.
- Task terminal states (done/error) are recorded into Trace, so the async evaluators see task-level outcomes.
- Backend Chinese reason/detail strings are translated in the English UI (contract-tested: a new backend
  reason without a translation fails the suite).

### Added
- **Taste hit-rate — "more you", proven**: before you decide a card, the system quietly predicts your
  call from your crystallized preferences; after you decide, it settles the bet. The decision-prefs
  panel now shows "it predicts your calls N% of the time (last 20; previous period M%)". Honesty
  built in: only bets placed *before* your decision count, failed predictions aren't counted, and
  below 10 samples it says "still learning you" instead of a fake percentage.
- **Cost before you spend**: execution-type decision cards (delegate / rerun / roundtable) show what
  recent similar tasks actually cost ("~12.4k tokens each, range 8k–18k, last 10") — measured from
  per-task token attribution, never guessed; hidden until there are at least 3 real samples.
- **One-click MCP channel presets** — the Coding capability card now has a "Connect a channel" section:
  pick a well-known MCP server (filesystem scoped to a folder — defaults to the workspace, web fetch,
  GitHub, memory, time, SQLite), fill in a folder/token where needed, and it's written into
  `config.yaml`'s `mcp.servers` (secrets stay in config.yaml, never echoed back). The UI states honestly
  that MCP servers connect at startup, so a restart is required to load the new tools.
- **The decision flow is now phone-friendly** — open the LAN token link (`karvyloop url`) on your phone
  and approve/reject/edit decision cards comfortably: on small screens the cockpit stacks into a single
  column with the decision column first, ACCEPT/DEFER/REJECT become thumb-sized full-width targets, the
  edit-then-accept textarea no longer triggers focus-zoom, and chat opens as a full-screen sheet. Purely
  additive CSS (one media query) — the desktop layout is untouched by construction.
- **Out-of-workspace access, governed**: roles are confined to the workspace by default; when one needs
  a path outside it, the denial surfaces as a decision card ("grant this folder?") — approve once and
  the grants ledger opens exactly that path (tool boundary + capability chain + sandbox mounts all honor
  it; revocable in the Capability overview). **Sensitive paths (API keys, ssh, credential stores) are a
  hard floor: never grantable, immune even to bypass mode.**
- **Open a company (starter templates)**: five staffed, working domains — personal research, finance
  research, job hunt, content studio, home ops — each with values, hard deontic rules, and roles with
  souls; one click in the Domains panel, yours from the first use.

- **Upgrades now snapshot state, verify the install, and auto-roll back on failure** (one click back on
  the console): before switching versions the updater records the current commit
  (`~/.karvyloop/update_rollback.json`) and backs up your instance state files
  (`~/.karvyloop/backups/`, last 3 kept, honest scope in each `manifest.json`); after install it
  smoke-checks that the new code actually imports, and a broken build is rolled back to the previous
  known-good commit automatically — with the reason stated out loud, never a silent broken restart.
  `POST /api/update/rollback` + `rollback_available`/`prev_version` in the update status payload.
- **`karvyloop export`** — your instance is a folder, now with a button: packs `~/.karvyloop`
  (skills, knowledge, preferences, history) into one portable archive with a self-explaining
  `MANIFEST.txt`; secrets (`config.yaml` with your API keys, `console.runtime.json`, `*.lock`)
  are deliberately left behind. Unpack on the new machine, add your key, `karvyloop console` — home.
- **Idle = 0 LLM calls is now a tested contract** (`tests/test_idle_zero_llm.py`): when nothing
  changed, the daily slow side burns nothing — knowledge consolidation and skill tagging hit their
  watermarks without touching the gateway, and the daily loop's idle path provably exits before any
  LLM work. No overnight heartbeat bills.
- **Windows is now a supported (degraded) platform**: the runtime, console and your own crystallized
  (knowledge-only) skills run fully on Windows; only third-party skill scripts are refused — fail-closed,
  with a clear message explaining the degraded mode (no sandbox on Windows yet; Linux/macOS keep the full
  sandbox). Ships a one-line PowerShell installer mirroring `install.sh`
  (`irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex`): dedicated
  venv under `%LOCALAPPDATA%\karvyloop`, a `karvyloop.cmd` shim on the user PATH, Python 3.11+ guard with
  `py -3.11` fallback.
- **Edit, then accept** on decision cards: kinds with an actionable text field let you fix the proposal
  in place and approve your version — the original→edited contrast feeds decision-preference
  crystallization (the richest taste signal there is), and an edit counts as real judgment for the
  high-stakes gate.
- **Under-the-hood drill-down** on task details: expand any task to see the real actions beneath the
  narration (tool calls and outcomes, projected from the Trace).
- **Kinder first-run**: model-connection failures now say what's actually wrong (bad key / wrong
  endpoint / unreachable) before the raw error, and a local **Ollama** install is auto-detected and
  offered as a one-click, no-API-key path.
- **Semantic tag layer for skill recall** (`tags:` in SKILL.md, matched alongside token overlap — no vectors),
  with a daily slow-side backfill that tags untagged own skills once (untrusted skills untouched).
- **Capability overview** (`/api/capability/overview` + a card in the Skills panel): one table for
  tools × mode floors and skills × trust/network/integrity-lock.

### Changed
- Internal restructuring, no behavior change: the workflow / distill / roundtable engines moved out of
  `console/routes.py` into their own console modules, and the core loop moved from `cli/main_loop.py`
  to the new `karvyloop/runtime/` package (`karvyloop.cli.main_loop` remains as a compatibility shim).

### Removed
- Six unreachable packages from an earlier architecture cycle (`ethos`, `syntonos`, `instance`,
  `onboarding`, `l0`, `bus`, ~2.6k lines): superseded by the current design (verify gate + evaluators,
  per-role paradigm compiler, H2A decision cards, `a2a/` transport) or parked concepts whose designs
  live in the design docs. No live code imported them; recoverable from git history.

## [2026.7.1] — 2026-07-01

**First release.** A local-first, loop-native AI agent runtime you can clone → install → point at a model →
drive in ~15 minutes: run one full **execution loop** (intent → run → verify → crystallize a skill → the fast
brain reuses it) and one full **decision loop** (proposal → decision card → you decide → the preference
crystallizes → it pre-aligns the next proposal). Everything below is what's in this first cut.

### Runtime & safety
- Local web **console** (FastAPI REST + `/ws` WebSocket + static SPA) and a terminal TUI, on a fast/slow-brain
  **MainLoop**; a **Forge** coding executor; a multi-provider **LLM gateway** that meters every token at one
  choke-point (any path that talks to a model is counted).
- Entity model **L0–L4** (tool/skill · atom · role · domain), mirrored field-for-field in `schemas/`.
- **Safety is foundational** — every task carries a capability token (zero-permission start); all
  file/network/process access is checked against it; third-party skills run in a **bubblewrap** (Linux) /
  **Seatbelt** (macOS) sandbox, below the agent's trust boundary. macOS adversarially verified on Apple Silicon.
- **Deterministic self-check** — `doctor` / `status` run **without a model** (config / key / port / deps /
  version / data integrity) and tell you the exact fix in your terms; `doctor --fix` auto-heals the reversible
  cases (e.g. corrupt persisted JSON → backup + reset so it boots). An LLM **ops agent** (`/api/ops/diagnose`)
  reasons on top of doctor's real findings but **never executes** — only the deterministic repair auto-applies.

### Roles, domains & collaboration
- **Roles** (a 7-file soul: identity / character / user / commitment / verify / …), **business domains** (like
  companies) with **sub-domain inheritance**, and **value.md + deontic** governance — hard guardrails (top-down,
  un-overridable) + soft defaults (most-specific-wins) — injected into every route / workflow / scheduled /
  roundtable, no opt-out.
- Domain membership is a **dynamic `member_query`** (weak reference resolved at access). A domain member role
  has a **read-only merged view**: its native paradigm plus the value.md / deontic it inherits from the domain.
- Every role is born a **"resourceful subordinate"** — a default, editable collaboration contract in its
  COMMITMENT layer: pursue feasible goals, exhaust your own resourcefulness before coming back, and bring
  evidence, not "what do I do?". The hard safety floor (budget ceiling / infra-dead stop / fail-loud / verify
  gate) is enforced by the runtime and can't be edited away.
- **Karvy 🦫**, the global assistant, turns plain language into a single hand-off, a **roundtable** (roles think
  in parallel → converge), a **workflow** (multi-step DAG), or an **ops** check — always surfaced as an **H2A
  decision card**, never auto-run. Vague instructions are LLM-decomposed over *real* domain members (never
  fabricated). Up to 50+ participants per roundtable / workflow.
- **Durable workflows** survive a console restart (steps checkpointed; completed steps replay instantly); a
  **full-screen Drawflow canvas** for human orchestration. **Scheduled tasks** in plain language (NL→cron),
  Karvy-only (one audit surface), with a ⏰ dashboard.

### The wedge — crystallization (the moat)
- **Skill crystallization stores the _method_, not the answer** — a recall hit re-executes the learned method
  on the current inputs (CBR-style Revise), never replays a stale answer; only semantically-stable results are
  cached. The payoff is fewer tokens (the slow brain is guided), not a cached reply.
- **Decision-interface crystallization** — the **decision card** translates a proposal into your terms, keeps a
  *verified* region (✓/✗, traceable to a gate) visibly separate from Karvy's narration, pre-aligns the standards
  you've crystallized, and forces judgment before a high-stakes commit. ACCEPTing a dispatch runs the independent
  checker and its real verdict becomes a grounded report card (`inconclusive` is shown honestly, never a fake ✓).
- **Two layers, judged along the accountability chain** — a role answers to *you*, so your decisions evaluate it
  (decision-preference crystallization, RLHF-shaped); an atom answers to its *role*, so the role's objective
  measures (achievement × efficiency, past its verify gate) evaluate it (RLVR-shaped). Evaluation is **off the
  hot path**: a drive only executes and writes facts to the **Trace** (run/eval split); a patient, idempotent
  evaluator reads the Trace to score and writes back — learning never competes with the live task.

### Cognition & knowledge
- **Personal knowledge base** — feed a link or notes; a distill flow (fetch → analyze → refine with Karvy →
  *you* decide → compile to Belief) sinks it. It **never silently writes 0** (a thin/failed fetch says so and
  keeps the todo), folds the key points you add in chat into the material, and **re-feeding the same source
  supersedes** the old version. Every knowledge point shows its **real source** (the link or file).
- **Cognition graph** — an Obsidian / map-style mesh: laid out **by connected component** (compact clusters
  packed together, unconnected notes in a tidy grid), drawing only *real* links (semantic + each node's
  strongest) so it branches instead of hairballing, with map-style zoom-level labels (LOD), hover tooltip, big
  hit targets, and a **click-to-select detail card** (title, full content, source, clickable related nodes).
- **No vector DB** — recall is grep + CJK-bigram + LLM semantic tags + spreading activation over the mesh;
  near-duplicate knowledge is tidied via an **H2A consolidate** (suggest → you confirm), off the hot path.

### Execution & models
- **Fast brain** (crystallized-skill hit → re-run with the method) vs **slow brain** (explore from zero);
  **atoms** (task/daemon) are the one reused ReAct loop; **`create_atom`** lets a role mint a new sub-agent when
  nothing fits — it searches the shared pool first, is born on trial, is merged if a near-duplicate exists, and
  can never silently poison the pool (strict-JSON synthesis, duplicate gate, earn-by-reuse lifecycle).
- A delegated role **pursues its goal within a budget** — re-plans on a failed attempt, fixes-and-retries a
  rejected result, stops immediately and says so when the model/network/sandbox is down (infra-dead vs
  replannable, classified end-to-end), and returns an evidence-carrying infeasibility card, never a silent stall.
- **Providers** — Anthropic, OpenAI-compatible, DeepSeek, Kimi/Moonshot (Global + For-Coding) — config-driven
  (`extra_headers` onboards a header-gated endpoint with no code). **Multimodal input** — attach images or
  text/Markdown files, threaded as content blocks.

### Updates, quality & housekeeping
- **By-version releases** (CalVer) with a **detect-and-notify** update path: a dismissible console banner +
  `karvyloop update`; a **one-click upgrade** that runs stop → install → restart for you and reconnects the page
  (localhost-only, CSRF-guarded, single-flight) — it **never auto-upgrades**. Your data in `~/.karvyloop/`
  survives upgrades (config, beliefs, skills, roles/atoms, decision log).
- **Concurrency-safe** — role-registry and memory writes are lock-guarded, so parallel roles/atoms can't lose a
  write. **Bilingual** (en/zh) throughout, with a parity test. Static assets are served `Cache-Control: no-cache`
  so a deployed frontend change shows on a normal refresh. A wiring test fails CI on any orphan endpoint / dead
  call. **MIT**-licensed; the test suite is self-contained (~1880 passed, optional infra skipped cleanly).
