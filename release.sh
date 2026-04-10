#!/bin/bash
# release.sh - Version, commit, tag, push, and release mapping.json
# Version format: YYYY.MM.NNN (NNN resets each month, starts at 001)
#
# Usage:
#   ./release.sh              # Auto-detect changes, release if any
#   ./release.sh --force      # Release even if no tracked file changes
#   ./release.sh --dry-run    # Show what would happen

set -euo pipefail
cd "$(dirname "$0")"

DRY_RUN=false
FORCE=false
for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=true ;;
        --force) FORCE=true ;;
    esac
done

# --- Check for changes ---
CHANGED=false
if git diff --quiet HEAD -- confirmed.json mapping.json ani_tmdb_mapper.py release.sh 2>/dev/null; then
    if [ "$FORCE" = false ]; then
        echo "ℹ️  No changes to release. Use --force to override."
        exit 0
    fi
else
    CHANGED=true
fi

# --- Compute next version ---
YM=$(date +%Y.%m)
LATEST=$(git tag --list "${YM}.*" --sort=-version:refname 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
    NNN=1
else
    LAST_NNN=$(echo "$LATEST" | awk -F. '{print $3}')
    # Strip leading zeros for arithmetic
    LAST_NNN=$((10#$LAST_NNN))
    NNN=$((LAST_NNN + 1))
fi
NEW_TAG="${YM}.$(printf '%03d' $NNN)"

echo "📦 New release: $NEW_TAG (changed=$CHANGED)"

# --- Commit ---
if [ "$DRY_RUN" = true ]; then
    echo "  [DRY RUN] Would commit and tag: $NEW_TAG"
    exit 0
fi

git add confirmed.json mapping.json ani_tmdb_mapper.py release.sh
if [ "$CHANGED" = true ]; then
    git commit -m "release ${NEW_TAG}: update mappings"
else
    git commit --allow-empty -m "release ${NEW_TAG}: forced update"
fi

# --- Tag and push ---
git tag "$NEW_TAG"
git push origin main --tags

# --- GitHub Release ---
JSD_URL="https://cdn.jsdelivr.net/gh/ChowDPa02k/ani-tmdb-mapper@${NEW_TAG}/mapping.json"
gh release create "$NEW_TAG" \
    --title "mapping.json $NEW_TAG" \
    --notes "Automated mapping update.

**jsDelivr URL**: \`${JSD_URL}\`

Latest mapping data for ANi → TMDB season/episode resolution." \
    mapping.json

echo "✅ Released: $NEW_TAG"
echo "🌐 jsDelivr: $JSD_URL"
