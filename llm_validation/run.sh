#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -x "$HERE/.venv/bin/python" ]]; then
  printf 'Missing .venv. Run ./setup.sh first.\n' >&2
  exit 2
fi
exec "$HERE/.venv/bin/python" "$HERE/run_suite.py" "$@"
