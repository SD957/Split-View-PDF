#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:---core}"

python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip
"$HERE/.venv/bin/pip" install -r "$HERE/requirements-core.txt"

(
  cd "$HERE/parsers"
  npm ci
)

mkdir -p "$HERE/third_party"
if [[ ! -f "$HERE/third_party/pdfbox-app-3.0.6.jar" ]]; then
  curl -fL "https://archive.apache.org/dist/pdfbox/3.0.6/pdfbox-app-3.0.6.jar" \
    -o "$HERE/third_party/pdfbox-app-3.0.6.jar"
fi
if [[ ! -d "$HERE/third_party/xpdf-tools-linux-4.06" ]]; then
  curl -fL "https://dl.xpdfreader.com/xpdf-tools-linux-4.06.tar.gz" \
    -o "$HERE/third_party/xpdf-tools-linux-4.06.tar.gz"
  tar -xzf "$HERE/third_party/xpdf-tools-linux-4.06.tar.gz" -C "$HERE/third_party"
fi

if [[ "$MODE" == "--all" ]]; then
  "$HERE/.venv/bin/pip" install -r "$HERE/requirements-text-pipelines.txt"
  (
    cd "$HERE/liteparse"
    npm install
  )
fi

printf 'Setup complete. Use ./run.sh <input.pdf>\n'
