#!/usr/bin/env bash
# Serve Qwen3-Coder-30B-A3B on vLLM-Metal for the LR seat.
# Hard rule: cap context to what KV cache holds (full ~262K default blows it).
# 128k = proven safe with gpu-util 0.50 on M-series; 65536 creeps to ceiling on
# long research sessions (32k→64k walls both hit). 128k gives headroom + pointer
# discipline keeps real usage well below.
#
# Lifecycle owner for the :8000 model server. vLLM has no cleanup (EngineCore
# reparents to PID 1 when the serve parent dies) and openRig `services:` is
# compose-only, so this script IS the reaper. Commands: up | down | reap | status.
set -euo pipefail
cd "$(dirname "$0")"

# ponytail: default resolves the local HF snapshot (portable, same path this script
# has always used); set MODEL= to override (repo id or any snapshot dir).
MODEL="${MODEL:-$(ls -d "$HOME"/.cache/huggingface/hub/models--mlx-community--Qwen3-Coder-30B-A3B-Instruct-4bit/snapshots/* 2>/dev/null | head -1)}"
if [ -z "$MODEL" ]; then
  echo "up: MODEL unset and no local snapshot found — run with MODEL=<path-or-repo-id>" >&2
  exit 2
fi
PORT="${PORT:-8000}"
LOG="${LOG:-/tmp/vllm-30b.serve.log}"

# Only parentless EngineCore children are garbage — a live serve's engine has
# the APIServer as parent and must never be touched. [V] keeps ps from
# matching its own grep pattern.
reap_orphans() {
  ps -eo pid=,ppid=,command= | awk '/[V]LLM::EngineCore/ && $2 == 1 {print $1}' | while read -r pid; do
    kill "$pid" 2>/dev/null || true
  done
}

case "${1:-up}" in
  reap)
    reap_orphans
    echo "reap: orphan VLLM::EngineCore (PPID 1) cleared"
    ;;
  down)
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | xargs kill 2>/dev/null || true
    pkill -f '[V]LLM::EngineCore' 2>/dev/null || true
    echo "down: :$PORT serve + EngineCore cleared"
    ;;
  status)
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo ":$PORT LISTENING"
    else
      echo ":$PORT down"
    fi
    ps -eo pid=,ppid=,command= | awk '/[V]LLM::EngineCore/ {print "EngineCore pid="$1" ppid="$2}'
    ;;
  up)
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "up: refusing double-start — :$PORT already listening (run 'vllm-30b.sh down' first)" >&2
      exit 1
    fi
    reap_orphans
    sleep 1
    echo "up: serving qwen3-coder-30b on :$PORT (log $LOG)"
    exec vllm serve "$MODEL" \
      --served-model-name qwen3-coder-30b \
      --host 127.0.0.1 --port "$PORT" \
      --gpu-memory-utilization 0.50 \
      --max-model-len 131072 \
      --enable-auto-tool-choice \
      --tool-call-parser qwen3_xml \
      --reasoning-parser qwen3 \
      >>"$LOG" 2>&1
    ;;
  *)
    echo "usage: vllm-30b.sh [up|down|reap|status]" >&2
    exit 2
    ;;
esac
