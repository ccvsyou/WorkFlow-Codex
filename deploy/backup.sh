#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
tar -czf "backups/flowcodex-${STAMP}.tar.gz" data repos vault .env
echo "已备份到 backups/flowcodex-${STAMP}.tar.gz"
