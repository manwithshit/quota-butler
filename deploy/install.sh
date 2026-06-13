#!/usr/bin/env bash
# 安装 quota-butler 的 launchd 定时任务。
# 用法：bash deploy/install.sh
#
# 它做的事：
#   1. 解析 python3 / lark-cli / claude 的真实路径，拼出 launchd 需要的 PATH
#   2. 从 ~/.quota-butler/config.yaml 读 interval_min（没有就默认 5 分钟）
#   3. 用模板生成 ~/Library/LaunchAgents/com.quota-butler.plist
#   4. load 进 launchd（先 unload 旧的，幂等）
set -euo pipefail

LABEL="com.quota-butler"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO/deploy/${LABEL}.plist.template"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
CONFIG="$HOME/.quota-butler/config.yaml"

# --- 1. 解析可执行路径 ----------------------------------------------------
PYTHON="$(command -v python3 || true)"
[ -z "$PYTHON" ] && { echo "✗ 找不到 python3"; exit 1; }

# launchd PATH：把 python3 / lark-cli / claude 所在目录都并进去 + 系统默认
collect_dir() { command -v "$1" 2>/dev/null | xargs -I{} dirname {} 2>/dev/null || true; }
DIRS="$(printf '%s\n%s\n%s\n/usr/local/bin\n/usr/bin\n/bin' \
        "$(dirname "$PYTHON")" "$(collect_dir lark-cli)" "$(collect_dir claude)" \
        | awk 'NF && !seen[$0]++' | paste -sd: -)"

command -v lark-cli >/dev/null 2>&1 || echo "⚠️  当前 shell 找不到 lark-cli —— 推送会失败，确认它在 $DIRS 里"
command -v claude   >/dev/null 2>&1 || echo "⚠️  当前 shell 找不到 claude —— 预热会失败"

# lark-cli 靠 LARK_CHANNEL 选中"在群里的 bridge bot"；从当前 shell 继承，默认 1
LARK_CHANNEL_VAL="${LARK_CHANNEL:-1}"

# --- 2. 读 interval（分钟 → 秒）------------------------------------------
INTERVAL_MIN=5
if [ -f "$CONFIG" ]; then
    v="$(awk -F: '/^interval_min:/{gsub(/[ \t#].*/,"",$2); print $2}' "$CONFIG" | head -1)"
    [ -n "${v:-}" ] && INTERVAL_MIN="$v"
fi
INTERVAL_SEC=$(( INTERVAL_MIN * 60 ))

# --- 3. 生成 plist --------------------------------------------------------
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__REPO__#${REPO}#g" \
    -e "s#__PATH__#${DIRS}#g" \
    -e "s#__LARK_CHANNEL__#${LARK_CHANNEL_VAL}#g" \
    -e "s#__INTERVAL__#${INTERVAL_SEC}#g" \
    "$TEMPLATE" > "$PLIST"

# --- 4. load（幂等）------------------------------------------------------
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✓ 已安装 $LABEL"
echo "  plist:    $PLIST"
echo "  python:   $PYTHON"
echo "  PATH:     $DIRS"
echo "  间隔:     ${INTERVAL_MIN} 分钟（${INTERVAL_SEC}s）"
echo "  日志:     $REPO/quota-butler.log / .err.log"
echo
echo "查看状态：  launchctl list | grep $LABEL"
echo "卸载：      bash deploy/uninstall.sh"
