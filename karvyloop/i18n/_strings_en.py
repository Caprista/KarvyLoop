"""_strings_en — 英文表(从 _strings.py 机械拆出,god-module 行数红线;键序/内容零改动)。

新增键改这里 + _strings_zh.py 同步(parity 测试锁 en/zh 键集合相等)。"""
EN = {
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
    "cli.url.not_running": (
        "The console is NOT running right now (port {port} refused — only a stale record from a "
        "previous run was found, so any link would not open). Start it with `karvyloop console`, "
        "then run `karvyloop url` again."
    ),
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
    "wizard.next_export": "note: config.yaml has a ${{{env_var}}} placeholder — it will NOT work until you set that env var (export {env_var}=..., then karvyloop run \"...\")",
    "wizard.custom_desc": "custom OpenAI-compatible endpoint (your own base_url + model id: vLLM / Ark / self-hosted gateway)",
    "wizard.custom_base_prompt": "Base URL of the OpenAI-compatible endpoint (e.g. https://host/v1): ",
    "wizard.custom_base_bad": "base URL must start with http:// or https://",
    "wizard.custom_model_prompt": "Model id as the endpoint expects it (e.g. gpt-4o / an Ark endpoint id): ",
    "wizard.custom_model_bad": "model id can't be empty",
    "wizard.custom_key_prompt": "API key (leave empty only for keyless/local endpoints): ",
    # 模型配置(gateway/console 共用;fail-loud 出人话)
    "models.api_unimplemented_choice": (
        "API dialect '{api}' isn't implemented in this build — the model would fail on every chat. "
        "For OpenAI-compatible endpoints (vLLM / Ollama-OpenAI / Ark / most gateways) pick "
        "'openai-completions'; for Anthropic-compatible endpoints pick 'anthropic-messages'."
    ),
    "models.kimi_coding_key_hint": (
        "This key (sk-kimi-…) is a Kimi For Coding key: it only works on the coding endpoint "
        "https://api.kimi.com/coding/v1 (User-Agent allowlist-gated; it may 403 until this client "
        "is allowlisted) and will NOT work on Moonshot chat endpoints. For chat, get a key from "
        "platform.moonshot.ai (Global) or platform.moonshot.cn (CN); or pick the 'Kimi For Coding' "
        "preset to use this key on its own endpoint."
    ),
    "gateway.api_unimplemented": (
        "API dialect '{api}' isn't implemented in this build. Edit the model (console 🤖 Models, or "
        "config.yaml): OpenAI-compatible endpoint → api 'openai-completions'; Anthropic-compatible "
        "endpoint → api 'anthropic-messages'."
    ),
    "gateway.api_embed_unimplemented": "embedding for API dialect '{api}' isn't implemented in this build.",
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
    # mesh 任务板:Pursuit 跨设备接管(docs/88 第三刀 #3)
    "mesh.takeover.pursuit_summary": (
        "Your “{device}” device was chasing a goal and went quiet: {statement} "
        "— {advances} rounds in{gate}. Continue it on this device?"
    ),
    "mesh.takeover.pursuit_gate_suffix": "; done when {gate_desc}",
    "mesh.takeover.pursuit_basis": (
        "Long-horizon goal {pursuit_id} was running on your “{device}” device ({source}); its lease "
        "expired without a heartbeat, so it looks interrupted. ACCEPT continues from the saved "
        "progress (round {advances} — the checkpoint synced between your devices), not from zero."
    ),
    "mesh.takeover.pursuit_receipt": (
        "Took over goal “{statement}” — continuing from round {advances} with saved progress."
    ),
    "mesh.takeover.pursuit_claim_lost": (
        "Another of your devices ({device}) already picked this goal up — standing down here."
    ),
    "mesh.takeover.pursuit_no_store": (
        "This device has no pursuit store wired, so it cannot take the goal over."
    ),
    "mesh.takeover.pursuit_bad_checkpoint": (
        "The saved progress from your other device could not be read ({error}) — not taking over."
    ),
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
        "time(s) (proof I can block the bad ones for you, not just nod along). After ACCEPT, for 30 days: I only "
        "handle cards where I predict you'd ACCEPT with ≥{min_conf}% confidence; predicted-REJECT or low-confidence "
        "cards still come to you; I'll also randomly let some through as ordinary cards to check my answers (you "
        "won't be told which are spot checks); irreversible things — deletes, outbound sends, payments, going live — "
        "always come to you. Every silent action leaves a full trail (run record + ledger), and after 30 days renewal "
        "must be your own hand; one wrong call and the grant is revoked automatically, immediately — and you can "
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
    # weekly-digest (card body markdown skeleton — 骨架走 i18n,动态数据/gist/回链是数据不翻)
    "weekly.md.title": "Weekly digest",
    "weekly.md.quiet": "A quiet week: nothing ran, no tokens burned, nothing crystallized/revised, and nothing waiting on your call.",
    "weekly.md.h_tasks": "Tasks",
    "weekly.md.h_token": "Token",
    "weekly.md.h_skills": "Skills",
    "weekly.md.h_decisions": "Your calls",
    "weekly.md.h_pending": "Still pending",
    "weekly.md.h_summary": "In a sentence",
    "weekly.md.tasks_line": "- Ran **{runs}**: {ok} ok / {fail} failed (success rate {rate})",
    "weekly.md.recall_line": "- Fast-brain / recall hit rate {rate} (stable replays {replays} + skill-guided reruns {reruns})",
    "weekly.md.failures_head": "- Failures (latest {n}{more}):",
    "weekly.md.failures_more": ", {m} more not shown",
    "weekly.md.token_unwired": "- Ledger not wired (no data, no guessing)",
    "weekly.md.token_line": "- **{total}** tokens over {calls} call(s) (in {input} / out {output})",
    "weekly.md.token_source": "  - {source}: {total} ({calls} call(s))",
    "weekly.md.skills_new": "- **{n}** newly crystallized",
    "weekly.md.crystallized_item": "  - {name} (sig {sig}, ref {ref})",
    "weekly.md.revisions_line": "- Revisions: {landed} landed / {proposed} awaiting your call",
    "weekly.md.revision_item": "  - {name} ({mode}, ref {ref})",
    "weekly.md.decisions_unwired": "- Decision ledger not wired (no data, no guessing)",
    "weekly.md.taste_line": "- Taste hit rate (rolling): **{rate}**{arrow} (n={n})",
    "weekly.md.taste_insufficient": "- Taste hit rate: not enough samples (n={n} < {min}), percentage withheld",
    "weekly.md.taste_unwired": "- Taste hit rate: taste betting not wired",
    "weekly.md.pending_line": "- **{count}** card(s) awaiting your call{age}",
    "weekly.md.pending_age": ", oldest pending {days} day(s)",
    "weekly.md.pending_none": "- No cards pending",
    # archive_stale(knowledge_tick)
    "proposal.archive_stale.summary": "🗄️ {n} knowledge item(s) unused for a year — archive them?",
    "proposal.archive_stale.basis": (
        "These {n} knowledge items haven't been recalled (or updated) in over a year — likely stale: {shown}. "
        "ACCEPT = mark them invalid and archive (invalidate, not delete: they stay in the library, auditable "
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
        "You want several roles to discuss this together — that's a roundtable (people around a table), not "
        "handing the job to one person (delegation). I'll gather {who} in group “{group}”, align the goal with you "
        "first, then start the discussion. The table only opens once you ACCEPT."
    ),
    # roundtable_conclusion(高风险圆桌结论落认知库确认卡)
    "proposal.roundtable_conclusion.summary": (
        "Roundtable “{topic}” reached a conclusion — record it into your cognition base?"
    ),
    "proposal.roundtable_conclusion.risk_shared": (
        "it would enter your shared cognition layer (recalled in every future decision)"
    ),
    "proposal.roundtable_conclusion.risk_dissent": (
        "it closed with unresolved dissent (minority report attached)"
    ),
    "proposal.roundtable_conclusion.risk_no_consensus": (
        "the table hit its round cap without reaching consensus"
    ),
    "proposal.roundtable_conclusion.risk_default": "it is marked high impact",
    "proposal.roundtable_conclusion.basis": (
        "High-stakes roundtable conclusion: {risk}. "
        "ACCEPT = record it into cognition (dissents stay on the record); "
        "REJECT = keep it only in the discussion thread, nothing is persisted."
    ),
    "receipt.roundtable_conclusion.ok": "Recorded the roundtable conclusion into your cognition base.",
    "receipt.roundtable_conclusion.ok_dissent": (
        "Recorded the roundtable conclusion into your cognition base "
        "({n} dissent(s) kept on the record)."
    ),
    "receipt.roundtable_conclusion.empty": "This conclusion is empty — nothing to record.",
    "receipt.roundtable_conclusion.no_memory": "Cognition base isn't wired — cannot record the conclusion.",
    "receipt.roundtable_conclusion.write_failed": "Couldn't record the conclusion: {error}",
    # ops_fix
    "proposal.ops_fix.fallback_summary": "Ops diagnosis",
    "proposal.ops_fix.cause": "Likely cause: {cause}",
    "proposal.ops_fix.fix": "Suggested fix: {fix}",
    "proposal.ops_fix.auto": (
        "ACCEPT runs a deterministic, reversible repair (backup first, then reset; recoverable from "
        ".corrupt.bak) — no model gets to change your system."
    ),
    "proposal.ops_fix.manual": (
        "This is an LLM diagnosis, unverified; ACCEPT only means you acknowledge it — the system will not "
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
        "ACCEPT will rewire-before-delete: first repoint every role referencing these atoms to the canonical "
        "one, then delete the redundant ones — never leaving dangling references; doing nothing is also safe "
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
        "This mail was triaged as needs your decision ({reason}). Suggested action: {action}. "
        "Body snippet: {snippet}. This pipe only notifies and suggests — nothing is ever sent without your "
        "confirmation; ACCEPT only records your decision, it does not auto-reply or trigger any external action."
    ),
    "proposal.inbox_reply.default_reason": "triage judged a reply can be drafted first",
    "proposal.inbox_reply.summary": "✉️ Draft reply awaiting your approval: {sender} “{subject}”",
    "proposal.inbox_reply.basis": (
        "This mail was triaged as needs a reply ({reason}); a draft is prepared (edit it in place before "
        "approving). ACCEPT = save the draft to the ledger and show it to you, and you copy and send it "
        "yourself — the system never sends mail on its own (nothing goes out without your confirmation, "
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
        "This is output from external executor “{badge} {cid}” — untrusted data (it doesn't carry your "
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
    # ---- agent-import 段(docs/84 #2 判型分流:api_agent_import 用户可见 note)----
    "agent_import.note.pure_executor": (
        "This agent is a pure executor (capability steps only — no stance, it doesn't carry your "
        "accountability), so no role seat was created. {n} reusable atom(s) landed in the shared atom "
        "library; any role can compose them. Want it at the decision table? Create a role yourself and "
        "bind these atoms."
    ),
    "agent_import.note.skill_like": (
        "This agent is essentially a process playbook (a skill), not a “who” — nothing was written to "
        "the role or atom library. Import it through the skill library instead."
    ),
    "agent_import.note.advisory_persona": (
        "This role has no immediately executable atoms yet (pure persona, or needs external "
        "integration). Imported as an advisory role. To make it actually do work → build or import a "
        "skill for it in the skill library (skills land as runnable code / MCP connections)."
    ),
    "agent_import.note.v0_fallback": (
        "No LLM wired, or decomposition didn't succeed → deterministic adapter (tools listed by name "
        "only; no atoms minted)."
    ),
    # ---- task-insight 段(docs/82 非任务认知沉淀:记忆面板来源列/kind 标签)----
    "memory.source.task_insight": "execution insight (auto-distilled from run traces, provisional)",
    # ---- memory.source 人话标签(D2 冲突卡「旧那条的来源」+ 记忆面板)----
    "memory.source.fed": "knowledge you fed & confirmed",
    "memory.source.user_edit": "your manual edit",
    "memory.source.cli": "added via CLI",
    "memory.source.user_explicit": "you told me directly",
    "memory.source.ingest": "material you ingested",
    "memory.source.role_experience": "a role's learned experience",
    # ---- D2 记忆冲突卡(memory_conflict:supersede 要推翻你钉住/人审的记忆 → 不自动,升卡让你裁)----
    "proposal.memory_conflict.summary": (
        "New info conflicts with something you confirmed — keep the old, adopt the new, or keep both? "
        "(old: “{old}” / new: “{new}”)"
    ),
    "proposal.memory_conflict.origin_pinned": "you pinned it ({src}, {when})",
    "proposal.memory_conflict.origin_reviewed": "{src}, {when}",
    "proposal.memory_conflict.basis": (
        "This conflicts with a memory you confirmed/pinned, so I won't overwrite it behind your back.\n"
        "• Existing: “{old}” — {origin}\n"
        "• New: “{new}”\n"
        "Decide: keep the old (drop the new), adopt the new (retire the old — still archived, reversible), "
        "or keep both (default — nothing is lost either way; nothing is invalidated unless you say so)."
    ),
    "receipt.memory_conflict.keep_both": "Kept both — nothing was invalidated.",
    "receipt.memory_conflict.adopt_new": "Adopted the new one; retired the old (still archived, reversible). Kept: “{keep}”.",
    "receipt.memory_conflict.keep_old": "Kept the old one; retired the new. Kept: “{keep}”.",
    "receipt.memory_conflict.gone": "That memory is no longer around — nothing to do.",
    "receipt.memory_conflict.already": "That one is already retired — nothing to do.",
    "receipt.memory_conflict.bad_resolution": "Unknown resolution “{r}” — nothing changed.",
    "insight.kind.env": "environment fact",
    "insight.kind.correction": "correction lesson",
    "insight.kind.observation": "incidental observation",
    # ---- schedule-catchup 段(跨天离线追赶:关机期间错过的定时任务 → 聚合补跑确认卡)----
    "proposal.schedule_catchup.summary": (
        "While you were away, “{title}” missed {n} scheduled run(s) "
        "(most recent was due {when}) — want me to run it once to catch up?"
    ),
    "proposal.schedule_catchup.basis": (
        "The system was off when “{title}” came due, so those {n} run(s) never happened. "
        "Catching up means running it once now — not replaying every missed slot. "
        "If you decline or ignore this, nothing runs, and I won't bring this batch up again."
    ),
    # ---- schedule-suggest 段(docs/90 刀3c:手动跑到第 N 次 → 温和的"要不要每周自动跑"提示)----
    "proposal.schedule_suggest.summary": (
        "You've run “{intent}” by hand {n} times — want me to run it on a schedule?"
    ),
    "proposal.schedule_suggest.basis": (
        "You did this yourself {n} times, so it might be worth automating. "
        "Accepting won't schedule anything yet — I'll bring it into the schedule setup so you can "
        "tell me how often and when (I won't guess the timing for you). "
        "Ignore it and I won't bring this one up again."
    ),
    "receipt.schedule_suggest.accepted": (
        "Brought it to the schedule setup — tell me how often and when (e.g. “every Monday 8am”) "
        "and I'll set it up. I won't pick the timing for you."
    ),
    # ---- scene 段(docs/94 刀1:场景触发换心脏 —— 当下场景卡文案 + 日预算回执)----
    "proposal.scene_schedule_due.summary": (
        "“{title}” fires in {mins} min — it failed last time. Want me to do a test run now?"
    ),
    "proposal.scene_schedule_due.basis": (
        "The scheduled task “{title}” is due within {mins} min, and its last run ended in "
        "error: {err}. A test run = run the same intent once right now, so if it fails again "
        "you'll know before the scheduled slot. The schedule itself is untouched."
    ),
    "scene.big_job.basis_prefix": (
        "You just finished “{intent}” (took about {mins} min)."
    ),
    "scene.budget.receipt": (
        "That's all my proactive suggestions for today ({n}/{n}) — if you want more, just ask."
    ),
    # ---- scene-ready 段(docs/94 刀2:预测定向预执行 —— 「已备好」卡 + ACCEPT 落地文案)----
    "proposal.scene_ready.summary": (
        "While things were quiet I already took care of it: “{what}” — want to take a look?"
    ),
    "proposal.scene_ready.basis_done": (
        "I did the prep work during idle time. A peek at what came out: {gist}"
    ),
    "proposal.scene_ready.basis_deliver": (
        "Accept = I drop the result into our chat (any files land in your workspace under "
        "karvy_prepared/). Reject or let it lapse = everything is discarded — nothing touches "
        "your knowledge, memory or skills unless you approve."
    ),
    "proposal.scene_ready.basis_rerun": (
        "Accept = rerun it once, carrying the diagnosis and fix draft I prepared. "
        "Reject or let it lapse = the draft is discarded and nothing runs."
    ),
    "receipt.scene_ready.gone": (
        "That prepared result has expired and was cleaned up — nothing was applied."
    ),
    "receipt.scene_ready.no_intent": "The original intent is missing — can't rerun it.",
    "receipt.scene_ready.delivered": (
        "Delivered what I prepared into our chat{files}."
    ),
    "receipt.scene_ready.files_moved": (
        " (and moved {n} file(s) into your workspace under karvy_prepared/{sid})"
    ),
    "receipt.scene_ready.deliver_failed": (
        "Couldn't deliver the prepared result (chat unavailable) — it stays staged and will be "
        "cleaned up automatically."
    ),
    "receipt.scene_ready.files_only": (
        "Chat was unavailable, so the text couldn't be posted{files}."
    ),
    "scene_ready.deliver.message": "Here's what I prepared ahead of time:\n\n{text}",
    "scene_ready.deliver.user_line": "✅ Accepted: {what}",
    "scene_ready.rerun.diagnosis_block": (
        "[Prepared failure diagnosis & fix draft]\n{text}"
    ),
    "scene_preexec.task_intent": "Pre-run while idle: {what}",
    "scene_preexec.product_error_diag": (
        "The test run failed again. Error: {err}\n\nWhat I saw while running it:\n{text}"
    ),
    # ---- system-import 段(docs/84 #3 多 agent 系统导入:plan/apply 用户可见文案)----
    "system_import.note.no_llm": (
        "No LLM wired (--no-llm?), so the topology can't be read. You can still import each agent "
        "one by one through the regular agent import (each will run its own decomposition)."
    ),
    "system_import.note.triage_failed": (
        "The system reader didn't produce a valid structure, so the topology is lost — reported "
        "honestly, nothing was written. You can import each agent one by one through the regular "
        "agent import (each will run its own decomposition)."
    ),
    "system_import.note.skill_agent": (
        "“{name}” is essentially a process playbook (a skill), not a “who” — it won't land as a "
        "role or atoms; import it through the skill library."
    ),
    "system_import.note.executor_folded": (
        "“{name}” is a pure executor — it lands as shared atoms only (no role seat); its pipeline "
        "step folds into the neighbouring role's step."
    ),
    "system_import.note.skills_to_import": (
        "Recognized embedded skills: {skills}. They were not written anywhere — import them through "
        "the skill library when you want them."
    ),
    "system_import.identity.report_note": (
        "In the original system, {reporters} reported to this role. KarvyLoop never builds "
        "role→role accountability — accountability is rewired to you; this role reviews their "
        "output as a duty, not as their boss."
    ),
    "system_import.relocate.supervisor": (
        "The supervisor's static dispatch power moved up: Karvy plans, you approve — no agent "
        "holds routing power over another."
    ),
    "system_import.task.step_fallback": "Complete your part of this stage (building on upstream output).",
    "system_import.task.review": "Review “{target}”'s output: is it solid, complete, usable downstream?",
    "system_import.workflow.name": "{domain} · imported flow",
    "system_import.seed.topic_fallback": "Kick off a roundtable on the system's goal",
    "system_import.degrade.topology.why": "The collaboration topology could not be read into a valid structure.",
    "system_import.degrade.topology.fallback": (
        "Topology lost (reported honestly). Import agents one by one via the regular agent import."
    ),
    "system_import.degrade.dynamic_route.why": (
        "Dynamic routing (which branch runs is decided at runtime by an agent) — a workflow "
        "template can't hold a runtime router."
    ),
    "system_import.degrade.dynamic_route.fallback": (
        "Flattened into a static dependency; or skip the template and let Karvy plan this hop "
        "fresh each run."
    ),
    "system_import.degrade.loop.why": "Loops aren't supported in workflow templates (honest P1).",
    "system_import.degrade.loop.fallback": (
        "The loop edge was dropped. Alternatives: unroll it as up to 2 explicit rounds when "
        "editing the template, or run it as a roundtable instead."
    ),
    "system_import.degrade.report_chain.why": (
        "“{src}” reported to “{dst}” in the original system — KarvyLoop never builds role→role "
        "accountability chains (accountability runs role→you and atom→role only)."
    ),
    "system_import.degrade.report_chain.fallback": (
        "Downgraded to a review step + the duty written into “{dst}”'s identity; accountability "
        "is rewired to you."
    ),
    "system_import.degrade.blackboard.why": (
        "The system shares a writable blackboard/state between agents — KarvyLoop has no shared "
        "mutable board."
    ),
    "system_import.degrade.blackboard.fallback": (
        "Upstream output feeds downstream steps (inputs), and durable knowledge goes to the "
        "domain knowledge base."
    ),
    "system_import.degrade.schedule.why": "“{agent}” runs on a schedule ({when}) in the original system.",
    "system_import.degrade.schedule.fallback": (
        "Not landed this time (daemon atom + domain routine is phase 2). Recreate it under ⏰ "
        "Scheduled if you need it now."
    ),
    "system_import.apply.missing_registry": "Runtime registries not wired (atom/role/domain registry missing).",
    "system_import.apply.no_domain_name": "The plan has no domain name — give the landing domain a name first.",
    "system_import.apply.same_name": (
        "An active domain named “{name}” already exists — rename the landing domain or archive "
        "the old one first. Nothing was written."
    ),
    "system_import.apply.bad_role_id": "Illegal role id “{role_id}” (letters/digits/underscore/hyphen only). Nothing was written.",
    "system_import.apply.bad_kind": "Role “{role_id}” has an illegal kind (decision/executor/hybrid/skill). Nothing was written.",
    "system_import.apply.failed": "Landing failed midway: {error}. Everything created this time was rolled back — no orphans.",
    "system_import.apply.per_agent_mode": (
        "This plan is in per-agent fallback mode (topology lost) — there is nothing to land as a "
        "system. Import each agent via the regular agent import."
    ),
    # ---- docs/88 外环 Pursuit(招牌"闭环完整性":跨天持久目标)----
    "proposal.pursuit_commit.no_trigger": "(none)",
    "proposal.pursuit_commit.summary": "Commit to a multi-day goal: “{statement}”?",
    "proposal.pursuit_commit.basis": (
        "This is a persistent cross-day goal — I'll keep pushing it on my own and only come back to you at "
        "commit / revise / done. Done gate (deterministic, never asks a model): {gate}. Revision triggers: "
        "{trigger}. ACCEPT = you commit to it and I start running it for days; REJECT / DEFER = I don't."
    ),
    "proposal.pursuit_revise.reason_default": "a revision trigger fired",
    "proposal.pursuit_revise.summary": "A pursuit hit a revision point — your call: “{statement}”",
    "proposal.pursuit_revise.basis": (
        "Reason: {reason}. Changing direction is your decision — I never re-aim a pursuit on my own. "
        "ACCEPT = set this pursuit aside (create a new one to change direction; auto-replan is a later cut); "
        "REJECT / DEFER = leave it paused, I won't run it automatically."
    ),
    "receipt.pursuit.no_store": "Pursuit isn't wired (started with --no-llm?) — can't act on it.",
    "receipt.pursuit.gone": "That pursuit is gone (maybe already finished or dropped).",
    "receipt.pursuit.terminal": "That pursuit is already {status} — nothing to do.",
    "receipt.pursuit_commit.already": "Already committed — I'm running it for you.",
    "receipt.pursuit_commit.ok": "Committed: “{statement}” — I'll keep pushing it and come back at done / revise.",
    "receipt.pursuit_revise.dropped": (
        "Set aside “{statement}” — create a new pursuit to change direction (auto-replan comes later)."
    ),
    "receipt.pursuit_revise.resumed": (
        "Back on it: “{statement}” — I'll keep pushing it and come back at done / revise."
    ),
    "pursuit.progress.done": "done — the goal's verify gate passed",
    "pursuit.progress.transferred": "Taken over by your device “{device}” — this device stands down.",
    "pursuit.progress.remote_done": "Finished on your device “{device}”.",
    "pursuit.receipt.done": "✅ Pursuit done: “{statement}” (its verify gate passed)",
    "pursuit.revise.reason_trigger": "a revision trigger fired",
    "pursuit.revise.reason_max_advances": (
        "pushed {n} times and still hasn't passed its done gate — your call (keep going / change "
        "direction / drop it)"
    ),
    "pursuit.revise.reason_consecutive_failures": (
        "the last {n} pushes all failed outright, so it's paused — your call (keep going / change "
        "direction / drop it)"
    ),
    "pursuit.triage.duplicate": (
        "Already on it — “{statement}” is in your pursuits, so I won't open a second one. "
        "If this really is a different goal, create it from the My Pursuits panel."
    ),
    "pursuit.triage.duplicate_paused": (
        "This goal is paused — “{statement}” is already in your pursuits, waiting on you. "
        "Open My Pursuits and hit Continue to pick it back up (I won't open a second one)."
    ),
    "pursuit.err.gate_not_dict": "A done-check has to be an object with a type.",
    "pursuit.err.gate_type": "For now a done-check can only be one of these kinds: {allowed}.",
    "pursuit.err.gate_cmd": "A test done-check needs a command to run (it's done when the command exits 0).",
    "pursuit.err.gate_cmd_unsplittable": "That test command doesn't parse into something runnable: {cmd}",
    "pursuit.err.gate_path": "A file done-check needs the path of the file to watch for.",
    "pursuit.err.gate_path_placeholder": (
        "The file path can't contain {{...}} placeholders (no path templating): {path}"
    ),
    "pursuit.err.no_store": "Pursuit isn't wired (started with --no-llm?).",
    "pursuit.err.not_found": "That pursuit is gone (maybe already finished or dropped).",
    "pursuit.err.terminal_no_resume": "That pursuit is already {status} — nothing to continue.",
    "pursuit.err.terminal_no_drop": "That pursuit is already {status} — nothing to drop.",
    "pursuit.err.bad_pursuit": "Couldn't build the pursuit: {error}",
    "pursuit.gate_desc.test_pass": "command `{cmd}` exits 0",
    "pursuit.gate_desc.file_exists": "file `{path}` exists",
    # docs/88 真伤7:test_pass 门 fail-loud 原因(cognition 层出稳定码 → 这里出人话进 progress_note)
    "pursuit.gate_note.no_isolation": (
        "This device has no real isolation sandbox, so I can't safely run this test-based done-check."
    ),
    "pursuit.gate_note.net_downgrade": (
        "Ran the done-check without network isolation (this device can't isolate the network)."
    ),
    "pursuit.gate_note.timed_out": "The done-check ran too long and was stopped.",
    "pursuit.gate_note.net_suspect": "The done-check likely couldn't reach the network (isolated).",
    # docs/88 第三刀 #2:「让小卡讲讲」的确定性兜底(gateway 无/失败/空回复时用;零 LLM)
    "pursuit.narrate.fb_advances": "Pushed this forward {n} time(s) so far.",
    "pursuit.narrate.fb_last_ok": "The latest pass finished.",
    "pursuit.narrate.fb_last_fail": "The latest pass got stuck: {err}",
    "pursuit.narrate.fb_stuck": "Stuck {n} passes in a row — waiting on your call.",
    "pursuit.narrate.fb_progress": "Where things stand: {note}",
    "pursuit.narrate.fb_none": "Haven't started on this yet — I'll pick it up on the next pass.",
    # docs/88 第二刀:聊天判型 create(小卡识别跨天目标 → 升承诺卡)的聊天回执 + REJECT 清理回执
    "pursuit.triage.card_text": (
        "This sounds like a multi-day goal to keep pushing: “{statement}”. "
        "Done-check (verified deterministically after each push — no model asked): {gate}. "
        "I've wrapped it into a commitment card — nothing starts until you accept. "
        "Each push spawns a task; after {max_rounds} pushes without passing the done-check "
        "it pauses and asks you. (Decide in 🤝 H2A)"
    ),
    "receipt.pursuit_commit.rejected_cleaned": (
        "Okay, not pursuing “{statement}” — I've cleaned up its record (no leftovers). "
        "Just say the word if you want it back."
    ),
    # D(内测 U-06)多模态降级:模型**显式声明** text-only → 图不拼进请求,占位一句人话(拼在该轮 user 内容里)
    "executor.images_unsupported": (
        "(You attached {n} image(s), but the current model {model} can't view images "
        "(its config declares text-only input) — proceeding with the text as usual. "
        "To use the images, switch to a vision-capable model; or if this model can actually "
        "see images, add `image` to its `input_modalities` in the model config.)"
    ),
    # D② drive 聊天路径 provider 错误人话化(人话在前,真因原文在后 —— fail-loud 不丢真因)
    "config.external_reloaded": (
        "Your model config was changed outside this console (CLI/editor) — reloaded it just now. "
        "If that change was a mistake, check Models (Global)."
    ),
    "config.external_reload_failed": (
        "Your model config was changed outside this console (CLI/editor), but the new config "
        "doesn't load: {reason} — chat may fail until it's fixed in Models (Global)."
    ),
    "task.err.infra_dead": (
        "⚠ Model service unavailable (model/network/sandbox couldn't be reached) — this isn't the "
        "task's fault. Check Models (Global) and your network, then rerun."
    ),
    "task.err.max_turns": "⚠ Hit the per-run step limit before finishing — rerun to continue.",
    "task.err.blocking_limit": "⚠ Token/cost budget ran out before finishing — raise the budget or rerun.",
    "task.err.circuit_open": "⚠ Stopped after repeated failures — something's stuck; take a look, then rerun.",
    "task.err.aborted": "⚠ Interrupted — the result may be incomplete.",
    "task.err.hook_stopped": "⚠ Stopped by a rule/hook.",
    "drive.err.bad_key": (
        "The model provider rejected the credentials (401/403) — check the API key in your "
        "model settings. (cause: {cause})"
    ),
    "drive.err.bad_url": (
        "The model endpoint wasn't found (404) — check base_url / path in your model settings. "
        "(cause: {cause})"
    ),
    "drive.err.unreachable": (
        "Couldn't reach the model service (network/timeout) — check your network or the endpoint, "
        "then try again. (cause: {cause})"
    ),
    "drive.err.bad_request": (
        "The model provider rejected this request (4xx). Usually the request carried something "
        "this model doesn't support (e.g. images sent to a text-only model) or a protocol mismatch. "
        "The task didn't run — adjust the model settings or remove the unsupported content and retry. "
        "(cause: {cause})"
    ),
    # K① 决策卡/路由提示(此前硬编码中文 f-string)
    "report.approach_route": "Executed by “{role}” under domain governance",
    "report.approach_rerun": "Re-run “{intent}”",
    "route.roundtable_hint": (
        "You want {who} to discuss this together — that's opening a **roundtable** (several "
        "people at one table), not handing it to one person. Open a table in “{group}” to "
        "discuss “{topic}”? (decide in 🤝 H2A)"
    ),
    "route.delegate_hint": (
        "This belongs to business domain “{domain_name}” — hand it to “{role}”? (decide in 🤝 H2A)"
    ),
    # U-03:私聊小卡 @角色 → 快通道委派卡(点名=已填好的单,拍一下就开工)
    "route.mention_fastlane_hint": (
        "Delegation card for “{role}” is ready — approve it on the right and they'll get started."
    ),
    "route.mention_multi_hint": (
        "Mention one role at a time here — for several people working together, "
        "@ them in a group, or say “open a roundtable”."
    ),
    "route.mention_no_domain": "Personal",
    # K② 卡侧 LLM prompt 的应答语言指令(跟界面语言;拼进 system prompt 末尾)
    "prompt.lang.answer": "Answer in English.",
    "prompt.lang.json_why": "Write the \"why\" values in English.",
    "prompt.lang.title": "Write the topic name in English.",
    # I:CLI run 默认路径的即时阶段提示(stderr;后续工具/文本事件实时流)
    "cli.run.progress_start": (
        "[karvyloop] working: recalling skills → calling the model if no skill hits; "
        "tool/text events stream below in real time"
    ),
}

# ---- 中文 ----
