---
categories: [项目, PRD]
项目: 额度管家 · quota-butler
阶段: MVP1
状态: 待开工
创建日期: 2026-06-13
事实依据: facts/01_本机实测_接口与凭据.md, facts/02_飞书bridge生态.md
---

# 📄 PRD · 额度管家 quota-butler · MVP1

> 本文是 MVP1 唯一的「事实源」。开工照此走；调研里与本文冲突的，以本文为准。
> 代码仓库在外部 30G GitHub project（Cursor 远程写），本文档留在 Obsidian 作产品事实源。

---

## 一、一句话定义

> **在 Mac 上常驻一个哨兵：定时读 Claude Code 真实额度 → 命中规则 → 往飞书推一张带【开 / 不开】按钮的卡片 → 用户点「开」→ 系统替他向 CC 发一条预热消息开窗。**

它不是"显示一个数字"的看板，而是一个**会替你拍板动手的管家**：感知 → 提醒 → 你拍板 → 代执行，闭环。

---

## 二、MVP1 目标 & 完成线（验收）

**目标**：把 **「感知 → 提醒 → 你拍板 → 代执行」整条闭环**焊通。这是这个工具的灵魂——纯单向提醒证明不了它的价值（它能**替你动手**）。

> [!check] 完成线（能演示 = done）
> 挂机、零代码干预，当 CC 的 5h 窗口命中触发规则时：
> 1. 飞书群准时收到一张卡片，卡上**额度 % 与 reset 时间和本机实际对得上**；
> 2. 卡片带【开 / 不开】两个动作；
> 3. 点「开」后，系统**真的替你向 CC 发了一条预热消息**，并回执"已开窗"；
> 4. 点「不开」则静默。
>
> **能把整个过程截图发出去 = MVP1 完成。**

---

## 三、范围

### ✅ MVP1 做

| # | 能力 | 说明 |
|---|------|------|
| 1 | 感知 CC 额度 | 读 Keychain token → 打 `oauth/usage`，拿 `five_hour` 利用率 + `resets_at` |
| 2 | 规则判断 | 单次快照即可计算的"窗口换挡"规则（阈值可配，见 FR2） |
| 3 | 飞书提醒卡片 | 主动 outbound 推送，卡片带【开 / 不开】 |
| 4 | 反向动作：开 | 用户点「开」→ 替他向 CC 发一条预热消息（warm-up 开窗）|
| 5 | 常驻调度 | launchd 定时拉起脚本 |
| 6 | 状态文件 | 一个 JSON 记上次状态，做去重 / 防重复打扰 |

### ❌ MVP1 明确不做（进 BACKLOG）

- ❌ Codex 接入（MVP1 只跑 CC；provider 抽象成接口留扩展位）
- ❌ 第二种触发场景（防浪费 / 新窗口的另一面）
- ❌ 自动预热（必须人点「开」才动）
- ❌ SQLite 重状态机（JSON 够用）
- ❌ 多档 snooze / 复杂调度策略
- ❌ Antigravity / Gemini 等其它 provider

---

## 四、核心流程（闭环四步）

```
[launchd 每 N 分钟]
      │
      ▼
① 感知   读 Keychain CC token → GET oauth/usage → {five_hour.utilization, resets_at}
      │
      ▼
② 判断   命中"窗口换挡"规则？  ── 否 ──▶ 写状态文件，退出
      │ 是
      ▼
③ 提醒   往飞书推卡片：「5h 窗口 23 分钟后重置，已用 18%。要预热下一个窗口吗？」[开][不开]
      │
      ▼
④ 拍板   用户点击
         ├─「开」 ▶ 替你向 CC 发一条预热消息 → 回执"✅ 已开窗，新窗口从现在起算"
         └─「不开」▶ 静默，写状态文件
```

---

## 五、功能需求（FR）

### FR1 · 感知 CC 额度 🟢（已实测跑通）

- 凭据：`security find-generic-password -s "Claude Code-credentials" -w` → JSON → `claudeAiOauth.accessToken`
  - ⚠️ 纠偏：CC 凭据在 **Keychain**，不在 `~/.claude/.credentials.json`（本机无此文件）
- 请求：`GET https://api.anthropic.com/api/oauth/usage`
  - Header：`Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20`
- 取用字段：
  - `five_hour.utilization`（0–100 已用百分比）
  - `five_hour.resets_at`（ISO8601 绝对时间戳，**直接读，不要自己按"首条消息+5h"推**）
- Token 过期处理：`expiresAt` 到期后需 fallback（CC CLI 在用时会自动刷新 Keychain；工具独立跑时若过期，MVP1 先简单报错/跳过，刷新策略进 BACKLOG）

### FR2 · 规则判断（窗口换挡）

- **规则（MVP1 占位，阈值全可配）**：
  `resets_at - now < RESET_SOON_MIN`（窗口即将重置）→ 触发提醒。
- 设计意图：把"防浪费"与"新窗口"统一成同一条 5h 窗口规则——**触发理由不重要，闭环才重要**。阈值调大即可当场逼它触发，便于验收。
- 可选叠加条件（配置开关）：`five_hour.utilization < WASTE_PCT`（剩太多没用 = 防浪费味）。
- **去重**：同一个 `resets_at` 窗口只提醒一次（靠状态文件记录上次提醒的 `resets_at`）。

### FR3 · 飞书提醒卡片（带按钮）

- 复用 `lark-channel-bridge` 生态，**新增"主动 outbound 推送"模块**（cron 触发 → 发飞书卡片）。
- 卡片内容：当前 5h 利用率、距 reset 时间、一句人话提示。
- 交互：两个动作【开】【不开】。
  - ⚠️ **技术待验证（留给技术方案）**：`lark-channel-bridge` 当前是入站（飞书→CC）；按钮**回调**能否接住需核实。两条候选机制：(a) 飞书卡片 action 回调到本机服务；(b) 退而求其次——卡片提示"回复『开』"，用户发文字消息，走 bridge 既有入站链路。MVP1 任选其一能跑通即可。

### FR4 · 反向动作：开 → 预热

- 用户点「开」（或回复「开」）→ 系统向 **CC** 发一条预热消息。
- 机制：`claude -p "<warmup prompt>"`（bridge 既有能力）。
- 回执：往飞书回一条"✅ 已开窗 / ❌ 失败原因"。
- **Provider 可配置**：`WARMUP_PROVIDER`，默认 `cc`，可切 `codex` 规避 6/15 计费（见风险）。

### FR5 · 常驻调度

- macOS `launchd`：每 `INTERVAL_MIN`（默认 ~5 分钟）拉起一次 Python 脚本，跑完即退（非常驻进程）。
- 入站按钮回调的承接服务按 FR3 选定机制决定是否需要常驻监听。

### FR6 · 状态文件

- 一个 JSON（如 `~/.quota-butler/state.json`）记：上次提醒的 `resets_at`、上次利用率、上次运行时间。
- 用途：去重、防重复打扰。**不上 SQLite**。

---

## 六、非功能需求

| 维度 | 要求 |
|------|------|
| 安全 | token 只留内存 / 读后即用，**不打印、不外传、不写盘**（除非脱敏）|
| 形态 | 轻量：launchd + Python 脚本 + 1 个 JSON。砍掉一切过度设计 |
| 可配置 | 阈值、间隔、provider、飞书目标群，全部走配置文件 |
| 代码质量 | 自用优先，但代码/文档写干净，方便顺手开源 + 出小红书内容 |
| 可扩展 | provider（感知 + 预热）抽象成接口，留 Codex / 其它扩展位 |

---

## 七、已知约束与风险

> [!warning] 风险 1 · 6/15 计费红线（最高优先）
> 自 **2026-06-15** 起，Claude 订阅对 `claude -p` / Agent SDK **独立计费、不走订阅额度**。
> 本 MVP「开」= `claude -p` 预热，**6/15 后会扣 token / 产生费用**。
> **缓解**：`WARMUP_PROVIDER` 可配置，切 `codex` 即走订阅、不计费。默认 CC 是你本轮的明确选择，知情即可。

- **风险 2 · CC token 过期**：独立跑时 token 可能过期，MVP1 先报错跳过，自动刷新进 BACKLOG。
- **风险 3 · 飞书按钮回调能力未验证**：见 FR3，写技术方案前必须先核实 `lark-channel-bridge` 是否支持卡片 action 回调；不支持则走"回复『开』"降级方案。

---

## 八、验收清单（Checklist）

- [ ] 脚本能读到本机 CC `five_hour` 真实利用率 + `resets_at`
- [ ] 规则按阈值正确触发 / 不触发（调大阈值可当场逼触发）
- [ ] 同一窗口只提醒一次（去重生效）
- [ ] 飞书群收到卡片，数字与本机实际一致
- [ ] 卡片带【开 / 不开】两个动作
- [ ] 点「开」→ CC 真的收到预热消息 → 飞书回执"已开窗"
- [ ] 点「不开」→ 静默
- [ ] launchd 挂机一段时间，全程零代码干预
- [ ] 整个过程可截图复现

---

## 九、部署形态

- 跑在 Mac 上的轻量常驻服务：`launchd 定时 + Python 脚本 + JSON 状态文件`。
- 代码仓库：外部 30G GitHub project（Cursor 远程写）。
- 产品文档：本文件（Obsidian `30_Projects/CC和Codex额度刷新提醒器/`）。

---

## 十、后续（Phase 2 +）

MVP1 跑通后再处理，详见 [[BACKLOG]]：Codex 接入、第二触发场景、自动预热、token 自动刷新、多档 snooze、其它 provider 扩展、token 刷新走 `codex exec` 一箭双雕等。

---

*v1 · 2026-06-13 · 事实源：扫地僧本机实测报告*
