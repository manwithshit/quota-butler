# 额度管家（Quota Butler）优化报告

评审基线：`main` @ `5ca6a33`（= `DevelopV1.1` @ `5ca6a33`，两分支当前完全一致）
评审日期：2026-07-03
评审范围：`src/` 全部源码、`tests/` 全部测试、README、launchd 部署方式

---

## 0. 分支与发布现状

| 分支 | 提交 | 说明 |
|---|---|---|
| `main` | `5ca6a33` | 已发布的稳定版（PR #3 于 07-03 合入 develop 全部改动） |
| `DevelopV1.1` | `5ca6a33` | 新一轮开发集成分支，暂无增量提交 |

两个分支目前零差异，本报告的全部结论对两者同时成立。由于产品**已经上线运行**，下文优先级按「线上热修 → 下个版本 → 后续迭代」组织，而非「发布前 / 发布后」。

---

## 1. 总体评价

单点工程质量高：恢复检测的容差去重与发送冷却（`poller.ts`）、429 指数退避与 Retry-After 精确退避（`agent_status.ts`）、免费档 Codex 防烧额度闸门（`codex.ts` / `agent_status.ts`）、安静时段消息持久队列（`poller.ts` / `scheduler.ts`），都能看出是踩坑后系统性补强的，且有针对性测试。

当前风险集中在三条**没被踩过的暗线**上：

1. **执行真实性**：预热「先标记已执行、再执行」+ 回执静默吞失败，存在「系统以为成功、实际没执行」的窗口；
2. **持久层健壮性**：`state.json` 非原子写入、损坏后静默清零，全部计划与去重状态可能无感丢失；
3. **承诺与实现落差**：README 与产品口径宣称双 Agent 接力，代码中该路径是死代码 + 一个点了必报错的按钮；Mac 睡眠会让核心场景（清晨预热）静默失效。

---

## 2. 线上热修（P0，建议立即处理）

### P0-1 state.json 非原子写入，损坏后静默清零

- **位置**：`src/state.ts:132-135`（`save()` 直接 `writeFileSync` 覆写）、`src/state.ts:217-224`（`load()` 解析失败静默返回 `defaultState()`）
- **触发**：每个 tick、每次预热、每次回调都会落盘；进程被杀 / 断电 / 磁盘满时写出半个 JSON
- **影响**：已采用计划消失（预热静默不执行）、恢复去重 key 消失（恢复卡重发）、免费档 tier 缓存消失（感知路径可能烧 Codex 月额度），用户全程无感知
- **修复**：临时文件 + `renameSync` 原子替换；`load()` 失败时把坏文件改名保留（`state.json.corrupt-<ts>`），并在下次连上飞书时告知「本地状态已重置」

### P0-2 预热先标记后执行，崩溃/失败后状态失真

- **位置**：`src/scheduler.ts:121-122`（`executedWarmups.push` + `save()` 在 `warmup()` 之前）、`src/notify.ts:695-701`（`eventStatus` 只看 executedWarmups）
- **触发**：① 预热进行中进程崩溃 / Mac 关机 → key 已持久化、预热从未发生、无回执、重启不补；② 预热失败 → 计划卡该节点仍显示「已执行」
- **影响**：「系统以为成功，实际没有执行」的直接实例；用户按计划开工发现窗口没开，卡片却显示一切正常
- **修复**：`executedWarmups` 由 `string[]` 升级为 `Record<key, 'attempted'|'ok'|'fail'|'skip'>`；执行前标 `attempted`，完成后更新；`rearmFromState()` 发现残留 `attempted` 时私聊「上次 XX 预热结果未知，请查额度确认」；计划卡按真实结果渲染 ✅/❌/⏭️

### P0-3 Mac 睡眠时预热直接跳过，无唤醒、无补跑

- **位置**：`src/scheduler.ts:114-119`（超过 5 分钟宽限即跳过）；`src/daemon.ts`（只有 KeepAlive，无 wake 安排）
- **触发**：清晨 06:30 预热正是笔记本最可能合盖睡眠的时刻——这是产品主打场景
- **影响**：计划形同虚设；「已跳过」通知还被安静时段压到 8 点后才送达
- **修复**（近期）：跳过改为有条件补跑——醒来时若仍早于 `work_start` 则补跑并注明迟到时长；（中期）采用计划时用 `pmset schedule wake` 排一次性唤醒、取消时清除，README 写明笔记本需接电源并允许定时唤醒

### P0-4 「两个都用」按钮点了必报错；双 Agent 是未兑现的承诺

- **位置**：`src/notify.ts:378-387`（`buildAgentControlCard` 提供 both 按钮）→ `src/planner.ts:73-75`（both 直接 throw）；`src/notify.ts:495`（`const dual = false;` 后约 30 行接力时间轴为不可达代码）
- **影响**：功能入口存在但必失败，直接消耗用户信任；README 口径与实现不符
- **修复**：本版本做减法——删除 both 按钮与 dual 死代码，文案明确「每个计划只编排一个工具」；双 Agent 接力独立立项（见 §6 第五批）

### P0-5 采用计划时调整预热时间，跨午夜会算错

- **位置**：`src/handler.ts:429-455`（`applyAdoptForm` 的 `setTime` 一律落在 work_start 同一天）；`src/schedule_flow.ts:122-128`（`validateWarmupTimes` 用 `Math.abs` 分钟差）
- **触发**：第一枪 22:00、第二枪 03:01（本意跨午夜 +5h）→ 校验通过但 03:01 落在当天，排序后事件全部错位
- **修复**：以 `work_start` 为锚，早于其 HH:mm 的预热时间判为次日；或将第二枪改为「第一枪 + 5h01m 自动派生」，把两个自由输入降为一个（同时消解 P0-6 一半场景）

### P0-6 傍晚/凌晨开工时间在 point 模式下无法生成计划

- **位置**：`src/schedule_flow.ts:114-120`（`addMinutes` 越界即 throw「默认计划不能跨天」）
- **触发**：开始时间 < 02:30 或 > ~18:29
- **影响**：用户选 19:00 开工只得到一句报错，无解释无出路；晚间重度使用是真实场景
- **修复**：`planner.combine` 已支持绝对时间跨天，把默认预热/结束时间计算从 HH:mm 字符串运算改为基于 `Date` 的绝对时间；跨天节点卡片标「次日」

### P0-7 手动「立即预热」无幂等保护，重复点击重复执行

- **位置**：`src/handler.ts:321-340`（仅有 `window_key` 时去重）、`src/notify.ts:320`（手动预热卡按钮不带 window_key）、`src/run.ts:58`（回调异步无串行化，可并发进入）
- **修复**：进程内按 provider 加「预热进行中」互斥标志，进行中再点回执「预热进行中，请稍候」

---

## 3. 下个版本处理（P1）

| # | 问题 | 位置 | 要点 |
|---|---|---|---|
| P1-1 | launchd plist 固化 `process.argv[1]` 路径，npx 缓存被清后守护静默失效、KeepAlive 反复拉起失败刷日志 | `src/daemon.ts:28` | `start` 时把 dist 复制到 `~/.quota-butler/app/` 再指向；`status` 校验 plist 指向文件是否存在 |
| P1-2 | 用户发「额度」也可能烧免费档 Codex 月额度（主动路径 `detectAgents()` 不带 knownTiers，401 触发 `codex exec` 刷 token） | `src/handler.ts:59` + `src/providers/codex.ts:64-69` | 主动路径也传 knownTiers；已知免费档时改为「确认后刷新」交互 |
| P1-3 | 预热成功但回执发送失败被静默吞掉（空 catch） | `src/scheduler.ts:164-169` | 复用 `pendingQuietMessages` 队列，失败入队下个 tick 重发 |
| P1-4 | 卡片回调内部出错用户零反馈（只 console.error） | `src/run.ts:108-114` | `safe()` 失败时尽力回一条「操作失败，请重试」 |
| P1-5 | 睡前卡在 23:00–23:59 仍发送，与自身安静时段（23–8 点）矛盾 | `src/poller.ts:130` vs `:447` | 收窄 bedtime 窗口到 22:00–22:59 |
| P1-6 | 计划卡承诺的窗口区间与真实窗口机制有偏差（预热落在既有窗口内时，实际关闭时间早于承诺） | 编排逻辑整体 | 预热成功后读一次 usage，把真实 resetsAt 写进回执 |
| P1-7 | `executedWarmups` 是全局数组且 activePlan 过期时一刀切清空，影响并存计划的节点显示；清理逻辑在两处重复 | `src/poller.ts:96-99`、`src/scheduler.ts:65-69` | 随 P0-2 改为带 planId 的 map 后按计划清理 |
| P1-8 | daemon.log 无轮转，常年运行无限增长 | `src/daemon.ts` LOG_DIR | 启动时超阈值轮转 |
| P1-9 | config.json（含飞书 app secret）与 state.json 权限 0644 | `src/config.ts:25`、`src/state.ts:134` | `mode: 0o600` + 对存量文件 chmod |
| P1-10 | 5h 恢复检测条件 `before>5 && current<=5`，上窗口用量 ≤5% 的用户永远收不到恢复卡 | `src/poller.ts:411` | 若为刻意取舍，注释/文档明示，避免日后当 bug 排查 |

---

## 4. 后续优化项（P2）

1. **显式计划状态机**：当前只有 proposed/active 两态；落地 `draft → proposed → adopted → armed → running →（per-event ok/fail/skip）→ completed/cancelled/expired`，计划卡按真实状态渲染。这是双 Agent 的地基。
2. **计划本体不应只存在飞书卡片里**：`buildScheduleCard`（`src/notify.ts:477`）把整个 record 塞进按钮 value，本地只留 `proposedPlanId`；飞书对 value 有大小限制，也意味着「预览中的计划」没有本地真相源。propose 时本地存 record，卡片只带 plan_id。
3. **handler 动作串行化**：按 chatId 的 promise 队列，消灭一整类并发竞态，替代各处 ad-hoc 判重。
4. **健康检查入口**：`lastRunAt` 已记录但从未展示；菜单加一行「守护运行中 · 上次巡检 21:47 · 已布置 2 个预热」。
5. **死代码清理**：`notify.ts` dual 分支与 `fmtDateCn`；`handler.ts` 的 `activePlanIfAny` / `sendExistingPlan`；`poller.ts` 的 legacy 兼容分支可在一两个版本后移除。
6. **CC usage 解析窗口硬编码**（`src/providers/claude.ts:15` 只认 `five_hour`/`seven_day` 字段名）：仿照 Codex 侧按窗口时长动态归类，抵御官方额度规则变化。
7. **预热超时的反向误报**：`claude -p` 120s 超时被杀报「失败」，但请求可能已发出、窗口已开（「以为失败实际成功」）；回执加提示「超时不代表未开窗，可查额度确认」。
8. **「暂时不用」无回执**（`src/handler.ts:167-170`）：点了像没点，补一句确认。

---

## 5. 缺失的关键场景

1. **笔记本合盖过夜**——核心场景当前必然失败（P0-3）。
2. **npx 缓存被清 / 版本升级后守护失联**（P1-1），且没有渠道告知用户「我死了」。
3. **飞书长连接永久断开 / 凭据被回收**：`channel.on('error')` 只打日志；需验证 `@larksuite/channel` keepalive 自愈语义，考虑「连续 N 次发送失败 → 进程自杀让 launchd 拉起」。
4. **额度被其他设备消耗**：另一台机器把周额度用光，本机计划照常预热，计划卡不会提示「计划已不可行」。
5. **系统时区变更 / 出差 / DST**：计划全部用本地 naive ISO（`src/plan_record.ts:103` 刻意为之），adopt 后改时区，触发时刻与卡片显示会漂移，无检测无提示。
6. **state.json 被手工编辑 / 损坏**（P0-1）。
7. **CLI 输出格式随版本变化**：`claude auth status` JSON 解析有宽松回退；`codex exec` 错误过滤（`src/providers/codex.ts:150-165`）是脆弱的文案匹配。
8. **飞书发送限流**：恢复队列有失败留队，但普通回执/卡片发送无退避重试。
9. **同一天先「立即预热」又采用计划**：两条路径互不知晓，无冲突提示。

---

## 6. 迭代顺序与验收标准

### 第一批（热修，改动小且互相独立）

P0-1（原子写 + 损坏备份）、P0-4（删 both 按钮与死代码）、P0-7（预热互斥）、P1-9（文件权限）、P1-5（睡前卡时段）。

**验收**：
- `kill -9` 正在 save 的进程 → 重启后状态完整，或收到「状态已重置」通知且存在 `state.json.corrupt-*`
- 卡片上不再有任何点击必失败的按钮
- 手动预热卡连点 5 次只执行 1 次、只回 1 条回执
- 新建 config/state 权限为 600

### 第二批（执行真实性，四项共改一套数据结构，合并做）

P0-2（executedWarmups 结果 map + 重启对账）、P0-3 近期版（补跑策略）、P1-3（回执失败入队重发）、P1-7（按计划清理）。

**验收**：
- 预热失败节点在计划卡显示 ❌ 而非「已执行」
- 预热进行中 kill 进程 → 重启后收到「结果未知」提醒
- 模拟睡眠（时间拨后 40 分钟）→ 仍在 work_start 前则补跑并注明迟到，否则跳过
- 断网时预热 → 联网后回执自动补达

### 第三批（时间输入正确性，两项根因相同）

P0-5 + P0-6：统一改为「以 work_start 为锚的绝对时间计算」，并决定是否把第二枪改为自动派生（推荐，同时简化 UI）。

**验收**：19:00、01:00 开工均可生成计划；第一枪 22:00 的跨午夜计划，第二枪落在次日 03:01，卡片显示「次日」，定时器在正确绝对时刻触发（fake timers 覆盖）。

### 第四批（部署健壮性与可观测性）

P1-1（安装路径）、P1-8（日志轮转）、P1-2（免费档刷新确认）、P2-4（健康状态入口）。

**验收**：清空 npx 缓存后守护仍能启动；`status` 能发现 plist 指向失效并给出修复指令；菜单显示上次巡检时间。

### 第五批（双 Agent，独立立项）

在显式状态机（P2-1）落地后再做接力/优先级/窗口重叠。`notify.ts` 的 dual 时间轴可复活，但接力点（主工具第二窗口耗尽时刻的估计）依赖先解决 P1-6 的「真实窗口 vs 承诺窗口」偏差，否则接力点必然不准。

---

## 7. 测试补充清单

- **正常流**（当前缺失的集成级）：菜单 → 设明日计划 → 采用 →（fake timers）到点执行 → 回执 → 日报汇总，全链路一条集成测试
- **边界**：跨午夜计划的 arm/fire；含 DST 时区的日期计算；`applyAdoptForm` 跨午夜；point 模式 01:00 / 19:00 开始；5h 与 7 天窗口同刻重置（已有）
- **失败恢复**：state.json 半截 JSON；预热中崩溃后的重启对账；回执发送失败重试；channel 断连行为；429 带/不带 Retry-After（部分已有）
- **并发**：同一卡片双击 adopt / warmup；poller tick 与回调同时写 state
