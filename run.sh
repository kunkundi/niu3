#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose up -d --build
docker compose ps
echo 'NiuNo3: http://127.0.0.1:8789'
