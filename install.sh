#!/usr/bin/env bash
# myTeams install — macOS one-shot, idempotent.
#   deps check → research venv (create only if missing) → API key (existing file
#   never touched) → trs on PATH → stamp-config render + verify.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/openRig-team/research/.venv"
STAMP="$ROOT/openRig-team/scripts/stamp-config.py"
CFG_DIR="$HOME/.config/myteams"
CFG="$CFG_DIR/config.local.yaml"

echo "== myTeams install =="

# 1. deps
missing=0
for c in tmux jq node rig claude python3; do
  if command -v "$c" >/dev/null 2>&1; then
    echo "  ok      $c"
  else
    echo "  MISSING $c"
    missing=1
  fi
done
if [ "$missing" = 1 ]; then
  echo "install: install the missing commands first (tmux jq node rig claude python3)" >&2
  exit 1
fi

# 2. research venv — an existing venv is never clobbered; pins are no-ops when satisfied
if [ ! -d "$VENV" ]; then
  echo "  create  openRig-team/research/.venv"
  python3 -m venv "$VENV"
fi
echo "  deps    requirements.txt -> research/.venv"
# --no-deps: requirements.txt is a closed known-good freeze — re-resolution
# breaks on extras pins (fastmcp -> fastmcp-slim[client,server]).
"$VENV/bin/python" -m pip install --quiet --no-deps -r "$ROOT/requirements.txt"

# 3. machine-local config — existing file is NEVER touched (keys never printed)
mkdir -p "$CFG_DIR"
if [ -f "$CFG" ]; then
  echo "  keep    ~/.config/myteams/config.local.yaml (already present)"
else
  key="${OPENROUTER_API_KEY:-}"
  if [ -z "$key" ]; then
    printf "OpenRouter API key (input hidden): "
    read -rs key
    echo
  fi
  [ -n "$key" ] || { echo "install: empty key" >&2; exit 1; }
  umask 077
  cat > "$CFG" <<EOF
# myTeams machine-local config — NOT committed (secrets + machine paths).
# Consumed by openRig-team/scripts/stamp-config.py. Keys are never printed.
# Optional extra MCP wiring for the CR seat, add manually if you have it:
#   graphify:
#     command: /abs/path/to/graphify-mcp
#     graph: /abs/path/to/graph.json
api_keys:
  OPENROUTER_API_KEY: $key
EOF
  chmod 600 "$CFG"
  echo "  wrote   ~/.config/myteams/config.local.yaml (mode 600)"
fi

# 4. trs on PATH — trs resolves its own symlinks, one link suffices
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/trs" "$HOME/.local/bin/trs"
echo "  link    ~/.local/bin/trs -> $ROOT/trs"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "  note    add ~/.local/bin to PATH (e.g. in ~/.zshrc)" ;;
esac

# 5. render + verify the one-place config
"$VENV/bin/python" "$STAMP"
"$VENV/bin/python" "$STAMP" --check

echo "== done — next: trs up <project-dir>  ·  trs attach director =="
