#!/bin/bash
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$HERE" exec python3 -m research.cli "$@"
