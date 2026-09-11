#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# This project name and database are intentionally separate from live paper-trading data.
if docker volume inspect niuno3-acceptance_niuno3-data >/dev/null 2>&1; then
  echo 'Acceptance volume already exists; inspect it before removing or rerunning.' >&2
  exit 1
fi
for stage in buy hold exit verify; do
  docker compose -p niuno3-acceptance run --rm --no-deps \
    -e NIUNO3_ACCEPTANCE=1 worker python -m scripts.scenario "$stage"
done
docker compose -p niuno3-acceptance down -v
