#!/usr/bin/env bash
# Bring the AET rig up. Source of truth = this folder; runtime state under
# ~/.openrig is a derived copy, never the master.
set -euo pipefail
cd "$(dirname "$0")/.."

SEAT_STATE="$HOME/.openrig/state/pi/specialists-researcher-local@aet/agent"
mkdir -p "$SEAT_STATE"
cp agents/researcher-local/models.json "$SEAT_STATE/models.json"

# CR seat: gemma on :8768 — needs its own models.json + default model,
# else pi falls back to the global vllm/qwen default and fails auth.
CR_STATE="$HOME/.openrig/state/pi/specialists-code-reader@aet/agent"
mkdir -p "$CR_STATE"
cp agents/code-reader/models.json "$CR_STATE/models.json"
[ -f "$CR_STATE/settings.json" ] || cat > "$CR_STATE/settings.json" <<'EOF'
{
    "defaultProvider": "mlx-gemma",
    "defaultModel": "mlx-community/gemma-4-e4b-it-4bit"
}
EOF

# Guard: each agent.yaml `name:` must match its directory — a copy-pasted spec
# name (e.g. the builtin `orchestrator`) silently boots the wrong role.
for spec in agents/*/agent.yaml; do
  want="$(basename "$(dirname "$spec")")"
  got="$(sed -n 's/^name:[[:space:]]*//p' "$spec" | head -1)"
  if [ "$got" != "$want" ]; then
    echo "rig-up: $spec declares name='$got', expected '$want' — fix before up" >&2
    exit 1
  fi
done

# Max-permissive seats: rig.yaml declares `permission_policy: builtin:yolo`
# (persists full_bypass to DB — daemon-independent). Do NOT rely on OPENRIG_YOLO
# env here; the daemon doesn't inherit it from this client process.

# Clean run: reap orphaned vLLM EngineCore engines (PPID 1) before up — a live
# serve's engine is never touched. Keeps model weight from accumulating across
# rig restarts (the 215GB swap-peg root cause).
./scripts/vllm-30b.sh reap

exec rig up rig.yaml --cwd "${1:-$HOME/Desktop/aet-test-project}" --yes
