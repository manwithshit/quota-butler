---
categories: [项目调研, 参考清单]
调研者: 扫地僧
调研日期: 2026-06-13
---

# 🔗 可借鉴 GitHub / 资源清单（站在别人肩膀上）

> 本项目全程涉及的开源项目 + 官方资源，按「能力」归类，标注**可借鉴啥 / 优先级**。
> 图例：⭐=核心复用/必看 · ✅=可直接用/借代码 · 📖=思路参考 · ⚠️=地址待核实

---

## A. 感知额度（能力①：读 CC/Codex 用量与重置时间）

| 项目 | 地址 | 可借鉴 | 优 |
|------|------|--------|----|
| **ryoppippi/ccusage** | https://github.com/ryoppippi/ccusage | 读 CC+Codex 本地 JSONL 用量的事实标准 npm 库，有 `ccusage codex` 子命令、实时 dashboard、MCP 集成。**可直接调，省自己解析** | ⭐✅ |
| **steipete/CodexBar** | https://github.com/steipete/CodexBar | 40+ 供应商 usage（免登录读接口），**Codex `wham/usage` 接口的权威参考实现**（我们 Codex 那半抄它） | ⭐📖 |
| **wesm/vibepulse** | https://github.com/wesm/vibepulse | macOS menubar，基于 ccusage，可配刷新频率 | 📖 |
| **tddworks/ClaudeBar** | https://github.com/tddworks/ClaudeBar | CC/Codex/Antigravity/Gemini 一站聚合 | 📖 |
| **jens-duttke/usage-monitor-for-claude** | https://github.com/jens-duttke/usage-monitor-for-claude | 轻量、可审计、零配置 | 📖 |
| **TylerGallenbeck/claude-code-limit-tracker** | https://github.com/TylerGallenbeck/claude-code-limit-tracker | statusline 实时用量，按模型显示 | 📖 |
| **FruityMaxine/claude-quotas** | https://github.com/FruityMaxine/claude-quotas | CC 插件，给 Claude 自省配额工具，长任务前预警 | 📖 |
| **ClaudeUsageBar** | https://www.claudeusagebar.com/ | 免费开源 macOS，撞限前通知 | 📖 |
| **codex-cli-usage** (PyPI) | https://pypi.org/project/codex-cli-usage/ | Python 读 Codex 用量 | 📖 |

> 💡 两条技术路线：**ccusage 派**=读本地 JSONL 算用量；**CodexBar 派**=直接打官方 usage 接口拿 `utilization`/`resets_at`。我已实测**接口派更准**（resets_at 是后端算好的），但 ccusage 拿来对账/兜底也好。

---

## B. API 中转 + 额度感知（GUI 工具，前序调研提到）

| 项目 | 地址 | 可借鉴 | 优 |
|------|------|--------|----|
| **farion1231/cc-switch** | https://github.com/farion1231/cc-switch | 多供应商统一管理，v3.13+ 自动查官方订阅配额，数据落 `~/.cc-switch/cc-switch.db`（SQLite 可直接读） | 📖 |
| **lbjlaq/Antigravity-Manager** | https://github.com/lbjlaq/Antigravity-Manager | Antigravity 账号管理 + 本地中转 + 额度监控（文本/图片额度、共享池规则） | 📖 |

---

## C. 自动 warm-up / 窗口平移（能力②的「自动」开关）

| 项目 | 地址 | 可借鉴 | 优 |
|------|------|--------|----|
| **tappress/claude-code-warmup** | https://github.com/tappress/claude-code-warmup | Vercel cron + `CLAUDE_CODE_OAUTH_TOKEN`，定时发一条消息锚定窗口 | ⭐📖 |
| **vdsmon/claude-warmup** | https://github.com/vdsmon/claude-warmup | 专做「把 5h 重置挪到你需要的时刻」，窗口平移逻辑 | ⭐📖 |
| **IyadhKhalfallah/clauditor** | https://github.com/IyadhKhalfallah/clauditor | 自动轮换超大会话、保留上下文，防 20 分钟烧光额度 | 📖 |
| **anthropics/claude-code-action** | https://github.com/anthropics/claude-code-action | 官方 GitHub Action，schedule 触发 CC | 📖 |
| **官方 Claude Code Routines** | https://claude.ai/code/routines | Anthropic 云端 cron（V2EX 作者在用，`"5 23,4,9 * * *"` 发 hello） | ⭐📖 |
| **Codex 桌面自动化** | （Codex 桌面 App 内置「无项目自动化 + 间隔触发」） | 官方自带定时触发 | 📖 |

---

## D. 飞书 ↔ CLI 桥接（能力②推送 + 能力③反向触发）

| 项目 | 地址 | 可借鉴 | 优 |
|------|------|--------|----|
| **zarazhangrui/feishu-claude-code-bridge** | https://github.com/zarazhangrui/feishu-claude-code-bridge | 即 `lark-channel-bridge`，**本机已部署 0.1.33**。飞书↔CC/Codex 双向、交互卡片按钮、`__claude_cb` 回调、消息队列、`/stop` 优先指令 | ⭐⭐ 核心复用 |
| **larksuite/cli**（lark-cli） | https://github.com/larksuite/cli | 官方飞书 CLI（本机 `@larksuite/cli@1.0.39`），发卡片/消息就用它 | ⭐✅ |
| **RyanWeb31110/lark-cli-codex-app** | https://github.com/RyanWeb31110/lark-cli-codex-app | Codex 向的飞书本地控制 + Agent Bridge，分发飞书消息到本地 `codex exec` | 📖 |
| **xvirobotics/feishu-claudecode** ⚠️ | https://github.com/xvirobotics/feishu-claudecode | 飞书 Bot ↔ CC，交互卡片实时进度（owner 待核实） | 📖⚠️ |
| **feishu-bridge** (PyPI) | https://pypi.org/project/feishu-bridge/ | 飞书↔CC/Codex，80+ 飞书 API 命令 | 📖 |

> 宝玉/dotey 推荐 Zara 这个项目的推文：https://x.com/dotey/status/2058084478459826432

---

## E. Codex usage 接口逆向 / token 相关参考 issue

| 来源 | 地址 | 价值 |
|------|------|------|
| openai/codex #10869 | https://github.com/openai/codex/issues/10869 | 实锤 `chatgpt.com/backend-api/wham/usage` 端点存在与行为 |
| NousResearch/hermes-agent #15167 | https://github.com/NousResearch/hermes-agent/issues/15167 | Codex token 读取 / credential_pool 的坑 |
| steipete/CodexBar #874 | https://github.com/steipete/CodexBar/issues/874 | Codex OAuth 报错回退 CLI 导致意外 token 消耗（警示） |

---

## F. 社区讨论（需求验证出处，非代码）

- V2EX《利用原生机制自动刷新五小时额度》：https://www.v2ex.com/t/1214103 ← **最直接 precedent**
- OpenAI 社区 Codex 周限额吐槽：https://community.openai.com/t/codex-weekly-limit-dropped-from-96-to-0-in-a-single-day/1381172
- OpenAI 帮助：用 ChatGPT plan 跑 Codex：https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan

---

## 🎯 一句话取舍

> **直接用**：ccusage（兜底读用量）、lark-cli（发卡）、lark-channel-bridge（双向闭环）。
> **抄思路**：CodexBar（Codex 接口）、claude-warmup/vdsmon（窗口平移）、官方 Routines（cron 节奏）。
> **不重复造**：OAuth 刷新（交给 codex CLI）、JSONL 解析（交给 ccusage）。

*完成时间：2026-06-13 凌晨 · 扫地僧*
