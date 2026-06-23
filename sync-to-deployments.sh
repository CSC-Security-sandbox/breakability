#!/usr/bin/env bash
# sync-to-deployments.sh - Sync centralized scripts to deployment repos
#
# Usage: ./sync-to-deployments.sh [ndm|vcp|both]
#
# Copies scripts/ and workflows/ from breakability repo to deployment repos.
# Maintains single source of truth in breakability repo.

set -euo pipefail

BREAKABILITY_ROOT="$(cd "$(dirname "$0")" && pwd)"
NDM_REPO="${NDM_REPO:-$HOME/code/ndm-fresh-breakability}"
VCP_REPO="${VCP_REPO:-$HOME/code/vcp-fresh-fix}"

TARGET="${1:-both}"

sync_to_repo() {
    local repo=$1
    local name=$2
    
    if [[ ! -d "$repo" ]]; then
        echo "⚠️  $name repo not found: $repo"
        return 1
    fi
    
    echo "📦 Syncing to $name: $repo"
    
    # Copy scripts
    mkdir -p "$repo/.github/scripts"
    rsync -av --delete \
        "$BREAKABILITY_ROOT/scripts/" \
        "$repo/.github/scripts/" \
        --exclude="*.pyc" \
        --exclude="__pycache__"
    
    # Copy workflow
    mkdir -p "$repo/.github/workflows"
    cp "$BREAKABILITY_ROOT/workflows/breakability-agent.yml" \
       "$repo/.github/workflows/breakability-agent.yml"
    
    echo "✅ $name synced"
}

case "$TARGET" in
    ndm)
        sync_to_repo "$NDM_REPO" "NDM"
        ;;
    vcp)
        sync_to_repo "$VCP_REPO" "VCP"
        ;;
    both)
        sync_to_repo "$NDM_REPO" "NDM"
        sync_to_repo "$VCP_REPO" "VCP"
        ;;
    *)
        echo "Usage: $0 [ndm|vcp|both]"
        exit 1
        ;;
esac

echo ""
echo "📌 To commit changes:"
echo "  cd $NDM_REPO && git status"
echo "  cd $VCP_REPO && git status"
