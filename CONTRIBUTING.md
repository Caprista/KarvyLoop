# Contributing to KarvyLoop

Thanks for looking at KarvyLoop. This page gets you from a fresh clone to a green
test run and a passing lint gate in a few minutes.

> Language: this repo defaults to English, with a Chinese README ([README.zh-CN.md](README.zh-CN.md)).
> Any user-visible string must ship in both `en` and `zh` tables (a parity test locks this).

## Quick start (clone → test → lint)

```bash
git clone https://github.com/Caprista/KarvyLoop.git
cd KarvyLoop

# 1. Install in editable mode WITH the dev extra. The [dev] extra is required for a clean
#    test collection — it pulls respx + psutil, the only non-core deps that test modules
#    import at top level. A bare `pip install -e .` will surface collection errors; use [dev].
pip install -e ".[dev]"

# 2. Run the test suite. On a clean [dev] install this collects with zero errors.
pytest -q

# 3. Run the lint gate (same one CI runs). It only flags; it does not rewrite your code.
ruff check .
```

That's the whole contributor loop: **`pip install -e ".[dev]"` → `pytest` → `ruff check`.**
CI runs exactly these two gates on every pull request (Ubuntu, Python 3.11 + 3.12), plus a
Windows leg (windows-latest, Python 3.12) and a macOS leg (macos-latest, Python 3.12) so
all three sandbox backends (bubblewrap / win32 / Seatbelt) stay verified — sandbox tests
that need OS privileges the runner can't grant self-skip rather than fail.

## Optional feature dependencies

Most extras are truly optional — the tests that need them **self-skip** via
`pytest.importorskip`, so the suite stays green without them. Install an extra only if
you're working on that feature:

| Extra | Enables | Notes |
|-------|---------|-------|
| `mcp` | MCP client / remote-server tests | `pip install -e ".[dev,mcp]"` |
| `relay` | Karvy messenger E2E-encryption (relay) tests | needs `cryptography` |
| `web` | Playwright web-verify / desk-soul UI tests | also run `playwright install chromium` |
| `redis` | cross-process A2A transport tests | needs a local redis; otherwise those tests skip |

CI installs `.[dev,mcp,relay]` so those paths actually run; `web` and `redis` tests
self-skip in CI (browser download / external service).

## Lint gate details

The lint gate is `ruff check` (configured in `pyproject.toml` under `[tool.ruff]`).

- It runs **check only, never `--fix`** — it won't rewrite your code out from under you.
- The current codebase passes clean. A number of rules are ignored as tracked debt
  (unused imports, long bilingual-comment lines, etc.) — each ignore is commented with
  *why* in `[tool.ruff.lint].ignore`. These will be tightened incrementally; please don't
  re-introduce violations in the still-active rules (real bug catchers: `E9`, `F63/F7`,
  `F82`, `W605`).

### Optional: run the lint gate before committing

If you'd like to catch lint issues before you push (opt-in, not required), the simplest
way is to run the same command CI runs:

```bash
ruff check .
```

You can wire this into a local git pre-commit hook, or use the
[`pre-commit`](https://pre-commit.com) tool with a `ruff-pre-commit` hook pinned to the
same ruff version as the `[dev]` extra. CI (`.github/workflows/ci.yml`) is the source of
truth either way, so a local hook is purely a convenience.

## Pull requests

- PRs trigger CI automatically. A green check means `ruff check` passed and the full suite
  collected + ran on 3.11 and 3.12. Please make sure CI is green before requesting review.
- Non-trivial changes: run the suite locally first (`pytest -q`). If you touch a security
  path (deontic gate, sandbox, relay, silence), keep its adversarial tests passing.
- Keep the runtime thin. Dev-time infra (CI, lint, pre-commit) is welcome; runtime
  scaffolding is scrutinized — the design bias is "trust the model, keep the loop lean."

## Reporting issues

Open a GitHub issue. If it's an environment/setup problem, please include your OS, Python
version, and whether you installed with `.[dev]` — most "collection error" reports trace
back to a bare `pip install -e .` without the dev extra.
