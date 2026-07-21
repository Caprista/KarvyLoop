#!/bin/sh
# KarvyLoop uninstaller (Linux / macOS) — removes exactly what install.sh created.
# Instance data (~/.karvyloop: config, keys, beliefs, skills, decision log) is KEPT by
# default; pass --purge-data to remove it too (consider `karvyloop export` first).
set -eu

VENV="$HOME/.karvyloop-venv"
BINLINK="$HOME/.local/bin/karvyloop"
DATA="$HOME/.karvyloop"
PURGE=0
[ "${1:-}" = "--purge-data" ] && PURGE=1

say() { printf '%s\n' "$*"; }

say "KarvyLoop uninstall:"
if [ -d "$VENV" ]; then
  rm -rf "$VENV" && say "  removed $VENV"
else
  say "  (no venv at $VENV)"
fi
if [ -L "$BINLINK" ] || [ -e "$BINLINK" ]; then
  rm -f "$BINLINK" && say "  removed $BINLINK"
else
  say "  (no launcher at $BINLINK)"
fi

# PATH lines the installer appended (marked). Sed in-place portably (backup then rm).
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.zlogin" "$HOME/.profile"; do
  [ -f "$rc" ] || continue
  if grep -q '# added by KarvyLoop installer' "$rc" 2>/dev/null; then
    cp "$rc" "$rc.karvyloop-uninstall.bak"
    # drop the marker line and the export line right after it
    awk 'BEGIN{skip=0} /# added by KarvyLoop installer/{skip=2} skip>0{skip--; next} {print}' \
      "$rc.karvyloop-uninstall.bak" > "$rc"
    say "  cleaned PATH line in $rc (backup: $rc.karvyloop-uninstall.bak)"
  fi
done

if [ "$PURGE" = 1 ]; then
  if [ -d "$DATA" ]; then
    rm -rf "$DATA" && say "  removed $DATA (instance data — gone)"
  fi
else
  [ -d "$DATA" ] && say "  KEPT $DATA (your instance data) — rerun with --purge-data to remove it"
fi
say "Done. Open a new shell so PATH changes take effect."
