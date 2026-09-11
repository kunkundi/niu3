#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check app tests scripts
uv run python -m unittest discover -s tests -v
node --check web/src/state.js
node --check web/src/main.js
node --check web/src/intraday.js
node --check web/src/intraday-levels.js
node --check web/src/candles.js
node --check web/src/chart-review.js
node --check web/src/chart-layout.js
node --check web/src/signal-chart.js
node --check web/src/price-action/engine.js
node --check web/src/price-action/renderer.js
node --check web/src/price-action/index.js
node --check web/src/price-action/strategy.js
node --check web/src/price-action/daily-policy.js
node --check web/src/price-action/current-context.js
node --check web/src/price-action/direction-context.js
node --check scripts/price_action.mjs
node --check scripts/import_niutwo.mjs
node --check scripts/niutwo-current-state.mjs
node --check scripts/niutwo-direction-control.mjs
pnpm --dir web test
node --check web/public/assets/theme.js
node --check web/vite.config.js
pnpm --dir web exec prettier --check src tests public vite.config.js index.html package.json
pnpm --dir web build
docker compose config --quiet
git diff --check
