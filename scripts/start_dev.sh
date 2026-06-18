#!/usr/bin/env bash
# Compatibility entry for older/local runbooks.
# The maintained local startup flow lives at the repository root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/start_local.sh" "$@"
