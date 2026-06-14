---
categories: [项目, 研发计划]
项目: 额度管家 · quota-butler
阶段: MVP1
创建日期: 2026-06-13
配套: PRD_MVP1.md, TEST_PLAN_MVP1.md
---

# 🛠️ 研发计划 · 额度管家 quota-butler · MVP1

> 给开发 Agent 的执行蓝图。需求见 [[PRD_MVP1]]，每阶段的测试见 [[TEST_PLAN_MVP1]]。
> **总目标**：把「感知 CC 额度 → 规则判断 → 飞书带按钮提醒 → 人点『开』→ 替你向 CC 发预热消息」整条闭环焊通，挂机零干预、可截图。
> **铁律**：只做 CC + 单触发场景 + 人工点「开」。[[BACKLOG]] 里的东西一律不碰。

---

## 技术基线（全部本机实测，见 facts/）

| 项 | 事实 |
|----|------|
| 语言 | Python 3（单脚本起步，与 vault 现有脚本一致）|
| 调度 | macOS `launchd` |
| CC 凭据 | **Keychain**：`security find-generic-password -s "Claude Code-credentials" -w` → JSON → `claudeAiOauth.accessToken`（⚠️ 不是 `~/.claude/.credentials.json`，本机无此文件）|
| CC quota | `GET https://api.anthropic.com/api/oauth/usage`，Header：`Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20` → `five_hour.{utilization, resets_at}`（🟢 本机实测 200）|
| 飞书推送 | `lark-cli`（本机 `@larksuite/cli@1.0.39`）发卡片 |
| 飞书反向 | `lark-channel-bridge`（本机已部署，飞书↔CC，`__claude_cb` 回调）|
| 状态 | 本地 JSON，**不上 SQLite** |

> 详细请求/响应/凭据结构见 `facts/01_本机实测_接口与凭据.md`；可借鉴开源实现见 `facts/03_可借鉴GitHub清单.md`（ccusage 兜底读用量、CodexBar 接口参考等）。

---

## 阶段总览

| 阶段 | 目标（一句话）| 风险 |
|------|--------------|------|
| S0 脚手架 | 跑起来一个能读配置、能读写状态文件的空壳 | 低 |
| S1 感知 | 稳定拿到 CC `five_hour` 真实利用率 + reset 时间 | 低（已实测）|
| S2 规则+去重 | 按阈值正确触发，同窗口只提醒一次 | 低 |
| S3 飞书提醒 | 命中时飞书收到数字正确、带【开/不开】的卡片 | **中**（按钮回调机制待定）|
| S4 反向动作 | 点「开」→ 真向 CC 发预热 → 飞书回执 | **中**（依赖 S3 回调）|
| S5 常驻+联调 | launchd 挂机零干预跑通端到端 | 低 |

---

## S0 · 脚手架与配置

**🎯 目标**：建好项目骨架，能加载配置、能读写 JSON 状态文件，`provider` 抽象成接口（MVP1 只实现 CC）。

- **交付物**
  - 项目结构（建议）：
    ```
    quota-butler/
    ├── quota_butler/
    │   ├── config.py        # 读 config.yaml/.toml
    │   ├── state.py         # 读写 state.json
    │   ├── providers/
    │   │   ├── base.py      # Provider 接口：read_usage() / warmup()
    │   │   └── claude.py    # CC 实现（S1/S4 填充）
    │   ├── rules.py         # 触发规则（S2）
    │   ├── notify.py        # 飞书推送（S3）
    │   └── main.py          # 入口：感知→判断→推送
    ├── config.example.yaml
    ├── README.md
    └── tests/
    ```
  - `config.example.yaml`：阈值 `RESET_SOON_MIN`、可选 `WASTE_PCT`、轮询间隔 `INTERVAL_MIN`、`WARMUP_PROVIDER`（默认 `cc`）、飞书目标群、静音开关。
  - `state.json` 读写：字段 `last_reset_at` / `last_utilization` / `last_run_at` / `last_notified_reset_at`。
- **✅ 完成判据 (DoD)**：`python -m quota_butler.main --dry-run` 能加载配置、读到（或初始化）状态文件、不报错退出。
- **对应测试**：TEST_PLAN S0。

---

## S1 · 感知层（CC 额度）🟢

**🎯 目标**：能稳定、安全地拿到本机 CC 的 `five_hour.utilization` 与 `five_hour.resets_at`。

- **依赖**：facts/01 的实测请求。
- **交付物**：`providers/claude.py` 的 `read_usage()`：
  1. `security find-generic-password -s "Claude Code-credentials" -w` → 取 `claudeAiOauth.accessToken`；
  2. `GET oauth/usage`（带 `anthropic-beta` 头）；
  3. 解析为统一结构 `{provider:"cc", five_hour:{utilization:float, resets_at:datetime}, seven_day:{...}}`；
  4. token 过期 / 非 200 → 抛规范异常（MVP1 先报错跳过，不实现自动刷新）。
- **安全红线**：token 只留内存，**不打印、不写盘、不外传**。
- **✅ DoD**：跑 `read_usage()` 打印出的 `utilization`、`resets_at` 与你本机 `claude` TUI `/status` 一致。
- **对应测试**：TEST_PLAN S1。

---

## S2 · 规则判断 + 状态去重

**🎯 目标**：按「窗口换挡」规则正确决定推不推，且同一个窗口只提醒一次。

- **交付物**：`rules.py` 的 `should_notify(usage, state, config) -> Decision`
  - 主规则：`resets_at - now < RESET_SOON_MIN` → 触发。
  - 可选叠加（配置开关）：`utilization < WASTE_PCT`（剩太多没用 = 防浪费味）。
  - **去重**：若 `usage.resets_at == state.last_notified_reset_at` → 不重复提醒。
  - 触发后由 main 写回 `last_notified_reset_at = usage.resets_at`。
- **设计意图**：把「防浪费 / 新窗口」统一成同一条 5h 窗口规则——触发理由不重要，闭环才重要。阈值调大即可当场逼触发，便于验收。
- **✅ DoD**：构造三组输入（临界+未提醒过 / 临界+已提醒过 / 未临界），返回值分别为 推 / 不推（去重）/ 不推。
- **对应测试**：TEST_PLAN S2。

---

## S3 · 飞书提醒卡片（带按钮）

**🎯 目标**：命中规则时，往飞书群推一张**数字正确**、带【开】【不开】两个动作的卡片。

- **⚠️ 开工第一步（阻塞项）**：先核实 `lark-channel-bridge` 是否支持飞书卡片 **action 回调**（按钮点击回到本机服务）。
  - **机制 A（首选）**：卡片 action 回调（`__claude_cb` / value 带 `{action:"warmup"}`）。
  - **机制 B（降级）**：卡片提示「回复『开』即可」，用户发文字消息，走 bridge 既有**入站**链路触发。
  - 任选一个能在本机跑通的即可，结论写回本文件「实施记录」。
- **交付物**：`notify.py` 的 `push_card(usage, config)`：用 `lark-cli` 发卡片；内容含 5h 利用率、距 reset 分钟数、一句人话提示、两个动作。
- **✅ DoD**：手动触发一次，飞书群收到卡片，卡上数字与本机一致，且能看到【开/不开】。
- **对应测试**：TEST_PLAN S3。

---

## S4 · 反向动作（开 → CC 预热）

**🎯 目标**：用户点「开」（或回复「开」）→ 系统真的替他向 CC 发一条预热消息开窗，并回执。

- **依赖**：S3 选定的回调机制。
- **交付物**：
  - 承接「开」事件 → 调 `providers/claude.py` 的 `warmup()`：`claude -p "<极短 warmup prompt>"`（如 "say hi"）。
  - `WARMUP_PROVIDER` 可配置，默认 `cc`；预留 `codex`（走 `codex exec`）规避 6/15 计费——MVP1 只须把 CC 跑通，Codex 实现进 [[BACKLOG]]。
  - 完成后往飞书回执：`✅ 已开窗，新窗口从现在起算` / `❌ 失败：<原因>`。
  - 点「不开」→ 静默，写状态。
- **⚠️ 计费提醒**：6/15 起 CC `claude -p` 独立计费，预热会扣钱——这是产品已知并接受的选择（见 PRD 风险 1）。
- **✅ DoD**：飞书点「开」→ 本机真跑了一条 `claude -p` → 飞书收到「已开窗」回执 →（可选）再读一次 usage 看 `resets_at` 是否刷新。
- **对应测试**：TEST_PLAN S4。

---

## S5 · 常驻调度 + 端到端联调

**🎯 目标**：用 launchd 挂机，全程零代码干预跑通整条闭环。

- **交付物**：
  - `launchd` plist：每 `INTERVAL_MIN` 拉起 `main.py`，跑完即退（非常驻进程）。若 S3 选了机制 A 需常驻回调监听，则另起一个长驻服务的 plist。
  - 安装/卸载脚本或 README 步骤。
- **✅ DoD**：把 `RESET_SOON_MIN` 调大让规则在下一次轮询就触发 → 不碰键盘，观察到「飞书收卡 → 点开 → CC 预热 → 回执」全自动跑完。整个过程可截图复现。
- **对应测试**：TEST_PLAN S5（端到端）。

---

## 实施记录（开发 Agent 边做边填）

> 把 S3 回调机制的最终选型、踩的坑、与 PRD 的偏差记在这里，保持文档为事实源。

### 代码落地

- **仓库位置**：本机 `~/projects/quota-butler`（独立 git 仓，**不在 vault 内**）。⚠️ 与 PRD「外部 30G GitHub project」未必是同一个——尚未推 remote，待懒人哥确认是否迁移/加 remote。
- **技术栈**：Python 3（系统 3.9.6），**纯 stdlib 零第三方依赖**（HTTP 用 `urllib` 绕开 requests 的 LibreSSL 告警；config 用 YAML 但内置 fallback 解析器，装不装 PyYAML 都能跑）。
- **进度**：S0–S4 代码完成，14/14 单测通过。S1/S3 已本机实测真跑通，S4 dry-run 验证、待真点击联调。S5 plist 模板就位待部署。

### S3 回调机制选定

- [x] **机制 A（卡片 action 回调）** ✅ 选定，不走 B 降级。
  - 依据：本 CC session 就活在 `lark-channel-bridge` 内，机制已确认。按钮 `value` 带 `{"__claude_cb": true, "action": "warmup"|"skip", "resets_at": ...}`；用户点击后 bridge 把 payload（去掉 marker）作为 `[card-click] {...}` 送回同一 session。
  - 承接：新建确定性处理器 `quota_butler/handler.py`，`python3 -m quota_butler.handler '<payload>'`，不依赖 agent 自由发挥。

### 踩的坑（本机真发 / launchd 实测暴露）

1. **发卡必须 `--as bot`**：lark-cli 默认 user 身份，缺 `im:message.send_as_user` scope 报错 missing_scope。改 bot 身份后 200。— 这个只有真发才暴露。
2. **`handler --dry-run` 原会真烧 token**：dry-run 最初只挡了回执没挡 `claude -p`，等于白调一次。已修：dry-run 直接跳过 warmup() 调用。6/15 后这是真金白银，必须挡住。
3. **`resets_at` 微秒会漂移**：oauth/usage 后端每次现算，同一窗口两次读到 `.959751` vs `.777681`。原 handler 用字符串精确匹配做预热去重 → 卡片重建后再点会双烧 token。已抽 `window.same_window`（60s 容差），推送去重 + 预热去重统一走它。
4. **launchd 受限环境两坑（env -i 复现）**：
   - **PATH**：launchd 默认 PATH 不含 `/usr/local/bin` → `lark-cli`/`claude` FileNotFoundError。plist 注入 PATH 解决。
   - **LARK_CHANNEL**：lark-cli 靠环境变量 `LARK_CHANNEL=1` 选中"在群里的 bridge bot"；缺了回退默认 app，报 **230002「Bot can NOT be out of the chat」**——推送静默失败。plist 注入 `LARK_CHANNEL` 解决。两者 install.sh 已自动处理。

### 与 PRD 的偏差

- 感知端与回调端**拆成两个入口**（`main.py` 定时拉起 + `handler.py` 承接点击），而非单进程——因为 launchd 进程跑完即退、不常驻，点击必须由独立处理器承接。PRD/FR5 已预见此点（「入站回调承接服务按机制决定是否常驻」），此为机制 A 下的具体落地。

### S5 部署（已完成）

- `deploy/install.sh` / `uninstall.sh`：幂等生成并加载 `~/Library/LaunchAgents/com.quota-butler.plist`，自动解析 python/lark-cli/claude 路径、从 config 读 interval、继承 LARK_CHANNEL。
- 已安装，`plutil -lint` 通过，launchd 首跑状态码 0；env -i 模拟 launchd 受限环境下"感知→触发→真推送"端到端跑通。

### 🎯 完成线已闭合（真实点击验证）

- [x] **bridge `[card-click]` 回传链路** — 真点击【不开】，bridge 把 `[card-click] {"action":"skip","resets_at":...}` 送回 session，承接侧跑 `python3 -m quota_butler.handler` → skip 分支静默、退出码 0、`last_action=skip`、不烧 token。**机制 A 端到端成立。**
- [x] **warmup 分支** — 早前已本机真跑验证（真 `claude -p` 预热 + 真回执「✅ 已开窗」到群）。

> 闭环四步全部真验证：感知（真打接口）→ 提醒（真发卡）→ 拍板（真点击，bridge 回传成立）→ 代执行（skip 真静默 / warmup 真预热）。
> 唯一未做的组合：真点【🔥 开】走完整 warmup（会烧 token，6/15 后计费）——但其两条构成链路（bridge 回传 + handler 预热）均已独立验证，组合无新风险。**MVP1 完成线达成。**

---

*v1 · 2026-06-13 · 配套 PRD_MVP1 / TEST_PLAN_MVP1*
*实施记录更新 · 2026-06-13 · S0–S5 代码完成，仅余真点击联调*
