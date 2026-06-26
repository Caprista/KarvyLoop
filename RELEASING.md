# Releasing KarvyLoop

KarvyLoop ships **by version**, and every release carries its changes. This is
the process; it's short on purpose.

## Versioning

- **Date-based (CalVer)** — `YYYY.M.D`, the date the release is cut (e.g. `2026.6.26`).
  A later date is a newer version; the update check compares them as dotted integers.
- **Single source of truth**: `karvyloop/__init__.py:__version__`. `pyproject.toml`
  reads it dynamically; the CLI, console, and update check all import it. Change
  the version in **one** place.
- Tags are the bare date `YYYY.M.D` (e.g. `2026.6.26`). The in-app update check reads the
  latest GitHub **Release** `tag_name`, so a release must be a real Release on a date
  tag — not just a pushed commit.

## Cutting a release

1. **Bump** `__version__` in `karvyloop/__init__.py` to today's date `YYYY.M.D`.
2. **Changelog** — in `CHANGELOG.md`, move the `[Unreleased]` items into a new
   `YYYY.M.D` section (Added / Changed / Fixed / Removed). This *is* the release
   notes; keep it written for a reader, not a commit log.
3. **Green** — `pytest -q` must pass.
4. **Commit** — `release: YYYY.M.D`.
5. **Tag & push** — `git tag -a YYYY.M.D -m "YYYY.M.D"` then `git push && git push --tags`.
6. **GitHub Release** — `gh release create YYYY.M.D --title YYYY.M.D --notes "<the CHANGELOG section>"`.
   This is what users' update check and banner surface.

## What users experience (and what we promise)

- **Detect → notify → they decide.** The console shows a dismissible "new version"
  banner; `karvyloop update` prints the same. KarvyLoop **never auto-upgrades** —
  upgrading is the user's call (it's H2A applied to the product itself).
- **Upgrade command** depends on how they installed: `git pull && pip install -e .`
  (from a clone) or `pip install -U karvyloop` (PyPI, once published).
- **Their data survives.** Everything in `~/.karvyloop/` (config, beliefs, skills,
  decision log) is outside the repo and must stay forward-compatible across
  versions. A breaking data change must ship a migration (and be called out loudly in
  the release notes).
- **No telemetry.** The update check is a plain version query to GitHub; it sends
  no user data and can be turned off with `KARVYLOOP_NO_UPDATE_CHECK=1`.
