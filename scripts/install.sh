#!/usr/bin/env bash
# KarvyLoop installer — installs the `karvyloop` command onto your PATH, isolated from system Python.
#
#   curl -fsSL https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.sh | bash
#
# Why this exists: `pip install` drops the `karvyloop` script into a Python bin dir that often isn't on your
# PATH (a non-activated venv, or `--user` where ~/.local/bin isn't on PATH), so you'd have to type a full path.
# This installer puts KarvyLoop in its own isolated venv AND makes `karvyloop` resolve on your PATH — with
# nothing for you to configure. It's safe on modern "externally managed" distros (PEP 668) because it installs
# into a dedicated venv, never system Python, and needs no pipx / no system packages.
#
# Env overrides:  KARVYLOOP_REF=<branch|tag>   KARVYLOOP_EXTRAS=mcp,web   KARVYLOOP_REPO=<git url>
set -euo pipefail

REPO="${KARVYLOOP_REPO:-https://github.com/Caprista/KarvyLoop.git}"
REF="${KARVYLOOP_REF:-main}"
EXTRAS="${KARVYLOOP_EXTRAS:-}"
VENV="$HOME/.karvyloop-venv"
BINDIR="$HOME/.local/bin"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

if [ -n "$EXTRAS" ]; then
  SPEC="karvyloop[${EXTRAS}] @ git+${REPO}@${REF}"
else
  SPEC="git+${REPO}@${REF}"
fi

# 1) find a Python 3.11+
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || die "Python 3.11+ is required but was not found. Install it and re-run."
say "→ Using $("$PY" -V 2>&1)  ($(command -v "$PY"))"

# 2) Self-contained, one path, zero config: a dedicated venv + a symlink onto ~/.local/bin. Installing INTO a
#    venv is always allowed (no PEP 668 "externally managed" wall — that's why we don't touch system pip or
#    depend on pipx). Re-running upgrades in place. This is exactly the path validated end-to-end via curl|bash.
say "→ Creating an isolated environment at $VENV …"
"$PY" -m venv "$VENV" 2>/dev/null \
  || die "couldn't create a venv — install the venv module first:  sudo apt install python3-venv"
"$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
say "→ Installing KarvyLoop from ${REPO}@${REF} …"
"$VENV/bin/python" -m pip install -q --upgrade "$SPEC" || die "install failed."
mkdir -p "$BINDIR"
ln -sf "$VENV/bin/karvyloop" "$BINDIR/karvyloop"
say "→ Linked $BINDIR/karvyloop"
# ensure ~/.local/bin is on PATH (append to your shell rc if it isn't already — nothing else to configure)
case ":${PATH:-}:" in
  *":$BINDIR:"*) : ;;
  *)
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
      [ -e "$rc" ] || continue
      grep -q '.local/bin' "$rc" 2>/dev/null || printf '\n# added by KarvyLoop installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
    done
    ;;
esac

say ""
say "✓ KarvyLoop installed."
say ""
say "  Open a NEW terminal (or:  source ~/.bashrc  /  source ~/.zshrc), then:"
say "     karvyloop console      # start the local console (opens the web UI)"
say "     karvyloop url          # print the access link (needed to reach it from another device)"
say ""
say "  Third-party-skill sandbox on Linux needs bubblewrap:   sudo apt install bubblewrap"
say "  (macOS uses the built-in sandbox — nothing to install.)"
