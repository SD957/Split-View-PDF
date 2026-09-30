#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -x "$HERE/.venv/bin/python" ]]; then
  printf 'Missing .venv. Run ./setup.sh --core or ./setup.sh --all first.\n' >&2
  exit 2
fi

export PDFBOX_JAR="${PDFBOX_JAR:-$HERE/third_party/pdfbox-app-3.0.6.jar}"
export XPDF_BIN_DIR="${XPDF_BIN_DIR:-$HERE/third_party/xpdf-tools-linux-4.06/bin64}"
exec "$HERE/.venv/bin/python" "$HERE/run_parsers.py" "$@"
