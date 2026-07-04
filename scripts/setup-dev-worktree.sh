#!/usr/bin/env bash
# Create a git worktree for isolated dev alongside prod Condor.
# Usage: ./scripts/setup-dev-worktree.sh [branch-name]
set -euo pipefail

BRANCH="${1:-merge-upstream-2026-07-04}"
PROD_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEV_DIR="$(dirname "$PROD_DIR")/condor-dev"

echo "Prod worktree:  $PROD_DIR"
echo "Dev worktree:   $DEV_DIR"
echo "Dev branch:     $BRANCH"
echo ""

if [ ! -d "$DEV_DIR" ]; then
  if git -C "$PROD_DIR" worktree add "$DEV_DIR" "$BRANCH" 2>/dev/null; then
    echo "Created worktree at $DEV_DIR (branch $BRANCH)"
  else
    LOCAL_BRANCH="condor-dev-local"
    git -C "$PROD_DIR" worktree add -b "$LOCAL_BRANCH" "$DEV_DIR" "$BRANCH"
    echo "Created worktree at $DEV_DIR (branch $LOCAL_BRANCH at $BRANCH)"
  fi
else
  echo "Worktree already exists at $DEV_DIR (skipping git worktree add)"
fi

# Copy shared credentials; dev uses isolated config/state paths via make dev-local.
if [ -f "$PROD_DIR/.env" ]; then
  cp "$PROD_DIR/.env" "$DEV_DIR/.env"
  echo "Copied .env from prod worktree"
fi

if [ -f "$PROD_DIR/config.yml" ] && [ ! -f "$DEV_DIR/config.dev.yml" ]; then
  cp "$PROD_DIR/config.yml" "$DEV_DIR/config.dev.yml"
  echo "Created config.dev.yml from prod config.yml"
fi

echo ""
echo "Installing dependencies in dev worktree..."
(cd "$DEV_DIR" && uv sync --extra dev)
(cd "$DEV_DIR/frontend" && npm install)

echo ""
echo "Setup complete."
echo ""
echo "  Prod (Telegram + dashboard):  keep running in primary worktree (make run)"
echo "                                http://localhost:8088"
echo ""
echo "  Dev (web-only, feature branch): cd $DEV_DIR && make dev-local"
echo "                                  http://localhost:5174  (API :8089)"
echo ""
echo "Warnings:"
echo "  - Never set CONDOR_WEB_ONLY=1 on prod."
echo "  - Both instances can share Hummingbot API; avoid live trading agents on dev unless intentional."
