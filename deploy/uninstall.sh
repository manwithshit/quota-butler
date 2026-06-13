#!/usr/bin/env bash
# 卸载 quota-butler 的 launchd 定时任务。用法：bash deploy/uninstall.sh
set -euo pipefail

LABEL="com.quota-butler"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "✓ 已卸载 $LABEL（plist 已删除，代码与配置保留）"
else
    echo "ℹ️  未发现 $PLIST，无需卸载"
fi
