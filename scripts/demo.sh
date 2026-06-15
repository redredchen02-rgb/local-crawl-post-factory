#!/usr/bin/env bash
# Offline demo of the data pipeline (no crawler, no browser).
# Feeds inputs/sample.ndjson through normalize -> dedupe -> render -> build.
set -euo pipefail
cd "$(dirname "$0")/.."

STATE="./state/demo.sqlite"
OUT="./out/demo"
rm -f "$STATE"; rm -rf "$OUT"

cat inputs/sample.ndjson \
  | normalize-items \
  | dedupe-posts --state "$STATE" \
  | render-caption --template ./templates/fixed-format.zh.yaml \
  | build-manifest --out "$OUT"

echo "--- packages built ---" >&2
ls "$OUT" >&2
