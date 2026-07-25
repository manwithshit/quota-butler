# Quota Butler

[中文](README.md) | English

Quota Butler is a local macOS helper for Claude Code and Codex users. It talks to you through a private Feishu/Lark bot chat, but it does not use an LLM for chat completion. The app only runs deterministic quota checks, status reminders, and warm-up scheduling.

Its goal is to help you make better use of the quota you already have: see 5-hour, 7-day, or monthly windows, know when they recover, plan tomorrow's heavy usage window, and let your Mac warm up the right tool at the right time.

## Current Version: V1.4

V1.4 is the only supported release line. The default `main` branch always points to the current version after automated verification and real-device dogfooding; the project does not maintain a parallel version track.

```bash
npx github:manwithshit/quota-butler run
```

To pin the current version tag or roll back later:

```bash
npx github:manwithshit/quota-butler#v1.4.0 run
```

V1.4 has completed public testing and sustained maintainer dogfooding. It adds weekly-only Codex support, redesigns tomorrow planning around explicit weekly-cycle activation, and strengthens state persistence, warm-up locking, daemon recovery, and network reconnection. Please report reproducible issues, screenshots, and redacted logs through [GitHub Issues](https://github.com/manwithshit/quota-butler/issues).

## Preview

### Quota Status

Quota Butler shows Claude Code and Codex side by side: 5-hour window, 7-day or monthly quota, remaining percentage, and refresh time. It supports the newer weekly-only Codex quota without misclassifying it as a free monthly tier. The status summary prioritizes the long-term cap, so a depleted 7-day quota is shown as the real limit even when the 5-hour window looks full.

### Menu And Current Plan

Send `菜单` or `menu` to open the command card. From there you can query quota, view the current plan, trigger an immediate warm-up, or set tomorrow's plan. Current plans are split into today and tomorrow, with clear states for executed and pending warm-ups.

### Tomorrow Plan

Pick one start time, and Quota Butler prioritizes Claude Code with two 5-hour warm-up points. Weekly-only Codex needs no daily warm-up while its current cycle is active. An adopted plan offers “Append Codex weekly activation” only when the known weekly reset occurs before that plan ends; the scheduled request then sends the first message that starts the new cycle. If Claude Code is unavailable, the planner falls back according to the Codex weekly-cycle state.

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

If your network requires a proxy, set `QUOTA_BUTLER_PROXY` before starting (for example, `http://127.0.0.1:7890`). Without it, the daemon connects directly.

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

## Branch And Release Workflow

The project maintains one public release entry point. Changes are developed and verified on focused branches, then merged into `main` through a pull request. Unverified code does not go directly into `main`.

```text
main          The only public release branch; contains the current verified version.
agent/*       Focused feature or fix branches; merged into main after tests and review.
```

Recommended workflow:

1. Develop on a focused branch and pass `npm test`, `npm run typecheck`, and `npm run build`.
2. Dogfood the candidate on a real device from that branch or a candidate tag.
3. After Feishu messaging, quota recovery, quiet hours, planning, and warm-ups are verified, merge through a pull request and create a version tag.
4. Users install the current verified version from `main`; future versions continue on the same release line.

## Known Limitations

- A sleeping or powered-off Mac can miss scheduled actions. Clearly stale actions are skipped after wake and reported to the owner.
- Codex weekly activation uses the latest reset timestamp known when the plan is created. It does not yet re-query at execution time to detect a cycle that the user started manually.

## Development

```bash
npm install
npm test
npm run typecheck
npm run build
node dist/cli.mjs selftest
```

The test suite covers quota parsing, agent classification, plan generation, plan adoption and cancellation, quiet hours, warm-up receipts, Feishu/Lark card copy, and provider behavior.
