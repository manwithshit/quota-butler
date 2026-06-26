# Quota Butler

Quota Butler is a lightweight macOS helper for Claude Code and Codex users.
It checks quota status, plans warm-up times, and sends interactive reminders
through a private Feishu/Lark bot chat.

## What It Does

- Query Claude Code and Codex quota status from Feishu/Lark.
- Show remaining quota percentage and next refresh time.
- Trigger an immediate warm-up when you explicitly choose it.
- Create a next-day warm-up plan from a single start time.
- Track planned warm-ups as `pending`, `executed`, or `failed`.
- Suppress non-urgent reminders during quiet hours and send a summary later.

The bot only uses deterministic commands and card callbacks. It does not send
your messages to a language model for chat completion.

## Feishu/Lark Entry

Supported text commands:

- `额度`
- `查看额度`
- `quota`
- `菜单`
- `帮助`
- `menu`
- `help`

All plan, warm-up, and cancel actions are handled through Feishu/Lark cards.

## Requirements

- macOS with `launchd`
- Python 3.10+
- Claude Code CLI and/or Codex CLI already signed in locally
- `lark-cli` configured for a Feishu/Lark self-built app bot
- A private bot chat with that Feishu/Lark app

App credentials, access tokens, chat IDs, and local state files must stay on the
user's machine. Do not commit them to this repository.

## Quick Start

```bash
git clone <this-repo>
cd quota-butler

mkdir -p ~/.quota-butler
cp config.example.yaml ~/.quota-butler/config.yaml

python3 -m unittest discover -s tests -v
python3 -m quota_butler.query --dry-run

bash deploy/install.sh
launchctl list | grep com.quota-butler
```

`--dry-run` does not send Feishu/Lark messages and does not execute real warm-ups.

## Feishu/Lark Binding

Quota Butler records the private bot chat on first contact:

1. Configure the Feishu/Lark self-built app and `lark-cli` locally.
2. Open the private chat with the bot.
3. Send `额度` or `菜单`.
4. Quota Butler stores that private chat as the notification target in
   `~/.quota-butler/state.json`.

Group chats are ignored as notification targets.

To replay first-contact binding for a demo, back up `~/.quota-butler/state.json`
and remove the `notification_target` field, then send `额度` in the private bot
chat again.

## Background Service

`deploy/install.sh` installs a macOS LaunchAgent named `com.quota-butler`.
After installation, Quota Butler runs in the background; users do not need to
keep a terminal command open.

The Feishu/Lark bridge is a separate background service. Quota Butler assumes
that bridge is already configured to receive bot messages and forward card
callbacks to:

```bash
python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml
```

## Local Files

Runtime files are intentionally outside the repository:

- `~/.quota-butler/config.yaml`
- `~/.quota-butler/state.json`
- `~/.quota-butler/plan-tasks/`
- the local Feishu/Lark CLI profile
- Claude Code and Codex auth files

## Uninstall

```bash
bash deploy/uninstall.sh
```
