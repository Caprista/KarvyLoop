"""karvyloop.i18n._strings — 字符串表(en 默认 + zh)。

每个 key 一行,两个 locale 同 key 同占位符。新增用户可见字符串时:
  ① 在此两张表都加同名 key(en 必填,zh 跟上);
  ② 调用处 `i18n.t("namespace.key", **占位)` 取串。

key 命名:`<surface>.<what>`,如 console.* / cli.* / wizard.* / tokens.*。
占位符用 `{name}`(str.format);两个 locale 必须用**相同**占位名。

> 这是 A2 的种子表(覆盖 console 启动横幅 + 几条 CLI)。A3 逐面铺开时
> 把 console/CLI/wizard/错误/建议卡的硬编码中文搬进来,调用处改走 t()。
"""
from __future__ import annotations

# ---- English(默认)----
_EN = {
    # console 启动横幅
    "console.lan_warning": (
        "[karvyloop] binding 0.0.0.0 = reachable on your LAN. Local (localhost) stays password-free; "
        "access from other devices requires the token link — run `karvyloop url` on this machine to get it."
    ),
    "console.remote_url": "[karvyloop console] cross-device access (token link): {url}",
    "console.url_hint": "[karvyloop console] get this link again anytime: `{cmd} url`",
    "console.token_ledger_failed": "[karvyloop console] token ledger wiring failed (startup unaffected): {error}",
    "console.karvy_wired_on": "[karvyloop console] Karvy intent analysis wired (LLM on)",
    "console.karvy_wired_off": "[karvyloop console] Karvy intent analysis wired (LLM off — proactive suggestions paused)",
    "console.karvy_wire_failed": "[karvyloop console] Karvy intent analysis wiring failed (console starts anyway): {error}",
    "console.conv_ready": "[karvyloop console] conversation ready (resumed {n} turns)",
    "console.conv_wire_failed": "[karvyloop console] conversation orchestrator wiring failed (console starts anyway): {error}",
    "console.domain_registry_failed": "[karvyloop console] domain registry construction failed (private chat only): {error}",
    "console.opening": "[karvyloop console] opening {url}",
    "cli.init.launching_console": "Setup done — opening your console…",
    "console.uvicorn_missing": "[karvyloop] uvicorn not installed ({error}); run `pip install 'uvicorn[standard]>=0.30'`",
    "console.bind_failed": "[karvyloop console] bind failed: {error}",
    "console.port_fallback": "[karvyloop console] port {orig} is in use — using {port} instead",
    "console.already_running": "[karvyloop console] already running at {url} (v{ver}) — open that instead of starting a second one",
    "console.old_running": "[karvyloop console] an older KarvyLoop (v{old}) is still running at {url}; stop it first, then start v{new} — the upgrade won't take effect while the old one holds the port",
    # CLI 通用
    "cli.config_missing": "[karvyloop] config.yaml not found ({path}) — read-only view",
    "cli.lang_set": "[karvyloop] language set to {lang}",
    "cli.unknown_cmd": "unknown subcommand: {cmd}",
    "cli.no_key_setup": "No usable model/API key yet — KarvyLoop can't run without one. Launching setup (or run `karvyloop init`; or set the provider's API-key env var).",
    "cli.help.update": "check whether a newer version exists (only checks + tells you — never auto-upgrades)",
    "cli.help.url": "print the running console's access links (local + tokened cross-device link)",
    "cli.url.no_runtime": "No running console found (no runtime recorded). Start it with `karvyloop console` first.",
    "cli.url.local": "Local (no token):     {url}",
    "cli.url.remote": "Cross-device (token): {url}",
    "cli.url.remote_none": "Cross-device: this console is bound to localhost only. To reach it from another device, restart with `--host 0.0.0.0`.",
    "update.disabled": "[karvyloop] update check is off (KARVYLOOP_NO_UPDATE_CHECK set). Current: {current}",
    "update.unreachable": "[karvyloop] couldn't reach the release feed (offline / rate-limited). Current: {current}",
    "update.uptodate": "[karvyloop] you're on the latest ({current}).",
    "update.available": "[karvyloop] a newer version is available: {current} → {latest}",
    "update.command": "  upgrade:  {command}",
    "update.notes": "  what's new:  {url}",
    # doctor / status(确定性自检)
    "cli.help.verify_web": "load a web app in a headless browser and report console/runtime errors (runtime gate for web output)",
    "cli.help.verify_web.path": "path to the web app dir or its index.html",
    "cli.help.verify_web.entry": "entry file (default index.html)",
    "verifyweb.unavailable": "[verify-web] Playwright not installed — can't verify browser runtime (syntax only). Install: pip install playwright && playwright install chromium",
    "verifyweb.ok": "[verify-web] ✓ loaded with no console/runtime errors ({url})",
    "verifyweb.inconclusive": "[verify-web] ⚠ couldn't run the browser verifier — runtime NOT verified (not a pass, not a failure):",
    "verifyweb.failed": "[verify-web] ✗ {n} runtime error(s) on load:",
    "cli.help.doctor": "diagnose the install and tell you, in plain terms, what's wrong and how to fix it (no model needed)",
    "cli.help.doctor.fix": "also auto-repair the safe, reversible problems (e.g. back up & reset corrupt data files); risky fixes are left for you",
    "cli.help.doctor.online": "also run liveness checks: is the model endpoint reachable, is the disk writable, can the sandbox start (a quick network probe — never sends your key)",
    "cli.help.status": "quick status: version, model readiness, updates",
    "doctor.header": "KarvyLoop self-check:",
    "status.header": "KarvyLoop status:",
    "doctor.overall.ok": "All good. ✓",
    "doctor.overall.warn": "Usable, with warnings above. ⚠",
    "doctor.overall.fail": "Not ready — fix the ✗ items above.",
    "doctor.msg.config_missing": "No config yet ({path}).",
    "doctor.fix.config_missing": "Run `karvyloop init`, or start `karvyloop console` and follow the setup screen.",
    "doctor.msg.config_unreadable": "Config can't be read ({path}): {err}.",
    "doctor.fix.config_unreadable": "Fix the YAML, or re-run `karvyloop init --force` to rewrite it.",
    "doctor.msg.no_default_model": "No default chat model set.",
    "doctor.fix.no_default_model": "Add one in the console (🤖 Models) or via `karvyloop init`.",
    "doctor.msg.no_key": "Your model needs an API key and none usable is set.",
    "doctor.fix.no_key": "Add a key in the setup screen (it shows where to get one), or set the provider's API-key env var.",
    "doctor.msg.model_not_ready": "Model not ready ({reason}).",
    "doctor.fix.model_not_ready": "Open the console setup screen to finish configuring a model.",
    "doctor.msg.model_ready": "Model ready: {model}.",
    "doctor.msg.deps_ok": "Core dependencies present.",
    "doctor.msg.dep_missing": "Missing required package: {pkg}.",
    "doctor.fix.dep_missing": "Install it: `pip install {pkg}` (or `pip install -e .`).",
    "doctor.msg.dep_optional_missing": "Optional package not installed: {pkg} (that feature is unavailable).",
    "doctor.msg.data_fresh": "Data folder not created yet — it'll appear on first use.",
    "doctor.msg.data_ok": "Your data is intact ({dir}).",
    "doctor.msg.data_corrupt": "Some data files won't parse: {files}.",
    "doctor.fix.data_corrupt": "Back them up and remove them — KarvyLoop will start those fresh (your other data is untouched).",
    "doctor.msg.version_current": "Version {current} (latest).",
    "doctor.msg.version_newer": "Version {current} — {latest} is available.",
    "doctor.fix.version_newer": "Upgrade: {command}",
    "doctor.msg.port_busy": "Console port {port} is in use (already running, or a conflict).",
    "doctor.fix.port_busy": "Use another port: `karvyloop console --port <N>`, or stop what's on {port}.",
    "doctor.msg.port_free": "Console port {port} is free.",
    "doctor.msg.check_error": "A check couldn't run ({err}) — skipped.",
    "doctor.fixing": "Auto-repairing the safe ones:",
    "doctor.after_fix": "After repair:",
    "doctor.nothing_to_fix": "Nothing safe to auto-repair — the items above need your call.",
    "doctor.msg.repaired_data_corrupt": "Backed up & reset corrupt data: {files} (saved as <name>.corrupt.bak).",
    "doctor.msg.repaired_config_missing": "Created a starter config: {path}. Edit it (or run `karvyloop init`) to add your model & key.",
    "doctor.msg.repaired_config_unreadable": "Backed up the broken config to {backup} and wrote a fresh starter at {path}.",
    # --fix 危险项确认(会重写用户配置 → 先问 y/N)
    "doctor.confirm.config_unreadable": "Config at {path} can't be parsed. Back it up and rewrite a fresh starter?",
    "doctor.confirm.skipped": "Skipped (left untouched).",
    "doctor.log_at": "(full log: {path})",
    # --online 活性检查
    "doctor.msg.endpoint_reachable": "Model endpoint reachable: {host} ({provider}).",
    "doctor.msg.endpoint_unreachable": "Model endpoint unreachable: {host} ({provider}) — configured, but can't connect.",
    "doctor.fix.endpoint_unreachable": "Check your network/DNS, the provider's status, or your base_url. (Your key looks set — this is a connectivity issue, not a missing key.)",
    "doctor.msg.local_endpoint_down": "Local model server not responding: {host}:{port} ({provider}).",
    "doctor.fix.local_endpoint_down": "Start it (e.g. `ollama serve`), or point your config at a running endpoint.",
    "doctor.msg.liveness_skipped": "Liveness probe skipped ({reason}) — configure a model first (see above).",
    "doctor.msg.disk_writable": "Data folder is writable ({dir}).",
    "doctor.msg.disk_not_writable": "Data folder isn't writable ({dir}): {err}.",
    "doctor.fix.disk_not_writable": "Check permissions / free space on {dir}.",
    "doctor.msg.sandbox_ok": "Sandbox ready ({impl}).",
    "doctor.msg.sandbox_degraded": "Sandbox degraded but usable ({impl}) — first-party runs pass through, untrusted scripts are refused.",
    "doctor.msg.sandbox_stub": "Sandbox unavailable ({impl}) — code execution is fail-closed (refused).",
    "doctor.fix.sandbox_stub": "On Linux install bubblewrap (`bwrap`); on macOS sandbox-exec should be present. Until then, code-running skills won't run.",
    "doctor.msg.sandbox_error": "Sandbox probe failed ({err}).",
    # 顶层 + 各子命令 help
    "cli.desc": "KarvyLoop — AI-Native Agent runtime (M0 prototype)",
    "cli.help.lang_global": "UI language: en (default) or zh",
    "cli.help.init": "write ~/.karvyloop/config.yaml (local-first defaults)",
    "cli.help.init.config": "config path (default ~/.karvyloop/config.yaml)",
    "cli.help.init.force": "overwrite if it already exists",
    "cli.help.init.no_wizard": "skip the interactive wizard (developer / CI); write default config directly",
    "cli.help.run": "one sentence → sandbox exec → streamed return (vertical slice; wired to MainLoop)",
    "cli.help.run.intent": "natural-language intent (required)",
    "cli.help.run.workspace": "workspace root (default cwd)",
    "cli.help.run.model": "override the default chat model (provider/id form)",
    "cli.help.run.json": "NDJSON output (jump straight to forge, bypass MainLoop)",
    "cli.help.run.no_recall": "fully bypass MainLoop (jump to forge, 1:1 legacy behavior, for debug/manual test)",
    "cli.help.run.skills_dir": "crystallized-skills dir (default ~/.karvyloop/skills; overridable via config.yaml crystallize.skills_dir)",
    "cli.help.chat": "launch the KarvyLoop Workbench TUI (L0+L1+L2+L3)",
    "cli.help.chat.headless": "headless mode (auto-quit, for tests)",
    "cli.help.chat.serve": "textual-serve remote (default 127.0.0.1:8765)",
    "cli.help.chat.host": "serve host (default 127.0.0.1, no LAN bind)",
    "cli.help.chat.port": "serve port (default 8765)",
    "cli.help.replay": "replay the trace events of one drive by task_id (NDJSON)",
    "cli.help.replay.task_id": "drive task ID (uuid4 hex[:16]; optional when --run is given)",
    "cli.help.replay.run": "only emit entries of this run_id (see run_id field in Trace / token ledger)",
    "cli.help.replay.trace_path": "trace.sqlite path (default ~/.karvyloop/trace.sqlite)",
    # run / chat 运行时
    "cli.run.config_missing": "config not found: {path}. run `karvyloop init` first.",
    "cli.interrupted": "interrupted.",
    "cli.chat.readonly_warning": "[karvyloop] config.yaml not found ({path}); TUI read-only view — intent submission will fail; run `karvyloop init` first",
    "chat.empty_retry_fallback": "(I didn't quite catch that — could you say it once more?)",
    # init wizard
    "wizard.choose_provider": "Choose your LLM provider:",
    "wizard.choose_prompt": "Pick [1..{n}] (default 1=local): ",
    "wizard.unknown_provider": "unknown provider: {raw}",
    "wizard.provider_hint": "pick 1..{n} or {names}",
    "wizard.apikey_prompt": "{env_var} (input is visible — not masked in this build; use getpass for real production): ",
    "wizard.apikey_skipped": "  skipped (a {env_var} placeholder will be written to config.yaml; export it, then run)",
    "wizard.apikey_bad": "API key format problem: {err}",
    "wizard.apikey_hint": "check {env_var} is copied in full (no spaces/newlines/placeholder), or rerun `karvyloop init` and choose skip",
    "wizard.written": "✓ written: {target}",
    "wizard.next_ollama": "next: start ollama (default http://127.0.0.1:11434), then karvyloop run \"...\"",
    "wizard.next_apikey": "next: just run karvyloop run \"...\" (the API key is already in config.yaml)",
    # console subcommand help
    "cli.help.console": "start the local HTML console (K3/K4 read-only, K5 factory)",
    "cli.help.console.config": "path to config.yaml (default ~/.karvyloop/config.yaml)",
    "cli.help.console.host": "bind host (default 127.0.0.1; LAN needs explicit 0.0.0.0)",
    "cli.help.console.port": "bind port (default 8766; distinct from textual-serve 8765)",
    "cli.help.console.no_browser": "do not auto-open the browser (headless / smoke)",
    "cli.help.console.no_llm": "skip LLM injection (read-only view + chat_history still work)",
    "cli.help.lang": "UI language: en (default) or zh",
    # karvyloop export(打包带走)
    "cli.export.help": "pack your instance (~/.karvyloop) into one portable archive — secrets excluded",
    "cli.export.done": "Exported your instance: {n} files ({size}) -> {path}",
    "cli.export.excluded": "Excluded on purpose: config.yaml (your API keys stay put), console.runtime.json, *.lock",
    "cli.export.restore": "Restore: unpack into ~/.karvyloop on the new machine, add your key, then run karvyloop console",
    # karvyloop import(export 的回程:一键迁移)
    "cli.import.help": "restore an exported instance archive into ~/.karvyloop — the return trip of `karvyloop export`",
    "cli.import.help.archive": "the archive produced by `karvyloop export` (.zip or .tar.gz)",
    "cli.import.help.force": "merge into an existing instance: overwrite colliding files one by one (local-only files are kept)",
    "cli.import.help.dry_run": "list what would be restored, write nothing",
    "cli.import.not_found": "Archive not found: {path}",
    "cli.import.unreadable": "Cannot read archive (not a zip/tar.gz, or truncated/corrupt): {path} — nothing was written",
    "cli.import.unsafe": "Refusing this archive: unsafe member '{name}' (absolute path, '..', or a link) — nothing was written",
    "cli.import.nothing": "Archive has no instance data to restore: {path}",
    "cli.import.refuse": "{root} already has instance data — refusing to merge without --force.",
    "cli.import.refuse.collisions": "Would overwrite (top-level): {items}",
    "cli.import.refuse.no_collisions": "No file collisions — --force would merge without overwriting anything.",
    "cli.import.refuse.hint": "Use --dry-run to see the full plan, or --force to merge (file-by-file overwrite; your local-only files are kept).",
    "cli.import.dry_run.header": "Dry run — nothing written. Would restore {n} files into {root}:",
    "cli.import.skipped": "Skipped on purpose: {items} (secrets/locks never land; MANIFEST.txt is the archive's own README)",
    "cli.import.done": "Restored your instance: {n} files -> {root}",
    "cli.import.overwrote": "Overwrote {n} existing files (--force)",
    "cli.import.config_kept": "Your local config.yaml was not touched — API keys stay per-machine.",
    "cli.import.next": "Next: add your model API key (`karvyloop init`, or edit ~/.karvyloop/config.yaml), then run `karvyloop console` — your skills, knowledge and history are home.",
    # channels.webhook(出站推送通知正文;用户在 ntfy/Bark/Slack 等承接端看到)
    "channels.webhook.title": "[KarvyLoop] {n} decision card(s) waiting for you",
    "channels.webhook.aging": "⏳{days}d pending ·",
    "channels.webhook.high_risk": "⚠ high-stakes — confirm at the console",
    "channels.webhook.more": "…and {n} more card(s)",
    "channels.webhook.open": "Decide at your console: {url}",
    "channels.webhook.reply_code": "↩ code: {code}",
    "channels.webhook.reply_hint": "Reply \"ACCEPT <code>\" (or REJECT / DEFER) to decide — codes are single-use and time-limited; high-stakes cards: console only.",
    # residents(原住民引荐入住,docs/60;卡文案在出卡时按当前 locale 定稿)
    "residents.referral.summary": "🏠 Your Karvy world is still empty — meet your first resident: {names}. Move in?",
    "residents.referral.basis_footer": (
        "ACCEPT = the role is actually created: its identity, temperament, verification gates and "
        "collaboration contract are plain files you can open and edit — a working example of how to "
        "constrain an agent. Folder access is a hard whitelist recorded in the capability ledger "
        "(visible and revocable anytime); deleting anything always requires your explicit "
        "confirmation with a backup made first. REJECT = it never asks again; DEFER = the card just "
        "waits here."
    ),
    "residents.referral.accepted": (
        "{names} moved in. Folder access granted (revocable in the capability overview): {dirs}. "
        "Every move shows you a preview first — nothing is deleted without your say-so."
    ),
    "residents.referral.no_registry": "Role registry is not wired — cannot move a resident in.",
    "residents.referral.none_found": "No resident mirror found in this install (packaging issue?) — nothing was created.",
    "residents.referral.failed": "Moving in \"{name}\" failed: {error}",
    # butler first lesson(文件管家第一课:方案预览卡 + 兑现回执;卡文案出卡时按 locale 定稿)
    "butler.lesson.summary": (
        "📁 File Butler's first job — a tidy-up plan for {dirs}: {n} move(s), previewed below. "
        "Nothing moves until you approve."
    ),
    "butler.lesson.basis_scan": "Read-only scan: {n} file(s) inventoried in {dirs} (metadata only, nothing touched).",
    "butler.lesson.mode_by_type": "Grouping: by type (Images / Documents / Installers…).",
    "butler.lesson.mode_by_time": "Grouping: by time (year-month folders).",
    "butler.lesson.mode_from_intake": "— following the filing habit you picked during onboarding.",
    "butler.lesson.basis_dups": (
        "Duplicates found: {n} group(s) with byte-identical content (hash-verified). Reported only — "
        "the first lesson never deletes; removing duplicates would be a separate decision of yours."
    ),
    "butler.lesson.basis_hogs": "Biggest space users: {top}. Reported only, not moved.",
    "butler.lesson.basis_truncated": "Note: a folder exceeded {cap} entries — this plan covers the first {cap}.",
    "butler.lesson.basis_safety": (
        "ACCEPT = execute exactly this plan: moves only, within these folders, never a delete, never an "
        "overwrite (existing targets are skipped and reported), every move journaled and reversible. "
        "REJECT = just looking — nothing moves, and that's a perfectly fine choice."
    ),
    "butler.lesson.receipt": (
        "Done: {moved} file(s) filed into place, {skipped} skipped (target existed / vanished / out of "
        "bounds — listed honestly, never forced). Nothing was deleted; every move is journaled in "
        "butler_moves.json, so it can be undone."
    ),
    "butler.lesson.receipt_none": "Nothing needed doing — the plan was already satisfied.",
    "butler.lesson.bad_plan": "The plan on this card is unreadable — refusing to touch any file (rescan to get a fresh plan).",
    # management CLI (noun-verb surface over existing backends: roles/domains/memory/skills/schedules/tokens)
    "cli.help.role": "manage roles (agent mirrors): list / show",
    "cli.help.role.list": "list all roles in this instance",
    "cli.help.role.show": "show one role's identity + composition (atoms/skills)",
    "cli.help.role.id": "role id (directory name)",
    "cli.help.domain": "manage business domains: list / show",
    "cli.help.domain.list": "list all business domains",
    "cli.help.domain.show": "show one domain (value.md, member query, lifecycle)",
    "cli.help.domain.id": "domain id",
    "cli.help.memory": "your knowledge base: recall / add beliefs",
    "cli.help.memory.recall": "recall beliefs by query (grep/overlap, no vectors)",
    "cli.help.memory.recall.query": "recall query text",
    "cli.help.memory.add": "add one belief to your personal knowledge base",
    "cli.help.memory.add.belief": "the belief text to remember",
    "cli.help.memory.scope": "scope: personal (default) or domain",
    "cli.help.memory.limit": "max results (default 8)",
    "cli.help.skill": "your crystallized skills: list",
    "cli.help.skill.list": "list all crystallized + system skills",
    "cli.help.schedule": "scheduled tasks (Karvy-owned): list",
    "cli.help.schedule.list": "list all scheduled tasks",
    "cli.help.token": "token usage ledger: report",
    "cli.help.token.report": "report token usage grouped by source / model / day",
    "cli.help.token.by": "group by: source (default), model, or day",
    "cli.help.json": "print machine-readable JSON instead of a table",
    "cli.help.yes": "confirm the action non-interactively (required off-TTY for create/mutate)",
    "cli.manage.no_instance": "No instance found at {path} — run `karvyloop init` first, or pass --config.",
    "cli.manage.role_none": "No roles yet.",
    "cli.manage.role_not_found": "Role not found: {id}",
    "cli.manage.domain_none": "No business domains yet.",
    "cli.manage.domain_not_found": "Domain not found: {id}",
    "cli.manage.memory_none": "No matching beliefs.",
    "cli.manage.memory_added": "Remembered: {belief}",
    "cli.manage.memory_add_failed": "Wrote to memory but persisting to disk failed: {error}",
    "cli.manage.memory_unavailable": "Memory store unavailable — is this a valid instance?",
    "cli.manage.skill_none": "No crystallized skills yet.",
    "cli.manage.schedule_none": "No scheduled tasks.",
    "cli.manage.token_none": "No token usage recorded yet.",
    "cli.manage.needs_yes": "This creates/changes data. Re-run with --yes to confirm (running off a terminal).",
    "cli.manage.confirm_add": "Add this belief to your knowledge base?",
    "cli.manage.aborted": "Aborted.",
    # ---- create/mutate subcommands (verbs beyond list/show) ----
    "cli.help.role.create": "create a new role (agent mirror)",
    "cli.help.role.create.id": "role id (directory name; letters/digits/_/-)",
    "cli.help.role.create.identity": "identity text (who this role is)",
    "cli.help.role.create.soul": "soul text (SOUL.md)",
    "cli.help.role.create.nickname": "in-domain display name (e.g. Zhang)",
    "cli.help.role.create.model": "role-level model reference (empty = cascade to default)",
    "cli.help.role.rm": "remove a role (destructive — deletes its mirror directory)",
    "cli.help.domain.create": "create a business domain (or a subdomain with --parent)",
    "cli.help.domain.create.name": "domain name",
    "cli.help.domain.create.parent": "parent domain id (subdomain inherits value.md + deontic)",
    "cli.help.domain.archive": "archive a domain (soft-delete — read-only afterwards)",
    "cli.help.schedule.add": "add a scheduled task from natural language (NL -> cron)",
    "cli.help.schedule.add.text": "natural-language description (e.g. 'every day at 8am summarize progress')",
    "cli.help.schedule.rm": "remove a scheduled task",
    "cli.help.schedule.toggle": "enable/disable a scheduled task",
    "cli.help.schedule.on": "enable the task",
    "cli.help.schedule.off": "disable the task",
    "cli.help.schedule.id": "scheduled task id",
    "cli.help.skill.import": "import a third-party skill (Agent-Skills open standard; runs sandboxed)",
    "cli.help.skill.import.source": "github spec / .zip|.skill url or path / local folder",
    "cli.help.skill.import.overwrite": "overwrite an existing skill of the same name",
    "cli.manage.role_id_required": "A role id is required (--id).",
    "cli.manage.role_create_failed": "Could not create role: {error}",
    "cli.manage.role_created": "Created role: {id}",
    "cli.manage.role_removed": "Removed role: {id}",
    "cli.manage.confirm_role_rm": "Delete role '{id}' and its mirror directory? This cannot be undone.",
    "cli.manage.domain_name_required": "A domain name is required (--name).",
    "cli.manage.domain_create_failed": "Could not create domain: {error}",
    "cli.manage.domain_created": "Created domain: {name} ({id})",
    "cli.manage.domain_archived": "Archived domain: {id}",
    "cli.manage.confirm_domain_create": "Create business domain '{name}'?",
    "cli.manage.confirm_domain_archive": "Archive domain '{id}' (becomes read-only)?",
    "cli.manage.confirm_role_create": "Create role '{id}'?",
    "cli.manage.schedule_text_required": "A natural-language description is required.",
    "cli.manage.schedule_no_llm": "No model is configured — NL->cron needs an LLM. Run `karvyloop init` or pass --config.",
    "cli.manage.schedule_not_understood": "Couldn't parse a clear time from that — try rephrasing (e.g. 'every day at 8am ...').",
    "cli.manage.schedule_parsed": "Parsed: cron={cron}  intent={intent}",
    "cli.manage.confirm_schedule_add": "Add this scheduled task?",
    "cli.manage.schedule_added": "Scheduled: {id}  [{cron}]  {title}",
    "cli.manage.schedule_removed": "Removed scheduled task: {id}",
    "cli.manage.confirm_schedule_rm": "Remove scheduled task '{id}'?",
    "cli.manage.schedule_not_found": "Scheduled task not found: {id}",
    "cli.manage.schedule_toggled": "Scheduled task {id} is now {state}.",
    "cli.manage.state_on": "enabled",
    "cli.manage.state_off": "disabled",
    "cli.manage.confirm_skill_import": "Import skill from '{source}' (third-party, untrusted)?",
    "cli.manage.skill_import_failed": "Skill import failed: {error}",
    "cli.manage.skill_imported": "Imported skill: {name} ({files} files){scripts}",
    "cli.manage.skill_scripts_note": " — contains scripts/ (execution is sandboxed)",
    # mesh 任务板:跨设备接活 H2A 卡(docs/74 §6.2/§6.3)
    "mesh.takeover.summary": "A task on your “{device}” device got interrupted: {intent} — pick it up on this one?",
    "mesh.takeover.basis": (
        "Task {task_id} was last claimed by your “{device}” device, but its lease expired with no "
        "heartbeat — that device looks offline mid-run (origin: {source}). ACCEPT = re-run it from scratch "
        "on this device and record the takeover on your shared task board (other devices then stop offering it). "
        "REJECT / no decision = nothing happens; any of your devices can still pick it up later."
    ),
    "mesh.takeover.receipt": "{detail} (takeover recorded on your shared task board)",
    # ---- 提案工厂 summary/basis(服务端出卡时按当前 locale 定稿;LLM 动态文本是数据不走这里)----
    # confirm_decision_pref(decision_wire)
    "proposal.confirm_pref.kind_constraint": "constraint",
    "proposal.confirm_pref.kind_taste": "taste",
    "proposal.confirm_pref.kind_standing": "standing",
    "proposal.confirm_pref.kind_default": "preference",
    "proposal.confirm_pref.summary": "Make this your default preference? [{label}] {content}",
    "proposal.confirm_pref.basis": (
        "I noticed this in how you decide; once saved, my proposals will align with it up front — "
        "fewer rejections, less repeating yourself."
    ),
    # run_task resume(proactive)
    "proposal.run_task.summary": "Last time “{intent}” didn't finish (error/interrupted) — want me to retry?",
    "proposal.run_task.basis": (
        "The task “{intent}” run by “{who}” ended with status = error (failed/interrupted) — it never finished. "
        "Cause / last output: {err}. Retry = run it again with the same intent."
    ),
    "proposal.run_task.default_error": "failed/interrupted",
    "proposal.run_task.default_who": "Karvy",
    # silence(挣来的静音:授权/续期/吊销)
    "proposal.silence.domain_suffix": " (domain “{d}”)",
    "proposal.silence_grant.summary": (
        "On “{kind}”{dom} cards I've called your decision right {hits} of the last {n} times "
        "(95% confidence lower bound {lb}%) — want me to quietly handle this kind for you from now on?"
    ),
    "proposal.silence_grant.basis": (
        "This isn't a request for more power — it's a score card: {hits} right out of {n} on this kind of card, "
        "≥{min_lb}% even at the 95% confidence lower bound (not a lucky streak; the accept and reject sides each "
        "clear the bar). Of those, I predicted you'd REJECT {reject_pred} time(s) and was right {reject_correct} "
        "time(s) (proof I can block the bad ones for you, not just nod along). After ACCEPT, for 30 days: I **only** "
        "handle cards where I predict you'd ACCEPT with ≥{min_conf}% confidence; predicted-REJECT or low-confidence "
        "cards still come to you; I'll also randomly let some through as ordinary cards to check my answers (you "
        "won't be told which are spot checks); irreversible things — deletes, outbound sends, payments, going live — "
        "always come to you. Every silent action leaves a full trail (run record + ledger), and after 30 days renewal "
        "must be your own hand; one wrong call and the grant is **revoked automatically, immediately** — and you can "
        "revoke it anytime. REJECT = keep things as they are, every card asks you."
    ),
    "proposal.silence_renew.audit_some": "spot-checked {audit_n} time(s), {audit_hits} right",
    "proposal.silence_renew.audit_none": "no spot-check samples accrued this period",
    "proposal.silence_renew.mark_overturned": "⚠overturned ",
    "proposal.silence_renew.mark_failed": "✗failed ",
    "proposal.silence_renew.review_item": "{mark}“{gist}”",
    "proposal.silence_renew.review_disp": (
        ". The {n} highest-risk item(s) this period (overturned/failed/most expensive first): {gists}"
    ),
    "proposal.silence_renew.summary": (
        "The mute grant for “{kind}”{dom} hits its 30-day limit — last month it silenced {silenced_n} card(s) "
        "for you, {audit}; renew for 30 days?"
    ),
    "proposal.silence_renew.basis_head": (
        "A mute grant only lasts 30 days, and renewal must be your own hand — reconciliation nobody reads doesn't "
        "count; if you don't click, it stops (this kind of card is already back to asking you one by one). "
        "This period's account: {silenced_n} silenced, {audit}"
    ),
    "proposal.silence_renew.basis_oldest": ", oldest trail record {pid}",
    "proposal.silence_renew.basis_tail": (
        "; every item is auditable in the ledger / run records — read them one by one, then decide. "
        "ACCEPT = renew 30 days (same rules: only handle cards I predict you'd ACCEPT with ≥{min_conf}% "
        "confidence, keep the random spot checks, one wrong call revokes immediately); "
        "REJECT = no renewal, every card asks you."
    ),
    "proposal.silence_revoked.summary": (
        "Mute grant for “{kind}”{dom} auto-revoked — this kind of card is back to asking you one by one"
    ),
    "proposal.silence_revoked.reason_default": "I called one of your decisions wrong",
    "proposal.silence_revoked.basis": (
        "{reason}. Earned silence is only valid while the hit rate holds — one wrong call revokes it immediately "
        "(conservative boundary); to earn it back, fresh reconciliation has to accrue after revocation "
        "(95% confidence lower bound ≥{min_lb}%, at least {min_n} samples) before I ask you again. "
        "ACCEPT = acknowledged."
    ),
    # cocreate_finalize(cocreation)
    "proposal.cocreate.summary_template": "Co-creation final: open template domain “{name}” in one step",
    "proposal.cocreate.basis_template": (
        "In the co-creation session you picked the ready-made template “{name}”. ACCEPT = go through the existing "
        "instantiate path and actually create that domain and its soul-configured roles (idempotent: an active "
        "domain with the same name is refused and reported honestly)."
    ),
    "proposal.cocreate.summary_custom": "Co-creation final: create business domain “{name}” + {n} role(s)",
    "proposal.cocreate.basis_custom": (
        "This is the final draft of the co-creation session (nothing was written during S1/S2 — zero side effects). "
        "Only ACCEPT creates things for real: roles go through RoleRegistry.create (the diligence contract "
        "COMMITMENT is seeded from the same single source as system defaults / imports), and the domain lands with "
        "value.md + real deontic guardrails. If any field on the card is off, edit it right there and approve."
    ),
    # weekly_digest
    "proposal.weekly_digest.gist_quiet": "a quiet week (no tasks, no spend)",
    "proposal.weekly_digest.gist": "ran {runs} task(s) ({ok} succeeded / {fail} failed), burned {tokens} tokens",
    "proposal.weekly_digest.summary": "Weekly digest {start}→{end}: {gist}",
    "proposal.weekly_digest.basis": (
        "Every number is deterministically aggregated from Trace / tokens.db / the decision ledger — zero LLM, "
        "fully traceable (each item carries a trace_ref/id). ACCEPT only means “read”; nothing gets executed."
    ),
    # archive_stale(knowledge_tick)
    "proposal.archive_stale.summary": "🗄️ {n} knowledge item(s) unused for a year — archive them?",
    "proposal.archive_stale.basis": (
        "These {n} knowledge items haven't been recalled (or updated) in over a year — likely stale: {shown}. "
        "ACCEPT = mark them invalid and archive (**invalidate, not delete**: they stay in the library, auditable "
        "and reversible, just out of recall); REJECT = keep them in recall."
    ),
    # promote_experience(promotion_tick)
    "proposal.promote_exp.line": "Before (in-domain): {before}\nPromoted (general): {content}",
    "proposal.promote_exp.line_why": "\n  ↳ why it generalizes: {why}",
    "proposal.promote_exp.summary": (
        "📜 “{role}” has {n} in-domain lesson(s) ready to become general playbook — promote?"
    ),
    "proposal.promote_exp.basis": (
        "These lessons of “{role}” in domain “{domain}” passed the generalization check and the de-identification "
        "rewrite. ACCEPT = promote them into the role's general playbook (usable across domains; any future "
        "outward-facing surface only ever sees this layer); REJECT = skip this round, keep using them in-domain "
        "(they won't be re-proposed unless they change). After promotion, deleting the domain no longer retracts "
        "them automatically — retract per item in the memory panel.\n\n{lines}"
    ),
    # route_to_role
    "proposal.route.summary": "Hand “{requirement}” to “{role}” of business domain “{domain_name}”",
    "proposal.route.basis": (
        "This belongs to business domain “{domain_name}”; rather than overstep and do it myself, I'd delegate it "
        "to “{role}” working under that domain's value.md governance. Nothing is handed over until you ACCEPT."
    ),
    # roundtable
    "proposal.roundtable.who_default": "the roles in the group",
    "proposal.roundtable.summary": "Open a roundtable in “{group}” with {who} to discuss “{topic}”",
    "proposal.roundtable.basis": (
        "You want several roles to discuss this together — that's a **roundtable** (people around a table), not "
        "handing the job to one person (delegation). I'll gather {who} in group “{group}”, align the goal with you "
        "first, then start the discussion. The table only opens once you ACCEPT."
    ),
    # ops_fix
    "proposal.ops_fix.fallback_summary": "Ops diagnosis",
    "proposal.ops_fix.cause": "Likely cause: {cause}",
    "proposal.ops_fix.fix": "Suggested fix: {fix}",
    "proposal.ops_fix.auto": (
        "ACCEPT runs a **deterministic, reversible repair** (backup first, then reset; recoverable from "
        ".corrupt.bak) — no model gets to change your system."
    ),
    "proposal.ops_fix.manual": (
        "This is an LLM diagnosis, **unverified**; ACCEPT only means you acknowledge it — the system will **not** "
        "change anything by itself. Please follow the steps above by hand."
    ),
    # merge_atoms
    "proposal.merge_atoms.head": "Merge {n} near-duplicate atoms into canonical atom “{canon}”: {members}.",
    "proposal.merge_atoms.reason": "Reasoning: {reason}",
    "proposal.merge_atoms.why": (
        "Merging = less duplication, more reuse (moat: batch-imported atoms often see low reuse because "
        "near-duplicates never get merged)."
    ),
    "proposal.merge_atoms.accept": (
        "ACCEPT will **rewire-before-delete**: first repoint every role referencing these atoms to the canonical "
        "one, then delete the redundant ones — **never leaving dangling references**; doing nothing is also safe "
        "(they just stay unmerged)."
    ),
    "proposal.merge_atoms.summary": "Merge {n} near-duplicate atoms → “{canon}”",
    # fs_access
    "proposal.fs_access.op_read": "read",
    "proposal.fs_access.op_write": "write",
    "proposal.fs_access.op_read_write": "read/write",
    "proposal.fs_access.who_role": "Role “{role}”",
    "proposal.fs_access.who_default": "A running role",
    "proposal.fs_access.summary": "{who} requests {op} access to a path outside your workspace: {path}",
    "proposal.fs_access.basis": (
        "It needs to touch this path to do its job, but the path is outside your workspace — closed by default "
        "under least privilege. ACCEPT = grant this path permanently (revocable anytime in the capability "
        "overview); key/credential paths never show up here (hard floor)."
    ),
    # merge_knowledge
    "proposal.merge_knowledge.head": "These {n} knowledge items say essentially the same thing: {shown}.",
    "proposal.merge_knowledge.reason": "Reasoning: {reason}",
    "proposal.merge_knowledge.accept": (
        "Suggest merging into one item “{label}”. ACCEPT = write the merged item first, then delete the "
        "merged-away originals (no data loss on mid-failure); doing nothing is also safe (the library just keeps "
        "some near-duplicates)."
    ),
    "proposal.merge_knowledge.summary": "🧹 Merge {n} near-duplicate knowledge items → “{label}”",
    # confirm_result
    "proposal.confirm_result.default_role": "the role",
    "proposal.confirm_result.default_req": "this task",
    "proposal.confirm_result.basis": (
        "“{role}” minted {n} new capabilities while completing “{req}”: {lines}. If you approve this result → "
        "{role} weighs which ones deserve a place in its own toolbox (they only become official once other roles "
        "reuse them); no action / not approved → they stay on trial and get cleaned up automatically if nobody "
        "uses them."
    ),
    "proposal.confirm_result.summary": (
        "“{role}” finished “{req}” and minted {n} new capabilities — approve the result to keep the useful ones?"
    ),
    # infeasible_report
    "proposal.infeasible.default_goal": "(unnamed goal)",
    "proposal.infeasible.default_role": "the role",
    "proposal.infeasible.attempt_unfinished": "unfinished",
    "proposal.infeasible.attempt_line": "attempt {i}: {term}",
    "proposal.infeasible.attempt_note": " ({note})",
    "proposal.infeasible.no_trail": "(no trail)",
    "proposal.infeasible.basis": (
        "“{role}” replanned on its own {n} time(s) trying to achieve “{goal}” and still didn't make it. "
        "Trail: {trail}. Automatic replanning can't break through — this is an evidence-backed conclusion, not a "
        "“what do I do?”: your call (accept it and let go / defer / adjust the goal or add resources and retry)."
    ),
    "proposal.infeasible.summary": "“{role}” didn't achieve “{goal}” (self-replanned {n} time(s))",
    # inbox(邮件管道)
    "proposal.inbox.no_body": "(no body)",
    "proposal.inbox_decision.default_reason": "triage judged this needs your call",
    "proposal.inbox_decision.default_action": "(see the mail)",
    "proposal.inbox_decision.summary": "📧 Needs your call: {sender} “{subject}”",
    "proposal.inbox_decision.basis": (
        "This mail was triaged as **needs your decision** ({reason}). Suggested action: {action}. "
        "Body snippet: {snippet}. This pipe only notifies and suggests — nothing is ever sent without your "
        "confirmation; ACCEPT only records your decision, it does not auto-reply or trigger any external action."
    ),
    "proposal.inbox_reply.default_reason": "triage judged a reply can be drafted first",
    "proposal.inbox_reply.summary": "✉️ Draft reply awaiting your approval: {sender} “{subject}”",
    "proposal.inbox_reply.basis": (
        "This mail was triaged as **needs a reply** ({reason}); a draft is prepared (edit it in place before "
        "approving). ACCEPT = save the draft to the ledger and show it to you, and **you copy and send it "
        "yourself** — the system never sends mail on its own (nothing goes out without your confirmation, "
        "hard rule)."
    ),
    # revise_skill(crystallize/revision)
    "proposal.revise_skill.summary": (
        "Skill “{skill}” has shown poor objective signals lately — proposing a major revision of its method "
        "(rewrite / over half the steps removed; needs your review)"
    ),
    "proposal.revise_skill.basis": (
        "Trigger: {trigger}; failing-sample traces: {traces}. The change is too large (method rewritten / over "
        "half the steps removed), so per the accountability chain it goes to you — no silent method swap."
    ),
    "proposal.revise_skill.traces_rolled": "(originals have rolled over)",
    "proposal.revise_skill.trigger": "confidence={conf}(<{thresh} trips) bad={n_bad}/{total}(≥{min_bad} trips)",
    # external_adopt(external_collab)
    "proposal.external_adopt.default_citizen": "external collaborator",
    "proposal.external_adopt.summary": "Adopt the output of {badge} “{cid}”? (external executor · untrusted data)",
    "proposal.external_adopt.basis_head": (
        "This is output from external executor “{badge} {cid}” — **untrusted data** (it doesn't carry your "
        "accountability; no accountability chain)."
    ),
    "proposal.external_adopt.basis_ctx": "Context: {ctx}",
    "proposal.external_adopt.basis_tail": (
        "ACCEPT = you approve adopting this output, and only then does it cross the provenance boundary (into "
        "memory / as a conclusion / to downstream roles); REJECT / no action = reference only, never entering "
        "memory automatically, never triggering anyone. Raw output:\n{preview}"
    ),
    "proposal.external_adopt.empty": "(empty)",
    # spend_budget 提醒卡(llm/spend_budget + console/entry 兜底)
    "proposal.spend.period_month": "this month",
    "proposal.spend.period_day": "today",
    "proposal.spend.summary_blocked": (
        "Budget used up: {period} spend {used} / cap {limit} ({pct}%) — background automatic tasks are paused; "
        "foreground is unaffected. Raise the cap or change on_limit to continue."
    ),
    "proposal.spend.summary_warn": "Spend reminder: {period} spend {used} / cap {limit} ({pct}%, {tier}% tier reached)",
    "proposal.spend.fallback_summary": "Spend reminder",
    # resolve_conflict(domain/skill_conflict → proposal_from_conflict)
    "conflict.rule_forbid": "forbidden rule",
    "conflict.rule_oblige": "obligation",
    "conflict.rule_value": "value principle",
    "conflict.rule_generic": "rule",
    "conflict.summary": "Skill “{skill}” may violate {label} “{rule}” of domain “{domain}” ({role})",
    "conflict.judge_reason": (
        "the skill's usage text hits keywords of a {label} — possible conflict, please confirm"
    ),
    # crystallize_skill ACCEPT 回执(proposal_handlers)
    "receipt.crystallize.default_habit": "this habit",
    "receipt.crystallize.accepted": (
        "Adopted “{summary}” — keep working this way and the system will crystallize it into a "
        "skill automatically"
    ),
}

# ---- 中文 ----
_ZH = {
    "console.lan_warning": (
        "[karvyloop] 绑 0.0.0.0 = 局域网可达。本机(localhost)免密;从别的设备访问需要带 token 的链接 —— "
        "在这台机器上跑 `karvyloop url` 获取。"
    ),
    "console.remote_url": "[karvyloop console] 跨设备访问(带 token 链接): {url}",
    "console.url_hint": "[karvyloop console] 以后再取这条链接: `{cmd} url`",
    "console.token_ledger_failed": "[karvyloop console] token 账本接线失败(不影响启动): {error}",
    "console.karvy_wired_on": "[karvyloop console] 小卡意图分析已接线(LLM on)",
    "console.karvy_wired_off": "[karvyloop console] 小卡意图分析已接线(LLM off — 暂不主动建议)",
    "console.karvy_wire_failed": "[karvyloop console] 小卡意图分析接线失败(console 照常起): {error}",
    "console.conv_ready": "[karvyloop console] 对话已就绪(续上 {n} 轮)",
    "console.conv_wire_failed": "[karvyloop console] 对话编排器接线失败(console 照常起): {error}",
    "console.domain_registry_failed": "[karvyloop console] domain_registry 构造失败(仅私聊): {error}",
    "console.opening": "[karvyloop console] 正在打开 {url}",
    "cli.init.launching_console": "配置好了 —— 正在打开你的控制台…",
    "console.uvicorn_missing": "[karvyloop] uvicorn 未安装({error});`pip install 'uvicorn[standard]>=0.30'`",
    "console.bind_failed": "[karvyloop console] 绑定失败: {error}",
    "console.port_fallback": "[karvyloop console] 端口 {orig} 被占用,已自动改用 {port}",
    "console.already_running": "[karvyloop console] 已有实例在 {url} 运行(v{ver})—— 直接打开它,别再开第二个",
    "console.old_running": "[karvyloop console] 端口上还有旧版 KarvyLoop(v{old})在 {url} 运行;请先停掉它再启动 v{new} —— 旧版占着端口时升级不会生效",
    "cli.config_missing": "[karvyloop] config.yaml 不存在({path}) — 只读视图",
    "cli.lang_set": "[karvyloop] 语言已设为 {lang}",
    "cli.unknown_cmd": "未知子命令:{cmd}",
    "cli.no_key_setup": "还没有可用模型/API Key —— 没有它 KarvyLoop 跑不起来。正在进入配置(或运行 `karvyloop init`;或设置对应 provider 的 API key 环境变量)。",
    "cli.help.update": "检查有没有新版本(只检测+提示,绝不自动升级)",
    "cli.help.url": "打印当前运行中 console 的访问链接(本机免密 + 跨设备带 token 链接)",
    "cli.url.no_runtime": "没有正在运行的 console(未记录 runtime)。先 `karvyloop console` 起服务。",
    "cli.url.local": "本机访问(免 token):    {url}",
    "cli.url.remote": "跨设备访问(带 token):  {url}",
    "cli.url.remote_none": "跨设备:当前 console 只绑了本机(localhost)。要从别的设备访问,用 `--host 0.0.0.0` 重启。",
    "update.disabled": "[karvyloop] 更新检测已关闭(设了 KARVYLOOP_NO_UPDATE_CHECK)。当前:{current}",
    "update.unreachable": "[karvyloop] 连不上发布源(离线 / 被限流)。当前:{current}",
    "update.uptodate": "[karvyloop] 已是最新({current})。",
    "update.available": "[karvyloop] 有新版本:{current} → {latest}",
    "update.command": "  升级:  {command}",
    "update.notes": "  更新内容:  {url}",
    # doctor / status(确定性自检)
    "cli.help.verify_web": "用无头浏览器真加载网页产物,抓控制台/运行时报错(网页类的运行时验收门)",
    "cli.help.verify_web.path": "网页产物目录或它的 index.html 路径",
    "cli.help.verify_web.entry": "入口文件(默认 index.html)",
    "verifyweb.unavailable": "[verify-web] 没装 Playwright —— 没法验浏览器运行时(只能验语法)。装:pip install playwright && playwright install chromium",
    "verifyweb.ok": "[verify-web] ✓ 加载无控制台/运行时报错({url})",
    "verifyweb.inconclusive": "[verify-web] ⚠ 浏览器验证器没跑成 —— 运行时没验到(既不算通过、也不算失败):",
    "verifyweb.failed": "[verify-web] ✗ 加载时有 {n} 条运行时报错:",
    "cli.help.doctor": "体检安装环境,用人话告诉你哪坏了、怎么修(不需要模型)",
    "cli.help.doctor.fix": "顺便自动修可逆的安全问题(如备份并重置坏掉的数据文件);有风险的留给你拍",
    "cli.help.doctor.online": "顺便跑活性检查:模型端点连不连得上、磁盘可不可写、沙箱能不能起(一次网络探测——绝不发送你的 key)",
    "cli.help.status": "快速状态:版本、模型就绪、更新",
    "doctor.header": "KarvyLoop 自检:",
    "status.header": "KarvyLoop 状态:",
    "doctor.overall.ok": "一切正常。✓",
    "doctor.overall.warn": "能用,但有上面的警告。⚠",
    "doctor.overall.fail": "还不能用 —— 先修上面带 ✗ 的项。",
    "doctor.msg.config_missing": "还没有配置({path})。",
    "doctor.fix.config_missing": "跑 `karvyloop init`,或启动 `karvyloop console` 按配置页走一遍。",
    "doctor.msg.config_unreadable": "配置读不了({path}):{err}。",
    "doctor.fix.config_unreadable": "修一下 YAML,或 `karvyloop init --force` 重写。",
    "doctor.msg.no_default_model": "没设默认 chat 模型。",
    "doctor.fix.no_default_model": "在控制台(🤖 模型)加一个,或跑 `karvyloop init`。",
    "doctor.msg.no_key": "你的模型需要 API key,但没有可用的。",
    "doctor.fix.no_key": "在配置页加一个 key(它会告诉你去哪拿),或设好对应 provider 的 API-key 环境变量。",
    "doctor.msg.model_not_ready": "模型未就绪({reason})。",
    "doctor.fix.model_not_ready": "打开控制台配置页,把模型配完。",
    "doctor.msg.model_ready": "模型就绪:{model}。",
    "doctor.msg.deps_ok": "核心依赖齐全。",
    "doctor.msg.dep_missing": "缺必需依赖:{pkg}。",
    "doctor.fix.dep_missing": "装上:`pip install {pkg}`(或 `pip install -e .`)。",
    "doctor.msg.dep_optional_missing": "可选依赖没装:{pkg}(对应功能不可用)。",
    "doctor.msg.data_fresh": "数据目录还没建 —— 首次用时会出现。",
    "doctor.msg.data_ok": "你的数据完好({dir})。",
    "doctor.msg.data_corrupt": "有几个数据文件解析不了:{files}。",
    "doctor.fix.data_corrupt": "备份后删掉它们 —— KarvyLoop 会把这几个从空重建(其余数据不动)。",
    "doctor.msg.version_current": "版本 {current}(最新)。",
    "doctor.msg.version_newer": "版本 {current} —— 有新版 {latest}。",
    "doctor.fix.version_newer": "升级:{command}",
    "doctor.msg.port_busy": "控制台端口 {port} 被占用(已在跑,或端口冲突)。",
    "doctor.fix.port_busy": "换个端口:`karvyloop console --port <N>`,或停掉占用 {port} 的进程。",
    "doctor.msg.port_free": "控制台端口 {port} 空闲。",
    "doctor.msg.check_error": "某项检查没跑成({err})—— 已跳过。",
    "doctor.fixing": "正在自动修复安全的那些:",
    "doctor.after_fix": "修复后:",
    "doctor.nothing_to_fix": "没有可安全自动修的 —— 上面剩下的都得你拍。",
    "doctor.msg.repaired_data_corrupt": "已备份并重置坏掉的数据:{files}(存为 <name>.corrupt.bak)。",
    "doctor.msg.repaired_config_missing": "已创建初始配置:{path}。编辑它(或跑 `karvyloop init`)填上你的模型和 key。",
    "doctor.msg.repaired_config_unreadable": "已把坏掉的配置备份到 {backup},并在 {path} 写了一份新的初始配置。",
    "doctor.confirm.config_unreadable": "{path} 的配置解析不了。要备份它并重写一份初始配置吗?",
    "doctor.confirm.skipped": "已跳过(原样不动)。",
    "doctor.log_at": "(完整日志:{path})",
    "doctor.msg.endpoint_reachable": "模型端点连得上:{host}({provider})。",
    "doctor.msg.endpoint_unreachable": "模型端点连不上:{host}({provider})—— 配是配了,但连不通。",
    "doctor.fix.endpoint_unreachable": "查一下网络/DNS、provider 是否正常、或你的 base_url。(你的 key 看起来是设了的——这是连通性问题,不是缺 key。)",
    "doctor.msg.local_endpoint_down": "本地模型服务没响应:{host}:{port}({provider})。",
    "doctor.fix.local_endpoint_down": "起一下它(如 `ollama serve`),或把配置指到一个在跑的端点。",
    "doctor.msg.liveness_skipped": "活性探测已跳过({reason})—— 先配好一个模型(见上)。",
    "doctor.msg.disk_writable": "数据目录可写({dir})。",
    "doctor.msg.disk_not_writable": "数据目录不可写({dir}):{err}。",
    "doctor.fix.disk_not_writable": "查一下 {dir} 的权限/剩余空间。",
    "doctor.msg.sandbox_ok": "沙箱就绪({impl})。",
    "doctor.msg.sandbox_degraded": "沙箱降级但可用({impl})—— 第一方直通,不受信脚本拒跑。",
    "doctor.msg.sandbox_stub": "沙箱不可用({impl})—— 代码执行 fail-closed(拒跑)。",
    "doctor.fix.sandbox_stub": "Linux 装 bubblewrap(`bwrap`);macOS 应自带 sandbox-exec。在此之前,跑代码的技能无法运行。",
    "doctor.msg.sandbox_error": "沙箱探测失败({err})。",
    "cli.desc": "KarvyLoop — AI-Native Agent 运行时(M0 雏形)",
    "cli.help.lang_global": "UI 语言:en(默认)或 zh",
    "cli.help.init": "写 ~/.karvyloop/config.yaml(本地优先默认)",
    "cli.help.init.config": "配置路径(默认 ~/.karvyloop/config.yaml)",
    "cli.help.init.force": "覆盖已存在",
    "cli.help.init.no_wizard": "跳过交互式 wizard(开发者 / CI 用),直接写默认 config",
    "cli.help.run": "一句话→沙箱执行→流式返回(垂直切片;M3+ 批 4 接 MainLoop)",
    "cli.help.run.intent": "自然语言意图(必填)",
    "cli.help.run.workspace": "工作区根(默认 cwd)",
    "cli.help.run.model": "覆盖默认 chat 模型(provider/id 形式)",
    "cli.help.run.json": "NDJSON 输出(直跳 forge,不走 MainLoop)",
    "cli.help.run.no_recall": "完全跳 MainLoop(直跳 forge,1:1 旧行为,debug/手动测试用)",
    "cli.help.run.skills_dir": "已结晶技能目录(默认 ~/.karvyloop/skills,可被 config.yaml crystallize.skills_dir 覆盖)",
    "cli.help.chat": "启动 KarvyLoop Workbench TUI(L0+L1+L2+L3)",
    "cli.help.chat.headless": "headless 模式(自动 quit,给测试用)",
    "cli.help.chat.serve": "textual-serve 远程(默认 127.0.0.1:8765)",
    "cli.help.chat.host": "serve host(默认 127.0.0.1,不绑 LAN)",
    "cli.help.chat.port": "serve port(默认 8765)",
    "cli.help.replay": "按 task_id 重放一次 drive 的 trace 事件(NDJSON)",
    "cli.help.replay.task_id": "drive 任务 ID(uuid4 hex[:16];给了 --run 可省)",
    "cli.help.replay.run": "只输出该 run_id 的条目(run_id 见 Trace / token 账本的 run_id 字段)",
    "cli.help.replay.trace_path": "trace.sqlite 路径(默认 ~/.karvyloop/trace.sqlite)",
    "cli.run.config_missing": "配置不存在:{path}。先跑 karvyloop init。",
    "cli.interrupted": "中断。",
    "cli.chat.readonly_warning": "[karvyloop] config.yaml 不存在({path});TUI 只读视图 — intent 提交将失败,先跑 `karvyloop init`",
    "chat.empty_retry_fallback": "(这次没接住,能再说一遍吗?)",
    "wizard.choose_provider": "选择你的 LLM provider:",
    "wizard.choose_prompt": "选 [1..{n}] (默认 1=本地): ",
    "wizard.unknown_provider": "未知 provider: {raw}",
    "wizard.provider_hint": "选 1..{n} 或 {names}",
    "wizard.apikey_prompt": "{env_var} (输入会隐藏但本版不屏蔽,真生产用 getpass): ",
    "wizard.apikey_skipped": "  跳过(将在 config.yaml 写 {env_var} 占位,你 export 后再跑)",
    "wizard.apikey_bad": "API key 格式有问题: {err}",
    "wizard.apikey_hint": "检查 {env_var} 是否复制完整(无空格/换行/占位符),或重跑 karvyloop init 选 skip",
    "wizard.written": "✓ 已写入:{target}",
    "wizard.next_ollama": "下一步:启动 ollama (默认 http://127.0.0.1:11434),然后 karvyloop run \"...\"",
    "wizard.next_apikey": "下一步:直接 karvyloop run \"...\" 即可(API key 已写入 config.yaml)",
    "cli.help.console": "启动本地 HTML 控制台(K3/K4 只读,K5 工厂)",
    "cli.help.console.config": "config.yaml 路径(默认 ~/.karvyloop/config.yaml)",
    "cli.help.console.host": "绑定 host(默认 127.0.0.1;LAN 需显式 0.0.0.0)",
    "cli.help.console.port": "绑定 port(默认 8766;区别于 textual-serve 8765)",
    "cli.help.console.no_browser": "不自动开浏览器(headless / smoke 用)",
    "cli.help.console.no_llm": "跳过 LLM 注入(只读视图 + chat_history 仍可用)",
    "cli.help.lang": "UI 语言:en(默认)或 zh",
    # karvyloop export(打包带走)
    "cli.export.help": "把你的实例(~/.karvyloop)打成一个可携带压缩包 —— 密钥除外",
    "cli.export.done": "已导出你的实例:{n} 个文件({size})-> {path}",
    "cli.export.excluded": "刻意排除:config.yaml(你的 API 密钥留在原地)、console.runtime.json、*.lock",
    "cli.export.restore": "恢复:在新机器解压到 ~/.karvyloop,补上密钥,然后 karvyloop console",
    # karvyloop import(export 的回程:一键迁移)
    "cli.import.help": "把导出的实例包恢复到 ~/.karvyloop —— `karvyloop export` 的回程",
    "cli.import.help.archive": "`karvyloop export` 产出的包(.zip 或 .tar.gz)",
    "cli.import.help.force": "合并进已有实例:冲突文件逐个覆盖(本机独有文件保留)",
    "cli.import.help.dry_run": "只列出会恢复什么,不写盘",
    "cli.import.not_found": "找不到包:{path}",
    "cli.import.unreadable": "读不了这个包(不是 zip/tar.gz,或已截断/损坏):{path} —— 没有写入任何东西",
    "cli.import.unsafe": "拒收这个包:不安全成员 '{name}'(绝对路径、'..' 或链接)—— 没有写入任何东西",
    "cli.import.nothing": "包里没有可恢复的实例数据:{path}",
    "cli.import.refuse": "{root} 已有实例数据 —— 不加 --force 不合并。",
    "cli.import.refuse.collisions": "会被覆盖的顶层项:{items}",
    "cli.import.refuse.no_collisions": "没有文件冲突 —— 加 --force 合并不会覆盖任何东西。",
    "cli.import.refuse.hint": "用 --dry-run 看完整清单,或 --force 合并(逐文件覆盖;本机独有文件保留)。",
    "cli.import.dry_run.header": "干跑 —— 零写盘。将恢复 {n} 个文件到 {root}:",
    "cli.import.skipped": "刻意跳过:{items}(秘密/锁永不落地;MANIFEST.txt 是包自己的说明书)",
    "cli.import.done": "已恢复你的实例:{n} 个文件 -> {root}",
    "cli.import.overwrote": "覆盖了 {n} 个已有文件(--force)",
    "cli.import.config_kept": "本机 config.yaml 一字未动 —— API 密钥按机器各留各的。",
    "cli.import.next": "下一步:补上模型 API key(`karvyloop init`,或编辑 ~/.karvyloop/config.yaml),然后 `karvyloop console` —— 你的技能、知识和历史都在原位。",
    # channels.webhook(出站推送通知正文;用户在 ntfy/Bark/Slack 等承接端看到)
    "channels.webhook.title": "[KarvyLoop] {n} 张决策卡待处理",
    "channels.webhook.aging": "⏳挂了{days}天 ·",
    "channels.webhook.high_risk": "⚠ 高危 —— 请回控制台确认",
    "channels.webhook.more": "…还有 {n} 张",
    "channels.webhook.open": "回控制台拍板:{url}",
    "channels.webhook.reply_code": "↩ 回批码:{code}",
    "channels.webhook.reply_hint": "回复「ACCEPT <码>」(或 REJECT / DEFER)即拍板 —— 码单次有效、限时;高危卡请回控制台。",
    # residents(原住民引荐入住,docs/60;卡文案在出卡时按当前 locale 定稿)
    "residents.referral.summary": "🏠 你的 Karvy 世界还空着 —— 认识一下第一位原住民:{names}。让 TA 入住吗?",
    "residents.referral.basis_footer": (
        "ACCEPT = 真的建出这个角色:TA 的身份、性情、验证门、协作契约全是明文文件,"
        "你随时能打开看、照着改 —— 这就是「怎么约束一个 agent」的活教材。"
        "目录权限是记在能力台账上的硬白名单(随时可见、可撤);删除任何东西都必须你亲手确认,"
        "且先备份。REJECT = 以后绝不再提;DEFER = 卡先挂着。"
    ),
    "residents.referral.accepted": (
        "{names} 已入住。目录权限已授(能力总览里随时可撤):{dirs}。"
        "TA 动任何文件都先给你看预览 —— 没你点头,什么都不会被删。"
    ),
    "residents.referral.no_registry": "角色库未接线 —— 没法安排入住。",
    "residents.referral.none_found": "这个安装包里找不到原住民镜像(打包问题?)—— 没有建任何东西。",
    "residents.referral.failed": "「{name}」入住失败:{error}",
    # butler first lesson(文件管家第一课:方案预览卡 + 兑现回执;卡文案出卡时按 locale 定稿)
    "butler.lesson.summary": "📁 文件管家的第一单 —— {dirs} 的整理方案:{n} 项移动,预览在下面。你拍板之前,一个文件都不会动。",
    "butler.lesson.basis_scan": "只读盘点:{dirs} 里清点了 {n} 个文件(只读元数据,没碰任何东西)。",
    "butler.lesson.mode_by_type": "分桶方式:按类型(图片/文档/安装包…)。",
    "butler.lesson.mode_by_time": "分桶方式:按时间(年-月文件夹)。",
    "butler.lesson.mode_from_intake": "—— 按你入门问答里选的整理习惯。",
    "butler.lesson.basis_dups": "查重发现:{n} 组内容完全相同的文件(hash 核实过)。只报告 —— 第一课绝不删除,要不要清重复得你另行拍板。",
    "butler.lesson.basis_hogs": "占位大户:{top}。只报告,不挪动。",
    "butler.lesson.basis_truncated": "注:有文件夹超过 {cap} 项 —— 本方案只覆盖前 {cap} 项。",
    "butler.lesson.basis_safety": (
        "ACCEPT = 严格按这份方案执行:只移动、只在这些文件夹之内,绝不删除、绝不覆盖"
        "(目标已存在就跳过并如实报),每一步都记台账、可撤回。"
        "REJECT = 只看看不动 —— 什么都不会发生,这也是完全合法的选择。"
    ),
    "butler.lesson.receipt": (
        "完成:{moved} 个文件已归位,{skipped} 个跳过(目标已存在/中途消失/越界 —— 如实列账,绝不硬来)。"
        "没有删除任何文件;每一步移动都记在 butler_moves.json 台账里,随时可以撤回。"
    ),
    "butler.lesson.receipt_none": "没什么需要动的 —— 方案已经满足了。",
    "butler.lesson.bad_plan": "这张卡上的方案读不出来 —— 拒绝碰任何文件(重扫一次拿新方案)。",
    # 管理面 CLI(名词-动词,覆盖既有后端:角色/域/记忆/技能/定时/token)
    "cli.help.role": "管理角色(agent 镜像):list / show",
    "cli.help.role.list": "列出本实例所有角色",
    "cli.help.role.show": "看一个角色的身份 + 配方(原子/技能)",
    "cli.help.role.id": "角色 id(目录名)",
    "cli.help.domain": "管理业务域:list / show",
    "cli.help.domain.list": "列出所有业务域",
    "cli.help.domain.show": "看一个业务域(value.md、成员查询、生命周期)",
    "cli.help.domain.id": "业务域 id",
    "cli.help.memory": "你的知识库:recall 召回 / add 沉淀",
    "cli.help.memory.recall": "按 query 召回 Belief(grep/词面重叠,无向量)",
    "cli.help.memory.recall.query": "召回的查询文本",
    "cli.help.memory.add": "往个人知识库加一条 Belief",
    "cli.help.memory.add.belief": "要记住的这条内容",
    "cli.help.memory.scope": "scope:personal(默认)或 domain",
    "cli.help.memory.limit": "最多返回几条(默认 8)",
    "cli.help.skill": "你结晶的技能:list",
    "cli.help.skill.list": "列出所有已结晶 + 系统技能",
    "cli.help.schedule": "定时任务(只 Karvy 能起):list",
    "cli.help.schedule.list": "列出所有定时任务",
    "cli.help.token": "token 用量账本:report",
    "cli.help.token.report": "按 source / model / day 聚合报 token 用量",
    "cli.help.token.by": "聚合维度:source(默认)、model 或 day",
    "cli.help.json": "输出机器可读 JSON(不打表格)",
    "cli.help.yes": "非交互确认(在非 TTY 里 create/mutate 必带)",
    "cli.manage.no_instance": "{path} 没有实例 —— 先跑 `karvyloop init`,或传 --config。",
    "cli.manage.role_none": "还没有角色。",
    "cli.manage.role_not_found": "找不到角色:{id}",
    "cli.manage.domain_none": "还没有业务域。",
    "cli.manage.domain_not_found": "找不到业务域:{id}",
    "cli.manage.memory_none": "没有匹配的 Belief。",
    "cli.manage.memory_added": "已记住:{belief}",
    "cli.manage.memory_add_failed": "已写入内存,但落盘失败:{error}",
    "cli.manage.memory_unavailable": "记忆库不可用 —— 这是个合法实例吗?",
    "cli.manage.skill_none": "还没有结晶技能。",
    "cli.manage.schedule_none": "没有定时任务。",
    "cli.manage.token_none": "还没有记录 token 用量。",
    "cli.manage.needs_yes": "这会创建/改动数据。在非终端环境请加 --yes 确认。",
    "cli.manage.confirm_add": "把这条 Belief 加进知识库?",
    "cli.manage.aborted": "已取消。",
    # ---- create/mutate 子命令(list/show 之外的动词)----
    "cli.help.role.create": "新建一个角色(agent 镜像)",
    "cli.help.role.create.id": "角色 id(目录名;字母/数字/_/-)",
    "cli.help.role.create.identity": "身份文本(这个角色是谁)",
    "cli.help.role.create.soul": "灵魂文本(SOUL.md)",
    "cli.help.role.create.nickname": "进域时的显示名(如「张三」)",
    "cli.help.role.create.model": "角色级模型引用(空=层叠到默认)",
    "cli.help.role.rm": "删除一个角色(破坏性 —— 删掉它的镜像目录)",
    "cli.help.domain.create": "新建业务域(带 --parent 则建子域)",
    "cli.help.domain.create.name": "业务域名称",
    "cli.help.domain.create.parent": "父业务域 id(子域继承 value.md + deontic)",
    "cli.help.domain.archive": "归档业务域(软删除 —— 之后只读)",
    "cli.help.schedule.add": "用自然语言加一条定时任务(NL→cron)",
    "cli.help.schedule.add.text": "自然语言描述(如「每天早上8点汇总进展」)",
    "cli.help.schedule.rm": "删除一条定时任务",
    "cli.help.schedule.toggle": "启用/停用一条定时任务",
    "cli.help.schedule.on": "启用该任务",
    "cli.help.schedule.off": "停用该任务",
    "cli.help.schedule.id": "定时任务 id",
    "cli.help.skill.import": "导入第三方技能(Agent-Skills 开放标准;沙箱执行)",
    "cli.help.skill.import.source": "github 来源 / .zip|.skill 的 url 或路径 / 本地文件夹",
    "cli.help.skill.import.overwrite": "覆盖同名的已有技能",
    "cli.manage.role_id_required": "必须给角色 id(--id)。",
    "cli.manage.role_create_failed": "无法创建角色:{error}",
    "cli.manage.role_created": "已创建角色:{id}",
    "cli.manage.role_removed": "已删除角色:{id}",
    "cli.manage.confirm_role_rm": "删除角色「{id}」及其镜像目录?此操作不可撤销。",
    "cli.manage.domain_name_required": "必须给业务域名称(--name)。",
    "cli.manage.domain_create_failed": "无法创建业务域:{error}",
    "cli.manage.domain_created": "已创建业务域:{name}({id})",
    "cli.manage.domain_archived": "已归档业务域:{id}",
    "cli.manage.confirm_domain_create": "创建业务域「{name}」?",
    "cli.manage.confirm_domain_archive": "归档业务域「{id}」(之后只读)?",
    "cli.manage.confirm_role_create": "创建角色「{id}」?",
    "cli.manage.schedule_text_required": "必须给自然语言描述。",
    "cli.manage.schedule_no_llm": "没配模型 —— NL→cron 需要 LLM。先跑 `karvyloop init`,或传 --config。",
    "cli.manage.schedule_not_understood": "没听懂明确的时间 —— 换种说法(如「每天早上8点……」)。",
    "cli.manage.schedule_parsed": "解析出:cron={cron}  intent={intent}",
    "cli.manage.confirm_schedule_add": "加这条定时任务?",
    "cli.manage.schedule_added": "已定时:{id}  [{cron}]  {title}",
    "cli.manage.schedule_removed": "已删除定时任务:{id}",
    "cli.manage.confirm_schedule_rm": "删除定时任务「{id}」?",
    "cli.manage.schedule_not_found": "找不到定时任务:{id}",
    "cli.manage.schedule_toggled": "定时任务 {id} 现在是 {state}。",
    "cli.manage.state_on": "启用",
    "cli.manage.state_off": "停用",
    "cli.manage.confirm_skill_import": "从「{source}」导入技能(第三方,不可信)?",
    "cli.manage.skill_import_failed": "技能导入失败:{error}",
    "cli.manage.skill_imported": "已导入技能:{name}({files} 个文件){scripts}",
    "cli.manage.skill_scripts_note": " —— 含 scripts/(执行走沙箱)",
    # mesh 任务板:跨设备接活 H2A 卡(docs/74 §6.2/§6.3)
    "mesh.takeover.summary": "你的「{device}」设备上的任务中断了:{intent} —— 要在这台接着跑吗?",
    "mesh.takeover.basis": (
        "任务 {task_id} 最后由你的「{device}」设备认领,但 lease 到期没有心跳续租 —— "
        "判定它中途离线(来源设备:{source})。ACCEPT = 在本机从头重跑,并在你的共享任务板上"
        "记下这次接管(其它设备不再重复提醒)。REJECT / 不拍 = 什么都不发生;之后你的任一设备仍可接。"
    ),
    "mesh.takeover.receipt": "{detail}(已在你的共享任务板记下这次接管)",
    # ---- 提案工厂 summary/basis(zh 保持既有原文,行为零回归)----
    # confirm_decision_pref(decision_wire)
    "proposal.confirm_pref.kind_constraint": "约束",
    "proposal.confirm_pref.kind_taste": "品味",
    "proposal.confirm_pref.kind_standing": "站位",
    "proposal.confirm_pref.kind_default": "偏好",
    "proposal.confirm_pref.summary": "记成你的默认偏好吗?[{label}] {content}",
    "proposal.confirm_pref.basis": "我从你的拍板里注意到这条;记下来后,我提案会提前按它对齐,你少拒、少重复解释自己。",
    # run_task resume(proactive)
    "proposal.run_task.summary": "上次「{intent}」没跑完(出错/中断)—— 要我重试吗?",
    "proposal.run_task.basis": (
        "「{who}」执行的任务「{intent}」状态 = error(出错/中断),没跑完。"
        "原因/最后输出:{err}。重试 = 用同样的意图再跑一遍。"
    ),
    "proposal.run_task.default_error": "出错/中断",
    "proposal.run_task.default_who": "小卡",
    # silence(挣来的静音:授权/续期/吊销)
    "proposal.silence.domain_suffix": "(域「{d}」)",
    "proposal.silence_grant.summary": (
        "「{kind}」{dom}这类板,我最近 {n} 次押中 {hits} 次"
        "(95% 置信下界 {lb}%)—— 要不要以后这类替你静音处理?"
    ),
    "proposal.silence_grant.basis": (
        "这不是要更多权限 —— 是同类卡上我 {n} 次押中 {hits} 次的成绩单,按 95% 置信"
        "下界算也 ≥{min_lb}%(不是碰巧连中,批/拒两向各自过线),其中我押"
        "你会拒 {reject_pred} 次、押对 {reject_correct} 次"
        "(证明我能替你挡坏的,不只会点头)。"
        "ACCEPT 后 30 天内:这类卡我**只**替你办「我押你会 ACCEPT 且把握 ≥"
        "{min_conf}%」的;押 REJECT 或没把握的照旧问你;"
        "我还会不定期抽一部分照常出卡对答案(哪张是抽查不告诉你);删除/外发/付款/"
        "上线这类不可逆的永远问你。每次静音处理完整留痕(运行记录+台账)、满 30 天"
        "要你亲手续期;我**押错一次立即自动收回**授权,你也随时可撤。"
        "REJECT=保持现状,每张都问你。"
    ),
    "proposal.silence_renew.audit_some": "抽查对账 {audit_n} 次中 {audit_hits} 次",
    "proposal.silence_renew.audit_none": "本期没攒到抽查对账样本",
    "proposal.silence_renew.mark_overturned": "⚠翻案 ",
    "proposal.silence_renew.mark_failed": "✗失败 ",
    "proposal.silence_renew.review_item": "{mark}「{gist}」",
    "proposal.silence_renew.review_disp": "。本期风险最高的 {n} 条(翻案/失败/最贵优先):{gists}",
    "proposal.silence_renew.summary": (
        "「{kind}」{dom}的静音授权满 30 天到期 —— 上月替你静音 {silenced_n} 次,"
        "{audit};要续 30 天吗?"
    ),
    "proposal.silence_renew.basis_head": (
        "静音授权只有 30 天,到期必须你亲手续 —— 没人看的对账不算数,不点就停"
        "(这类卡已恢复逐张问你)。本期账:静音 {silenced_n} 次、{audit}"
    ),
    "proposal.silence_renew.basis_oldest": "、最老一条留痕 {pid}",
    "proposal.silence_renew.basis_tail": (
        ";每条都在台账/运行记录里可查,逐条看完再决定。ACCEPT=续 30 天(规则不变:"
        "只办押你会 ACCEPT 且把握 ≥{min_conf}% 的,"
        "继续不定期抽查,押错一次立即收回);REJECT=不续,每张都问你。"
    ),
    "proposal.silence_revoked.summary": "已自动收回「{kind}」{dom}的静音授权 —— 这类卡恢复逐张问你",
    "proposal.silence_revoked.reason_default": "我押错了一次你的拍板",
    "proposal.silence_revoked.basis": (
        "{reason}。挣来的静音只在命中率兑现时有效 —— "
        "押错一次立即收回(保守边界);要重新拿授权,得吊销之后重新攒新鲜对账"
        "(95% 置信下界 ≥{min_lb}%,至少 {min_n} 次)我才会再问你。ACCEPT=知悉。"
    ),
    # cocreate_finalize(cocreation)
    "proposal.cocreate.summary_template": "共创定稿:一键开出模板域「{name}」",
    "proposal.cocreate.basis_template": (
        "共创会话里你选定了现成模板「{name}」。ACCEPT = 走既有 instantiate 路径"
        "真开出该域和配好灵魂的角色(幂等:同名活跃域已存在会被拒并如实说)。"
    ),
    "proposal.cocreate.summary_custom": "共创定稿:建业务域「{name}」+ {n} 个角色",
    "proposal.cocreate.basis_custom": (
        "这是共创会话的最终草案(S1/S2 期间没写过任何东西 —— 零副作用)。"
        "ACCEPT 才真建:角色走 RoleRegistry.create(尽责契约 COMMITMENT 统一 seed,"
        "与系统默认/导入同一份),域落 value.md + deontic 真护栏。"
        "卡上任何字段不对,可直接改了再批。"
    ),
    # weekly_digest
    "proposal.weekly_digest.gist_quiet": "这周很安静(无任务/无消耗)",
    "proposal.weekly_digest.gist": "跑了 {runs} 次任务(成 {ok}/败 {fail}),烧了 {tokens} tokens",
    "proposal.weekly_digest.summary": "周报 {start}→{end}:{gist}",
    "proposal.weekly_digest.basis": (
        "数字全部从 Trace / tokens.db / 决策流水确定性汇总,零 LLM、可回链"
        "(每条带 trace_ref/id)。ACCEPT 仅表示已读,不触发任何执行。"
    ),
    # archive_stale(knowledge_tick)
    "proposal.archive_stale.summary": "🗄️ {n} 条知识一年没用了,归档?",
    "proposal.archive_stale.basis": (
        "这 {n} 条知识超过一年没被召回过(也没更新),疑似过时:{shown}。"
        "ACCEPT = 打失效标记归档(**失效不删**:仍留库可审计、可翻案,只是不再进召回);"
        "REJECT = 留着继续参与召回。"
    ),
    # promote_experience(promotion_tick)
    "proposal.promote_exp.line": "原(域内):{before}\n升(通用):{content}",
    "proposal.promote_exp.line_why": "\n  ↳ 为什么泛化:{why}",
    "proposal.promote_exp.summary": "📜 「{role}」有 {n} 条域内经验可升为通用兵法,升吗?",
    "proposal.promote_exp.basis": (
        "「{role}」在域「{domain}」的这些经验通过了泛化判定与脱敏改写。"
        "ACCEPT = 升为该角色的通用兵法(跨域可用;将来对外可见面也只有这一层);"
        "REJECT = 这轮不升,域内照用(这批经验没有新变化就不再重提)。升层后删域不再自动撤——"
        "要撤在记忆面板单条失效。\n\n{lines}"
    ),
    # route_to_role
    "proposal.route.summary": "把「{requirement}」转给业务域「{domain_name}」的「{role}」",
    "proposal.route.basis": (
        "这件事属于业务域「{domain_name}」的职责;我不越界自己做,"
        "而是委派给「{role}」在该域 value.md 治理下执行。你 ACCEPT 才真正转过去。"
    ),
    # roundtable
    "proposal.roundtable.who_default": "群里的角色",
    "proposal.roundtable.summary": "在「{group}」开圆桌,叫上 {who} 讨论「{topic}」",
    "proposal.roundtable.basis": (
        "你想让多个角色一起讨论,这是**圆桌**(几个人坐一起),不是把活交给一个人(委派)。"
        "我会在群「{group}」拉上 {who},先和你对齐目标再开始讨论。你 ACCEPT 才真正开桌。"
    ),
    # ops_fix
    "proposal.ops_fix.fallback_summary": "运维诊断",
    "proposal.ops_fix.cause": "可能原因:{cause}",
    "proposal.ops_fix.fix": "建议修法:{fix}",
    "proposal.ops_fix.auto": "ACCEPT 将执行**确定性可逆修复**(先备份再重置,可从 .corrupt.bak 找回),不调模型改系统。",
    "proposal.ops_fix.manual": "这是 LLM 诊断、**未经验证**;ACCEPT 只表示你认可,系统**不会自动改**——请按上面步骤手动处理。",
    # merge_atoms
    "proposal.merge_atoms.head": "把 {n} 个近义原子合并成规范原子「{canon}」:{members}。",
    "proposal.merge_atoms.reason": "判断依据:{reason}",
    "proposal.merge_atoms.why": "合并 = 减少重复、提升复用(护城河:批量导入的原子常因近义不并而 reuse 偏低)。",
    "proposal.merge_atoms.accept": (
        "ACCEPT 会 **rewire-before-delete**:先把所有引用这些原子的角色改写到规范原子,"
        "再删冗余,**绝不留悬空引用**;不动也安全(只是不并)。"
    ),
    "proposal.merge_atoms.summary": "合并 {n} 个近义原子 → 「{canon}」",
    # fs_access
    "proposal.fs_access.op_read": "读取",
    "proposal.fs_access.op_write": "写入",
    "proposal.fs_access.op_read_write": "读写",
    "proposal.fs_access.who_role": "角色「{role}」",
    "proposal.fs_access.who_default": "执行中的角色",
    "proposal.fs_access.summary": "{who}请求{op}工作区外路径:{path}",
    "proposal.fs_access.basis": (
        "它在干活时需要碰这个路径,但该路径在你的工作区之外 —— 按最小权限原则默认关闭。"
        "ACCEPT=永久放行该路径(能力总览随时可撤);密钥/凭据类路径永远不会出现在这里(硬地板)。"
    ),
    # merge_knowledge
    "proposal.merge_knowledge.head": "这 {n} 条知识点讲的基本是同一件事:{shown}。",
    "proposal.merge_knowledge.reason": "判断依据:{reason}",
    "proposal.merge_knowledge.accept": (
        "建议合并成一条「{label}」。ACCEPT = 先写入合并条、再删被并旧条(中途失败不丢数据);"
        "不动也安全(只是库里留着近重复)。"
    ),
    "proposal.merge_knowledge.summary": "🧹 合并 {n} 条近重复知识 → 「{label}」",
    # confirm_result
    "proposal.confirm_result.default_role": "角色",
    "proposal.confirm_result.default_req": "这个任务",
    "proposal.confirm_result.basis": (
        "「{role}」为完成「{req}」临时造了 {n} 个新能力:{lines}。"
        "你认可这次结果 → 由 {role} 综合裁哪些值得留进自己的工具箱(被别的角色复用才正式转正);"
        "不处理 / 不认可 → 它们留作试用,长期没人用会被自动清掉。"
    ),
    "proposal.confirm_result.summary": "「{role}」做完「{req}」,新造了 {n} 个能力 —— 认可结果就留有用的?",
    # infeasible_report
    "proposal.infeasible.default_goal": "(未命名目标)",
    "proposal.infeasible.default_role": "角色",
    "proposal.infeasible.attempt_unfinished": "未完成",
    "proposal.infeasible.attempt_line": "第 {i} 次:{term}",
    "proposal.infeasible.attempt_note": "（{note}）",
    "proposal.infeasible.no_trail": "（无轨迹）",
    "proposal.infeasible.basis": (
        "「{role}」为完成「{goal}」自助重规划了 {n} 次仍没成。轨迹:{trail}。"
        "系统靠自动重规划突破不了 —— 这是带证据的结论,不是问你「怎么办」:"
        "请你定夺(接纳并放下 / 暂缓 / 我来调整目标或补资源再试)。"
    ),
    "proposal.infeasible.summary": "「{role}」追求「{goal}」未达成(自助重规划 {n} 次)",
    # inbox(邮件管道)
    "proposal.inbox.no_body": "(无正文)",
    "proposal.inbox_decision.default_reason": "分诊判定需要你拍板",
    "proposal.inbox_decision.default_action": "(见邮件)",
    "proposal.inbox_decision.summary": "📧 需要拍板:{sender} 「{subject}」",
    "proposal.inbox_decision.basis": (
        "这封邮件被分诊为**需要你拍板**({reason})。建议动作:{action}。"
        "正文摘要:{snippet}。"
        "本管道只通知与建议 —— 未经你确认绝不对外发信;ACCEPT 也只是记录你的决定,"
        "不会自动回信或执行任何外部动作。"
    ),
    "proposal.inbox_reply.default_reason": "分诊判定可以先代拟回复",
    "proposal.inbox_reply.summary": "✉️ 代拟回复待批:{sender} 「{subject}」",
    "proposal.inbox_reply.basis": (
        "这封邮件被分诊为**需要回复**({reason}),已代拟草稿(可就地修改后再批)。"
        "ACCEPT = 把草稿存进台账并显示给你,由你**自行复制发送** —— "
        "系统不代发任何邮件(未经确认绝不对外发信是硬规矩)。"
    ),
    # revise_skill(crystallize/revision)
    "proposal.revise_skill.summary": "技能「{skill}」近几次客观信号差,建议大幅修订方法(重写/删步骤过半,需你过目)",
    "proposal.revise_skill.basis": (
        "触发依据:{trigger};失败样本 traces: {traces}。"
        "改动幅度过大(方法重写/删步骤过半),按问责链升 H2A,不静默换方法。"
    ),
    "proposal.revise_skill.traces_rolled": "(原文已滚动)",
    "proposal.revise_skill.trigger": "confidence={conf}(<{thresh}触发) bad={n_bad}/{total}(≥{min_bad}触发)",
    # external_adopt(external_collab)
    "proposal.external_adopt.default_citizen": "外部同事",
    "proposal.external_adopt.summary": "采纳 {badge} 「{cid}」的产出?(外部执行体·不可信数据)",
    "proposal.external_adopt.basis_head": "这是外部执行体「{badge} {cid}」的产出——**不可信数据**(它不担你的责、无问责链)。",
    "proposal.external_adopt.basis_ctx": "背景:{ctx}",
    "proposal.external_adopt.basis_tail": (
        "ACCEPT = 你拍板采纳这份产出,它才穿过来源边界(可进记忆/当结论/交给下游角色);"
        "REJECT/不处理 = 只当参考,永不自动进记忆、不触发别人。原始产出:\n{preview}"
    ),
    "proposal.external_adopt.empty": "(空)",
    # spend_budget 提醒卡(llm/spend_budget + console/entry 兜底)
    "proposal.spend.period_month": "本月",
    "proposal.spend.period_day": "今天",
    "proposal.spend.summary_blocked": (
        "预算已用满:{period}已花 {used} / 上限 {limit}（{pct}%）"
        "—— 后台自动任务已暂停,前台照常。要继续请提高上限或改 on_limit。"
    ),
    "proposal.spend.summary_warn": "花费提醒:{period}已花 {used} / 上限 {limit}（{pct}%,达 {tier}%）",
    "proposal.spend.fallback_summary": "花费提醒",
    # resolve_conflict(domain/skill_conflict → proposal_from_conflict)
    "conflict.rule_forbid": "禁止项",
    "conflict.rule_oblige": "强制项",
    "conflict.rule_value": "价值观",
    "conflict.rule_generic": "规则",
    "conflict.summary": "技能「{skill}」可能违反域「{domain}」的{label}「{rule}」({role})",
    "conflict.judge_reason": "技能用途文本命中{label}关键词,疑似冲突,请确认",
    # crystallize_skill ACCEPT 回执(proposal_handlers)
    "receipt.crystallize.default_habit": "这个习惯",
    "receipt.crystallize.accepted": "已采纳「{summary}」— 你继续这样用,系统会自动把它结晶成技能",
}

TABLES = {"en": _EN, "zh": _ZH}

__all__ = ["TABLES"]
