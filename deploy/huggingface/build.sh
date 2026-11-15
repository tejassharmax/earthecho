#!/usr/bin/env bash
# Assemble the Hugging Face Space folder: this launcher + backend (no secrets) + built frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?usage: build.sh <output-dir>}"
rm -rf "$OUT"; mkdir -p "$OUT/backend" "$OUT/frontend"
cp "$ROOT/deploy/huggingface/"{app.py,README.md,requirements.txt,packages.txt} "$OUT/"
cp -r "$ROOT/backend/app" "$OUT/backend/app"
cp -r "$ROOT/backend/demo" "$OUT/backend/demo"
rm -f "$OUT/backend/demo/showcase_raw.json"
cp -r "$ROOT/frontend/dist" "$OUT/frontend/dist"
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$OUT" -name ".env" -delete
