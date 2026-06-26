# Quota Butler

[中文](README.md) | English

Quota Butler is a local macOS helper for Claude Code and Codex users. It talks to you through a private Feishu/Lark bot chat, but it does not use an LLM for chat completion. The app only runs deterministic quota checks, status reminders, and warm-up scheduling.

Its goal is to help you make better use of the quota you already have: see the 5-hour and 7-day windows, know when they recover, plan tomorrow's heavy usage window, and let your Mac warm up the right tool at the right time.

## Preview

### Quota Status

Quota Butler shows Claude Code and Codex side by side: 5-hour window, 7-day or monthly quota, remaining percentage, and refresh time. The status summary prioritizes the long-term cap, so a depleted 7-day quota is shown as the real limit even when the 5-hour window looks full.

![Quota status](docs/images/quota-status.png)

### Menu And Current Plan

Send `菜单` or `menu` to open the command card. From there you can query quota, view the current plan, trigger an immediate warm-up, or set tomorrow's plan. Current plans are split into today and tomorrow, with clear states for executed and pending warm-ups.

![Menu and current plan](docs/images/menu-and-current-plan.png)

### Tomorrow Plan

Pick one start time, and Quota Butler selects one available AI tool based on the latest quota state. It generates two warm-up points by default, aiming to make one tool cover around 7.5 hours of focused work, roughly equivalent to two 5-hour windows. You can still adjust the two warm-up times before adopting the plan.

![Tomorrow plan](docs/images/tomorrow-plan.png)

## Requirements

- macOS with `launchd`
- Node.js 20.12+
- Claude Code CLI and/or Codex CLI signed in locally
- A Feishu/Lark account

On first run, Quota Butler prints a QR code in the terminal. Scan it with Feishu/Lark to create and bind a personal bot automatically. You do not need to create a Feishu developer app manually or configure lark-cli.

## Quick Start

```bash
npx github:manwithshit/quota-butler run
```

First run:

1. Scan the QR code shown in the terminal.
2. Open the newly created Quota Butler bot chat.
3. Send `额度` or `菜单`.
4. Confirm that the bot replies.

After the foreground run works, install the background daemon:

```bash
npx github:manwithshit/quota-butler start
npx github:manwithshit/quota-butler status
npx github:manwithshit/quota-butler stop
```

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

Other messages fall back to the menu. The product is designed for a private bot chat only; group chats are not used as proactive notification targets.

## CLI

```text
quota-butler run        Foreground run and first QR setup
quota-butler start      Install and start the macOS background daemon
quota-butler stop       Stop the daemon
quota-butler status     Show daemon status
quota-butler selftest   Offline self-test without Feishu/Lark
quota-butler report     Preview the daily report card
```

## Local Files

Runtime files live outside the repository:

```text
~/.quota-butler/config.json
~/.quota-butler/state.json
~/.quota-butler/logs/
Claude Code / Codex auth files
```

Keep secrets local. Feishu/Lark app credentials, access tokens, open IDs, chat IDs, local state, and Claude Code / Codex auth files should never be committed to this repository.

## Development

```bash
npm install
npm test
npm run typecheck
npm run build
node dist/cli.mjs selftest
```

The test suite covers quota parsing, agent classification, plan generation, plan adoption and cancellation, quiet hours, warm-up receipts, Feishu/Lark card copy, and provider behavior.
