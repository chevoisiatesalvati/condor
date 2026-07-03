#!/usr/bin/env bash
# Remove trading_agents/macdbb_scanner_aggressive_hl/presets.py from all git history
# (eliminating leaked winner parameters), then reintroduce the framework-only file.
#
# Prerequisites:
#   pip install git-filter-repo
#   Working tree contains the refactored framework-only presets.py (uncommitted is OK).
#
# Usage:
#   ./scripts/scrub_presets_history.sh
#   git push --force-with-lease origin $(git branch --show-current)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PRESETS_PATH="trading_agents/macdbb_scanner_aggressive_hl/presets.py"

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "git-filter-repo is required: pip install git-filter-repo" >&2
  exit 1
fi

if [[ ! -f "$PRESETS_PATH" ]]; then
  echo "Missing $PRESETS_PATH" >&2
  exit 1
fi

if grep -q "hl_dynamic_timeline_refine_v5_winner_binance_1y" "$PRESETS_PATH"; then
  echo "Current $PRESETS_PATH still embeds private winner preset names." >&2
  echo "Land the framework-only refactor before scrubbing history." >&2
  exit 1
fi

BACKUP="$(mktemp)"
cp "$PRESETS_PATH" "$BACKUP"

echo "Removing $PRESETS_PATH from entire git history..."
git filter-repo --force --invert-paths --path "$PRESETS_PATH"

echo "Reintroducing framework-only $PRESETS_PATH..."
mkdir -p "$(dirname "$PRESETS_PATH")"
cp "$BACKUP" "$PRESETS_PATH"
git add "$PRESETS_PATH"
git commit -m "$(cat <<'EOF'
Reintroduce framework-only macdbb presets loader after history scrub.

Private winner parameters now live in strategies/{slug}/presets.yaml.
EOF
)"

rm -f "$BACKUP"

echo "Done. Verify with: git log --oneline -- $PRESETS_PATH"
echo "Then: git push --force-with-lease origin $(git branch --show-current)"
