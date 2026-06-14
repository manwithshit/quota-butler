---
categories: [项目, 测试计划]
项目: 额度管家 · quota-butler
阶段: MVP1
创建日期: 2026-06-13
实测回填: 2026-06-13
配套: PRD_MVP1.md, DEV_PLAN_MVP1.md
---

# 🧪 测试计划 · 额度管家 quota-butler · MVP1

> 与 [[DEV_PLAN_MVP1]] 阶段一一对应。每个阶段写完即测，全绿才进下一阶段。
> **核心可测性技巧**：把 `RESET_SOON_MIN` 阈值调大，就能让「窗口换挡」规则**当场触发**，无需挂机等几小时——这是 MVP1 选用此规则的原因。
>
> **2026-06-13 实测回填**：下表「结果」列为真实跑测结论。图例：✅ 通过 · 🟡 待懒人哥人工确认 · ⬜ 未实测（代码就位）。
> 单测共 **18 个全绿**（`python3 -m unittest discover -s tests`）。

---

## 测试分层

| 层 | 说明 | 范围 |
|----|------|------|
| 单元 | 纯函数 / 模块，可 mock | S0–S2、S4（handler）|
| 集成 | 打真实接口 / 发真飞书 | S1、S3、S4 |
| 端到端 | launchd 挂机全链路 | S5 |

---

## S0 · 脚手架

**🎯 验证目标**：配置与状态文件读写正确，空壳能跑。

| # | 用例 | 期望 | 结果 | 证据 |
|---|------|------|------|------|
| S0-1 | 配置加载 | 各项配置被正确读出，无异常 | ✅ | `--dry-run` 跑通 + `test_config.test_parse_sample`；含无 PyYAML 的 fallback 解析（`test_tiny_yaml_fallback_directly`）|
| S0-2 | 状态文件初始化 | 自动新建合法 JSON，字段齐全 | ✅ | `test_state.test_missing_returns_empty`；main 自动建 state |
| S0-3 | 状态文件读写 | 数值一致、无损坏 | ✅ | `test_state.test_roundtrip`（原子写）|

---

## S1 · 感知层（CC 额度）

**🎯 验证目标**：能拿到真实、正确的 CC `five_hour` 数据，且不泄露 token。

| # | 用例 | 期望 | 结果 | 证据 |
|---|------|------|------|------|
| S1-1 | 读 Keychain | 成功取到 accessToken（不打印明文）| ✅ | `read_usage()` 本机真跑取到 token，全程不打印 |
| S1-2 | 打 usage 接口 | HTTP 200，含 `five_hour.utilization`/`resets_at` | ✅ | 本机真打，多次取到（利用率 20%→47% 随用量变化）|
| S1-3 | **数据对账** | 与 `claude` TUI `/status` 一致 | 🟡 | 脚本读数正常（如 5h 20%、20:00 重置），但**需你本机开 `/status` 比对确认**——我无法替你看 TUI |
| S1-4 | 结构化输出 | `resets_at` 是带时区 datetime，可做时间差 | ✅ | rules 用它算 `minutes_to_reset`，真跑正常 |
| S1-5 | token 过期容错 | 抛规范异常并跳过，不崩溃/不写盘 | ✅ | `test_claude_provider` 故障注入：Keychain 缺失/凭据损坏/token 过期/401/网络错误/缺 five_hour 均抛 `ProviderError`；**过期时断言不发起网络请求**（不带 token 出门）|
| S1-6 | 安全审计 | 全程无 token 明文落盘/落日志 | ✅ | `git grep` 扫库无凭据明文；日志只打利用率/时间，不打 token |

---

## S2 · 规则判断 + 去重

**🎯 验证目标**：触发/不触发判断正确，同窗口不重复打扰。

| # | 用例 | 期望 | 结果 | 证据 |
|---|------|------|------|------|
| S2-1 | 临界且未提醒 | **触发** | ✅ | `test_critical_and_not_notified_yet_notifies` |
| S2-2 | 临界但已提醒（去重）| **不触发** | ✅ | `test_critical_but_already_notified_dedups` + 本机真跑观察到去重 |
| S2-3 | 未临界 | 不触发 | ✅ | `test_not_critical_does_not_notify` |
| S2-4 | 防浪费叠加 | 不触发（用得多）| ✅ | `test_waste_pct_blocks_when_utilization_high` + `_allows_when_low` |
| S2-5 | 阈值可调 | 立即满足，便于验收 | ✅ | 本机把 `reset_soon_min` 调到 600 当场触发 |
| S2-6 | 窗口滚动后复位 | 去重解除，可再触发 | ✅ | `test_window`：不同窗口 `same_window=False`；微秒漂移同窗口 `=True` |

> 📌 S2 顺带修了一个真 bug：`resets_at` 微秒每次现算会漂移（`.959751` vs `.777681`），去重改用 `window.same_window`（60s 容差），避免卡片重建后重复打扰/重复烧 token。

---

## S3 · 飞书提醒卡片

**🎯 验证目标**：飞书真收到数字正确、带按钮的卡片；回调机制可用。

| # | 用例 | 期望 | 结果 | 证据 |
|---|------|------|------|------|
| S3-0 | **回调机制核实**（阻塞）| 定 A 或 B，记入 DEV_PLAN | ✅ | 定 **机制 A**（按钮 `__claude_cb` 回调）。依据：本 session 即活在 bridge 内，机制确定。已记入 DEV_PLAN 实施记录 |
| S3-1 | 卡片送达 | 飞书目标群收到卡片 | ✅ | 真发到群，`message_id` 正常返回 |
| S3-2 | 数字正确 | 利用率%、距 reset 分钟一致 | 🟡 | 卡片数字来自同一次 `read_usage`、内部一致（如 21%/269min）；与 TUI 交叉核对同 S1-3，**待你比对** |
| S3-3 | 按钮齐全 | 含【开】【不开】 | ✅ | 卡 JSON 两个 callback button 齐全 |
| S3-4 | 文案可读 | 一句话看懂为什么提醒 | ✅ | 「5h 窗口即将换挡 / 距重置 N 分钟 / 要现在预热吗」|

> 📌 S3 修了真 bug：发卡必须 `--as bot`，user 身份缺 `im:message.send_as_user` scope。

---

## S4 · 反向动作（开 → CC 预热）

**🎯 验证目标**：点「开」真能驱动本机给 CC 发预热并回执；点「不开」静默。

| # | 用例 | 期望 | 结果 | 证据 |
|---|------|------|------|------|
| S4-1 | 点「开」触发预热 | 本机真执行 `claude -p` | ✅ | 真跑 handler warmup 分支，真 `claude -p "say hi"` 执行，退出码 0 |
| S4-2 | 回执 | 飞书收到「✅ 已开窗」 | ✅ | 回执真发到群 |
| S4-3 | 窗口确认（可选）| `resets_at` 刷新/窗口起算 | ⬜ | 未专门验证（"say hi" 对滚动窗口影响有限），可选项 |
| S4-4 | 点「不开」 | 静默，状态正确写入 | ✅ | **你真点【不开】**，bridge 回传 `[card-click]{skip}` → handler 静默、`last_action=skip`、退出 0、不烧 token |
| S4-5 | provider 可配置 | 走对应分支（非 CC 可报"未实现"）| 🟡 | `warmup_provider` 配置位生效；切 `codex` 现会 `ProviderError("MVP1 只支持 cc")`——符合"未实现"语义，但**未真切换实测** |
| S4-6 | 失败回执 | 飞书收「❌ 失败：原因」，不吞错 | ✅ | `test_handler.test_warmup_failure_sends_error_receipt`：预热抛错 → 返回码 3 + 发「❌」回执 + 不误记已预热（可重试）|

> 📌 S4 修了真 bug：`handler --dry-run` 原会真烧 token（只挡回执没挡预热），已改为 dry-run 跳过 `claude -p`。

---

## S5 · 端到端（挂机零干预）

**🎯 验证目标**：launchd 挂机，全链路自动跑通，可截图复现。

| # | 用例 | 期望 | 结果 | 证据 |
|---|------|------|------|------|
| S5-1 | launchd 拉起 | 到点自动跑 `main.py`，日志可见 | ✅ | install 后 `RunAtLoad` 真跑，状态码 0，`quota-butler.log` 有感知输出，stderr 空 |
| S5-2 | **闭环全程** | 不碰键盘等一次轮询全自动跑完 | 🟡 | 各环节已分别真验（launchd 感知+推送 via env -i、真点击回传、真预热回执），但**未做一次完全无人值守的连续全自动跑**——这步留给你回家挂机观察 |
| S5-3 | 去重防骚扰 | 同窗口只提醒一次 | ✅ | 本机连跑观察到去重生效（含 launchd 路径）|
| S5-4 | 可复现截图 | 截图作为完成证据 | 🟡 | 机制可复现；**截图需你来截**（群里已有多张真卡 + 回执可截）|

> 📌 S5 修了真 bug（env -i 复现 launchd 受限环境）：plist 须注入 PATH（含 `/usr/local/bin`）+ `LARK_CHANNEL`，否则 `lark-cli`/`claude` 找不到 / 报 230002 推送静默失败。install.sh 已自动处理。

---

## ✅ MVP1 总验收清单（= PRD §八）

- [x] 脚本读到 CC `five_hour` 真实利用率 + `resets_at`（S1-2 ✅；S1-3 对账 🟡 待你 `/status` 比对）
- [x] 规则按阈值正确触发/不触发（S2 全绿）
- [x] 同窗口只提醒一次（S2-2 ✅）
- [x] 飞书收到卡片（S3-1 ✅；数字与 TUI 交叉核对 🟡 同 S1-3）
- [x] 卡片带【开/不开】（S3-3 ✅）
- [x] 点「开」→ CC 真收到预热 → 回执「已开窗」（S4-1/2 ✅ 真跑）
- [x] 点「不开」→ 静默（S4-4 ✅ 真点击验证）
- [~] launchd 挂机零干预跑通（S5-1/3 ✅；S5-2 连续无人值守 🟡 待回家挂机）
- [~] 全过程可截图复现（S5-4 🟡 待你截图）

> **结论**：核心闭环（感知→提醒→拍板→代执行）四步**已真实端到端验证**，机制全部成立。单测 **32 个全绿**（含容错故障注入）。
> 剩 3 个 🟡 项都不是"没做"，而是**只能由你完成**：① `/status` 数据对账；② 一次完全无人值守的连续挂机观察；③ 截图存证。
> 容错项已补测：S1-5（坏 token/401/过期不出门）✅、S4-6（失败回执）✅ 均已故障注入覆盖；另顺手修了 `_parse_dt` 格式漂移脆弱点。仅 **S4-3（预热后窗口刷新确认）** 仍为可选未验。

---

*v1 · 2026-06-13 · 实测回填 2026-06-13 · 配套 PRD_MVP1 / DEV_PLAN_MVP1*
