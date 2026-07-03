#!/usr/bin/env bash
# Push the local strategies/ repo to a private GitHub remote and register it as a submodule.
#
# Prerequisites:
#   1. Create an empty private repo on GitHub (e.g. condor-strategies).
#   2. Export the SSH URL:
#        export CONDOR_STRATEGIES_REMOTE=git@github.com:YOUR_USER/condor-strategies.git
#
# Usage (from condor repo root):
#   ./scripts/setup_condor_strategies_remote.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REMOTE="${CONDOR_STRATEGIES_REMOTE:-}"
if [[ -z "$REMOTE" ]]; then
  echo "Set CONDOR_STRATEGIES_REMOTE to your private repo SSH URL." >&2
  echo "Example: export CONDOR_STRATEGIES_REMOTE=git@github.com:chevoisiatesalvati/condor-strategies.git" >&2
  exit 1
fi

if [[ ! -d strategies/.git ]]; then
  echo "strategies/ is not a git repo. Run migration first or clone your private repo into strategies/." >&2
  exit 1
fi

echo "Pushing strategies/ to $REMOTE ..."
git -C strategies remote remove origin 2>/dev/null || true
git -C strategies remote add origin "$REMOTE"
git -C strategies branch -M main 2>/dev/null || true
git -C strategies push -u origin main

if [[ -f .gitmodules ]] && grep -q 'path = strategies' .gitmodules 2>/dev/null; then
  echo "Submodule already registered in .gitmodules."
  git submodule update --init --recursive strategies
  exit 0
fi

if [[ -d strategies ]] && [[ ! -f .git/modules/strategies/config ]]; then
  echo "Registering strategies/ as submodule..."
  STRATEGIES_HEAD="$(git -C strategies rev-parse HEAD)"
  mv strategies strategies.bak
  git submodule add "$REMOTE" strategies
  git -C strategies checkout "$STRATEGIES_HEAD" 2>/dev/null || true
  rm -rf strategies.bak
  echo "Submodule added at commit $STRATEGIES_HEAD"
  echo "Commit .gitmodules and strategies/ in the public condor repo."
fi
