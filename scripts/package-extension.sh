#!/usr/bin/env bash
# Build a Chrome-loadable zip: manifest.json at zip root (contents of extension/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/h1b-checker-extension.zip}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rsync -a --exclude '.DS_Store' --exclude '__MACOSX' "$ROOT/extension/" "$TMP/extension-pack/"
(
  cd "$TMP/extension-pack"
  zip -qr "$OUT" .
)
echo "Wrote $OUT"
