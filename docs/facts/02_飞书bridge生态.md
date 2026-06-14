# 📦 调研 03：飞书 ↔ CLI Agent Bridge 生态

## 核心项目（按与本任务相关度排序）

### 🟢 S 级（直接相关）

#### 1. **Zara Zhang / feishu-claude-code-bridge**（懒人哥本机已部署 v0.1.31）
- 仓库：`https://github.com/zarazhangrui/feishu-claude-code-bridge`
- 工具名：`lark-channel-bridge`
- 启动：`npx -y lark-channel-bridge@latest run`
- **能力**：
  - 飞书消息 → 本机 CC CLI（`claude -p` 模式）
  - CC 流式输出 → 飞书
  - 多账号 / 多 Workspace / 飞书文档反向操作
  - **0.1.33 新增** Codex CLI 支持 / Telemetry Adapter / profile repin
- **本机状态**：v0.1.31（落后 2 版）
- **可复用**：**最高优先级**——飞书消息接收 + 触发 CC 任务的能力已就绪

#### 2. **Codex 飞书 Bridge 两个独立项目**（dotey 推文提到）
- `github.com/QQQingyu/feishu-codex-bridge`（或 feishu-codex 命名类似）
- `github.com/kxn/codex-remote`（codex-remote 命名）
- 同样模式：飞书消息 ↔ 本机 Codex CLI
- **可复用**：直接 fork 一个用，或参考其 Codex 触发逻辑

### 🟡 A 级（间接相关）

#### 3. **L-x-C/cc-channel**（待 clone 看实现）
- 轻量实现，飞书 ↔ Claude Code
- 可能比 lark-channel-bridge 简单

#### 4. **joewongjc/feishu-claude-code**（待 clone 看实现）
- **Python 实现**（与 lark-channel-bridge Node.js 区分）
- WebSocket 桥接
- 适合懒人哥如果想"轻量 Python 集成"的需求

### 🔴 B 级（背景参考）

#### 5. **dotey/awesome-bridge**（待搜）
- 收集各种"IM ↔ 本机 Agent"项目
- 理解生态

---

## ⚠️ 关键风险（6/15 计费变更）

**Claude 官方公告**：自 **2026 年 6 月 15 日**起，Claude 订阅计划对 `claude -p` 和 Agent SDK 的使用将**独立计费**，**不再走订阅额度**！

- **影响**：
  - `lark-channel-bridge` 走的是 `claude -p` 模式
  - 懒人哥如果用 Claude **官方订阅**，通过 bridge 触发的 CC 任务**6/15 后开始扣 token**
  - 通过 bridge 触发的"prewarm"也可能算额外费用
- **应对**：
  - **Codex 端**走 `codex` CLI 仍然走官方订阅（Codex 暂无此限制）
  - 或者用 **API Key** 走 CC（自费）
  - 或者 bridge 改用 **Codex** 作为唯一触发源
- **Plan 文档必标注**

---

## 🔌 复用方式

| 复用深度 | 说明 |
|----------|------|
| **🟢 浅：直接用 lark-channel-bridge** | 已经部署，无需自己写桥接 |
| **🟡 中：加主动推送模块** | 给 lark-channel-bridge 加 cron 触发 + 飞书 outbound 消息 |
| **🔴 深：fork 改造** | 改成同时支持 CC + Codex + 主动 push |

---

## 📚 参考

- dotey 推文：https://x.com/dotey/status/2058084478459826432
- lark-channel-bridge 仓库：https://github.com/zarazhangrui/feishu-claude-code-bridge
- 本机安装位置：`/usr/local/bin/lark-channel-bridge`（v0.1.31）

---

*记录时间：2026-06-13 00:25（长任务第一波）*
