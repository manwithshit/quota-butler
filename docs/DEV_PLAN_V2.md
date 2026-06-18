---
categories: [项目, 研发计划]
项目: 额度管家 · quota-butler
阶段: V2
创建日期: 2026-06-17
配套: PRD_V2_SCHEDULER.md, TEST_PLAN_V2.md
---

# 研发计划 · Quota Butler V2

> 目标：把 Quota Butler 从“额度提醒器”升级为“AI Agent Scheduler”。
> 正式主路径必须是飞书卡片按钮闭环；文字 router 只作为测试/兜底。

---

## 当前实现基线

代码已具备：

- `planner.py`：生成单/双 Agent 调度计划，计算 CAS。
- `schedule.py`：生成计划卡。
- `menu.py`：发送测试菜单卡。
- `chat_router.py`：轮询群文字命令并路由到菜单/计划/状态卡。
- `handler.py`：已支持 `schedule_intent` 和 `query_status` callback action。
- `notify.py`：已有状态卡、计划卡、测试菜单卡。

群内实测：

- 文字命令路径可用。
- 卡片按钮展示可用。
- 历史按钮点击失败原因是官方 bridge 已拒绝旧 `__claude_cb` 协议；当前改用
  私人 fork 的 `cmd: quota` 通道。

## 2026-06-18 实施状态

- P1 状态卡视觉升级：已完成。
- P2 人机协作时间线：已完成。
- P3 采用/查看/取消计划与 launchd 任务：已完成。
- P4 主动 one-up、延后、今日静默与去重：已完成。
- P5 文字 router 兜底：已完成并补单元测试。
- P0 bridge：已采用本机私人 fork，`cmd: quota` 权限校验、固定 argv 和 launchd
  常驻已完成；待完成群内逐按钮点击验收。

---

## P0 · Bridge 正式接通

### 目标

解决按钮点击时“目标回调服务当前未在线”。

### 技术路径

使用本机 `lark-channel-bridge-quota` 私人 fork：

```bash
node bin/lark-channel-bridge.mjs start --profile codex
```

注意：

- 本机同时有 `claude` 与 `codex`，必须显式指定 `--agent codex` 或 `--agent claude`。
- 为避免 Claude Code 计费路径，默认先选 `codex`。
- 复用现有 `codex` profile 和加密凭据，不重新读取 app secret。
- app secret 禁止写入文档、日志或聊天。

### 交付物

- bridge 初始化步骤文档。
- 本机可启动的 bridge 命令。
- 按钮 callback 能进入当前会话或本地 handler。
- 菜单按钮：
  - `schedule_intent`
  - `query_status`
  能自动执行。

### 完成判据

- 用户点击“帮我安排明天”按钮，群内自动出现计划卡。
- 用户点击“当前额度”按钮，群内自动出现状态卡。
- 不依赖 `chat_router --watch`。

---

## P1 · 状态卡视觉升级

### 目标

把额度状态从文字百分比升级为可读工作面板。

### 设计

使用 Markdown 兼容进度条作为第一版稳定实现：

```text
Codex
██████░░░░ 63%
窗口：5h
恢复：14:30
状态：正常使用
```

错误状态单独展示：

```text
Claude Code
░░░░░░░░░░ ?
状态：token 已过期
建议：运行一次 claude CLI 刷新登录
```

### 技术实现

- 新增 `usage_bar(percent, width=10)`。
- `build_status_card()` 改为按 Agent 分块展示。
- 状态文案规则：
  - `<30%`：余量充足
  - `30-70%`：正常使用
  - `70-90%`：注意消耗
  - `>=90%`：接近耗尽
  - error：展示失败原因和建议

### 待验证

- 飞书 CardKit 是否有原生 progress 组件。
- 若原生组件稳定可用，再替换 Markdown 进度条。

---

## P2 · 计划卡时间线升级

### 目标

让计划卡表达“人机协作节奏”，而不是只列启动时间。

### 设计

计划卡应包含：

- 总览：模式、工作时间、CAS、预计等待时间
- 时间线：
  - Agent 预热节点
  - Agent 恢复节点
  - 用户创作窗口
  - 人机接力建议

示例：

```text
明日协作节奏 · CAS 100%

06:30  Claude Code 预热
09:00  你开始创作
09:00-11:30  人主导：需求整理 / 编码 / 验证
11:30  Claude Code 恢复，可接力
11:30-14:00  Agent 接力：重构 / 测试 / 文档
14:00  Codex 恢复，可接力
14:00-17:00  深度创作窗口
```

### 技术实现

- 在 `planner.py` 中生成 `PlanEvent` 之外的 human segment。
- 或在 `notify.py` 渲染层根据 event gaps 推导 human segment。
- 第一版优先放在渲染层，避免 planner 过早复杂化。

---

## P3 · 采用计划

### 目标

点击“采用计划”后真正落地本地计划。

### 技术路径

状态写入：

```json
{
  "active_plan": {
    "plan_id": "...",
    "mode": "balanced",
    "agents": ["cc", "codex"],
    "work_start": "...",
    "work_end": "...",
    "events": [...],
    "status": "active"
  }
}
```

执行方式优先级：

1. launchd 生成一次性 warmup 任务。
2. 或长期 router/scheduler 进程按 active plan 执行。

第一版建议用 launchd，符合项目现有部署模型。

### 操作

- `采用计划`
- `查看计划`
- `取消计划`

### 完成判据

- 点击“采用计划”后群里回执 active plan。
- 到点执行 warmup。
- 执行后群里回执。
- 用户可取消尚未执行的计划。

---

## P4 · 主动 one-up 推送

### 目标

当某个 Agent 恢复可用，且用户没有 active plan 时，主动提醒用户接上下一棒。

### 触发条件

- 当前 usage 表明某 Agent 已恢复或可用。
- 没有 active adopted plan。
- 当前不在 quiet_hours。
- 同一窗口未推送过。
- 未被用户“今天不提醒”屏蔽。

### 推送卡

```text
Codex 已恢复，可以 one up 了

当前可用：Codex
Claude Code：token 已过期
建议：现在启动 Codex，保持连续工作

[现在启动] [稍后提醒] [今天不提醒]
```

### 技术实现

- 扩展 `rules.py` 或新增 `scheduler_rules.py`。
- 扩展 `state.py`：
  - `last_oneup_notified_at`
  - `muted_until`
  - `active_plan`
- 新增 `build_oneup_card()`。
- `main.py` 定时轮询时判断并推送。

---

## P5 · 文字 router 降级保留

### 目标

保留群聊文字命令能力，但定位为 fallback。

### 行为

- 识别命令才回复。
- 无关消息静默。
- 同一条消息只处理一次。
- 可作为 bridge 故障时的临时入口。

### 部署

可选 launchd 常驻：

```bash
python3 -m quota_butler.chat_router --watch --interval 3
```

但正式按钮链路跑通后，watch 不应成为唯一生产路径。

---

## 推荐执行顺序

1. P1 状态卡视觉升级
2. P2 计划卡时间线升级
3. P0 bridge 初始化与按钮闭环
4. P3 采用计划
5. P4 主动 one-up 推送
6. P5 router 工程化兜底

理由：

- P1/P2 不依赖 bridge，能马上改善群内体验。
- P0 是正式交互必要项，必须完成。
- P3/P4 依赖按钮闭环和状态模型，放在 bridge 后更稳。
