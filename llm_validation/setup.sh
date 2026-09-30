#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip
"$HERE/.venv/bin/pip" install -r "$HERE/requirements.txt"
printf 'Setup complete. Copy .API_KEY.example to .API_KEY before live API runs.\n'
