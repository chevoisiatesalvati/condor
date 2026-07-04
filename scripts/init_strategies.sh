#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .gitmodules ]] && grep -q 'path = strategies' .gitmodules 2>/dev/null; then
  echo "Initializing strategies submodule (tracking main)..."
  git submodule update --init --remote strategies
  exit 0
fi

if [[ -d strategies/.git ]]; then
  echo "strategies/ already present ($(git -C strategies rev-parse --short HEAD 2>/dev/null || echo local))"
  exit 0
fi

if [[ -d strategies/macdbb_scanner_aggressive_hl ]]; then
  echo "strategies/ directory exists but is not a git repo."
  echo "Initialize your private condor-strategies remote, then either:"
  echo "  git submodule add git@github.com:YOUR_ORG/condor-strategies.git strategies"
  echo "  or clone into strategies/ manually."
  exit 0
fi

echo "No strategies submodule configured."
echo "Create a private condor-strategies repo, then run:"
echo "  git submodule add git@github.com:YOUR_ORG/condor-strategies.git strategies"
echo ""
echo "For local-only development (placeholder agent only — not production tuning):"
echo "  mkdir -p strategies/macdbb_scanner_aggressive_hl"
echo "  cp trading_agents/macdbb_scanner_aggressive_hl/presets.private.example.yaml strategies/macdbb_scanner_aggressive_hl/presets.yaml"
echo ""
echo "For production: add agent.md and presets.yaml under strategies/{slug}/ in the private repo."
