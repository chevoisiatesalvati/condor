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
elif [ ! -f "$DEV_DIR/config.dev.yml" ] && [ -f "$DEV_DIR/config.dev.yml.example" ]; then
  cp "$DEV_DIR/config.dev.yml.example" "$DEV_DIR/config.dev.yml"
  echo "Created config.dev.yml from config.dev.yml.example (edit admin_id and web_jwt_secret)"
fi

# Optional: share prod macdbb session history with dev (read-only symlink).
MACDBB_STRATEGY_DIR="$DEV_DIR/agents/macdbb_scanner_aggressive_hl/strategies/macdbb_scanner_aggressive_hl"
PROD_MACDBB="$PROD_DIR/agents/macdbb_scanner_aggressive_hl/strategies/macdbb_scanner_aggressive_hl"
if [ -d "$PROD_MACDBB/sessions" ] && [ ! -e "$MACDBB_STRATEGY_DIR/sessions" ]; then
  ln -s "$PROD_MACDBB/sessions" "$MACDBB_STRATEGY_DIR/sessions"
  echo "Linked prod macdbb sessions into dev strategy dir"
fi
if [ -f "$PROD_MACDBB/learnings.md" ] && [ ! -e "$MACDBB_STRATEGY_DIR/learnings.md" ]; then
  ln -s "$PROD_MACDBB/learnings.md" "$MACDBB_STRATEGY_DIR/learnings.md"
  echo "Linked prod macdbb learnings.md into dev strategy dir"
fi
if [ -d "$PROD_DIR/reports" ] && [ ! -e "$DEV_DIR/reports" ]; then
  ln -s "$PROD_DIR/reports" "$DEV_DIR/reports"
  echo "Linked prod reports into dev reports/ (for replay/timeline tests)"
fi
if [ -d "$PROD_DIR/reports" ] && [ ! -e "$DEV_DIR/reports-dev" ]; then
  ln -s "$PROD_DIR/reports" "$DEV_DIR/reports-dev"
  echo "Linked prod reports into dev reports-dev (for make dev-local)"
fi
if [ -d "$PROD_DIR/data/replay_snapshots_binance_1y" ] && [ ! -e "$DEV_DIR/data/replay_snapshots_binance_1y" ]; then
  mkdir -p "$DEV_DIR/data"
  ln -s "$PROD_DIR/data/replay_snapshots_binance_1y" "$DEV_DIR/data/replay_snapshots_binance_1y"
  echo "Linked prod replay snapshots into dev data/"
fi

echo ""
echo "Installing dependencies in dev worktree..."
(cd "$DEV_DIR" && uv sync --extra dev)
(cd "$DEV_DIR/frontend" && npm install)
(cd "$DEV_DIR/condor/acp/cursor_bridge" && npm install)

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
