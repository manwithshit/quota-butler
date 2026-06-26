# Quota Butler

[中文](README.md) | English

Quota Butler is a local macOS helper for Claude Code and Codex users. It talks to you through a private Feishu/Lark bot chat, but it does not use an LLM for chat completion. The app only runs deterministic quota checks, status reminders, and warm-up scheduling.

It is designed for people who frequently hit Claude Code / Codex usage windows. You can check the real 5-hour and 7-day quota state from Feishu/Lark, plan tomorrow's heavy usage window, and let your Mac prepare warm-ups at the right time.

## Preview

### Quota Status

Quota Butler shows Claude Code and Codex side by side: 5-hour window, 7-day quota, remaining percentage, and refresh time. The status summary prioritizes the long-term cap, so a depleted 7-day quota is shown as the real limit even when the 5-hour window looks full.

![Quota status](docs/images/quota-status.png)

### Menu And Current Plan

Send `菜单` or `menu` to open the command card. From there you can query quota, view the current plan, trigger an immediate warm-up, or set tomorrow's plan. Current plans are split into today and tomorrow, with clear task states for executed, pending, failed, or canceled warm-ups.

![Menu and current plan](docs/images/menu-and-current-plan.png)

### Tomorrow Plan

Pick a start time, and Quota Butler selects one available AI tool based on the latest quota state. It generates two warm-up points by default, aiming to make one tool cover around 7.5 hours of focused work, roughly equivalent to two 5-hour windows. You can still adjust the two warm-up times before adopting the plan.

![Tomorrow plan](docs/images/tomorrow-plan.png)

## Features

- Query Claude Code and Codex quota from a private Feishu/Lark bot chat.
- Show 5-hour, 7-day, or monthly windows with remaining percentage and refresh time.
- Explain a fresh 100% 5-hour window as "triggered by sending any message" when no refresh time exists yet.
- Rank schedulable tools by weekly quota first, so a depleted 7-day quota is not planned accidentally.
- Generate a single-agent tomorrow plan from one start time.
- Require at least 5 hours between the two warm-up points.
- Show today and tomorrow plans with per-node execution status.
- Offer immediate warm-up only when the selected tool is actually suitable for warm-up.
- Respect quiet hours and delay non-urgent reminders.
- Run in the background through a macOS LaunchAgent.

## Feishu/Lark Entry

Supported text commands:

```text
额度
查看额度
quota
菜单
帮助
menu
help
```

All other actions are handled by Feishu/Lark card buttons. The current product is designed for a private bot chat only; group chats are not used as proactive notification targets.

## Requirements

- macOS with `launchd`
- Python 3.10+
- Claude Code CLI and/or Codex CLI already signed in locally
- A working `lark-cli` setup
- A private chat with a Feishu/Lark self-built app bot

Keep secrets local. Feishu/Lark app credentials, access tokens, chat IDs, open IDs, local state, and Claude Code / Codex auth files should never be committed to this repository.

## Quick Start

```bash
git clone https://github.com/manwithshit/quota-butler.git
cd quota-butler

mkdir -p ~/.quota-butler
cp config.example.yaml ~/.quota-butler/config.yaml

python3 -m unittest discover -s tests -v
python3 -m quota_butler.query --dry-run

bash deploy/install.sh
launchctl list | grep com.quota-butler
```

`--dry-run` does not send Feishu/Lark messages and does not execute real warm-ups.

## First Private Chat Binding

1. Configure the Feishu/Lark self-built app and `lark-cli` locally.
2. Open the private chat with the bot.
3. Send `额度` or `菜单`.
4. Quota Butler stores that private chat in `~/.quota-butler/state.json`; future proactive reminders use this target.

To replay the first-contact flow for a demo, back up `~/.quota-butler/state.json`, remove the `notification_target` field, and send `额度` again in the private bot chat.

## Background Service

`deploy/install.sh` installs this macOS LaunchAgent:

```text
com.quota-butler
```

After installation, Quota Butler runs in the background to check quota state, reconcile plan tasks, and send reminders. Users do not need to keep a terminal command running.

The Feishu/Lark message bridge is a separate service. Quota Butler assumes it can forward bot messages and card callbacks to:

```bash
python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml
```

## Local Files

Runtime files live outside the repository:

```text
~/.quota-butler/config.yaml
~/.quota-butler/state.json
~/.quota-butler/plan-tasks/
local lark-cli profile
Claude Code / Codex auth files
```

## Uninstall

```bash
bash deploy/uninstall.sh
```

## Development And Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile quota_butler/handler.py quota_butler/main.py quota_butler/notify.py quota_butler/schedule_flow.py quota_butler/state.py
```

The test suite covers quota parsing, agent classification, plan generation, plan adoption and cancellation, quiet hours, warm-up receipts, Feishu/Lark card copy, and legacy state migration.
