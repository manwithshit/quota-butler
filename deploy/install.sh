#!/usr/bin/env bash
# Install Quota Butler as a macOS LaunchAgent.
# Usage: bash deploy/install.sh
#
# This script:
#   1. Resolves python3, lark-cli, claude, and codex paths for launchd.
#   2. Reads interval_min from ~/.quota-butler/config.yaml.
#   3. Generates ~/Library/LaunchAgents/com.quota-butler.plist.
#   4. Reloads the LaunchAgent idempotently.
set -euo pipefail

LABEL="com.quota-butler"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO/deploy/${LABEL}.plist.template"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
CONFIG="$HOME/.quota-butler/config.yaml"

# --- 1. Resolve executable paths -----------------------------------------
PYTHON="$(command -v python3 || true)"
[ -z "$PYTHON" ] && { echo "✗ python3 not found"; exit 1; }

# launchd has a minimal PATH, so include known tool directories explicitly.
collect_dir() { command -v "$1" 2>/dev/null | xargs -I{} dirname {} 2>/dev/null || true; }
DIRS="$(printf '%s\n%s\n%s\n%s\n/usr/local/bin\n/usr/bin\n/bin' \
        "$(dirname "$PYTHON")" "$(collect_dir lark-cli)" "$(collect_dir claude)" \
        "$(collect_dir codex)" \
        | awk 'NF && !seen[$0]++' | paste -sd: -)"

command -v lark-cli >/dev/null 2>&1 || echo "⚠️  lark-cli not found in current shell; message sending may fail"
command -v claude   >/dev/null 2>&1 || echo "⚠️  claude not found in current shell; Claude Code warm-up may fail"
command -v codex    >/dev/null 2>&1 || echo "⚠️  codex not found in current shell; Codex warm-up may fail"

# Preserve lark-cli profile selection for users who rely on it.
LARK_CHANNEL_VAL="${LARK_CHANNEL:-1}"
LARK_CLI_CONFIG_DIR="${LARKSUITE_CLI_CONFIG_DIR:-$HOME/.lark-channel/profiles/codex/lark-cli}"

# --- 2. Read interval -----------------------------------------------------
INTERVAL_MIN=15
if [ -f "$CONFIG" ]; then
    v="$(awk -F: '/^interval_min:/{gsub(/[ \t#].*/,"",$2); print $2}' "$CONFIG" | head -1)"
    [ -n "${v:-}" ] && INTERVAL_MIN="$v"
fi
INTERVAL_SEC=$(( INTERVAL_MIN * 60 ))

# --- 3. Generate plist ----------------------------------------------------
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__REPO__#${REPO}#g" \
    -e "s#__PATH__#${DIRS}#g" \
    -e "s#__LARK_CHANNEL__#${LARK_CHANNEL_VAL}#g" \
    -e "s#__LARK_CLI_CONFIG_DIR__#${LARK_CLI_CONFIG_DIR}#g" \
    -e "s#__INTERVAL__#${INTERVAL_SEC}#g" \
    "$TEMPLATE" > "$PLIST"

# --- 4. Reload LaunchAgent -----------------------------------------------
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✓ Installed $LABEL"
echo "  plist:    $PLIST"
echo "  python:   $PYTHON"
echo "  PATH:     $DIRS"
echo "  lark-cli: $LARK_CLI_CONFIG_DIR"
echo "  interval: ${INTERVAL_MIN} min (${INTERVAL_SEC}s)"
echo "  logs:     $REPO/quota-butler.log / .err.log"
echo
echo "Status:    launchctl list | grep $LABEL"
echo "Uninstall: bash deploy/uninstall.sh"
