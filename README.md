# 额度管家 Quota Butler

中文 | [English](README.en.md)

额度管家是一个运行在 Mac 本地、通过飞书/Lark 私聊交互的 Claude Code / Codex 额度助手。它不接大模型聊天，也不会把你的消息发给模型推理；它只做确定性的额度查询、状态提醒和预热计划编排。

适合经常使用 Claude Code / Codex 的人：你可以在飞书里看到 5 小时窗口和 7 天额度的真实状态，提前安排明天的重度使用时间，并让本机在合适的时间点做预热。

## 功能预览

### 查询额度

同时展示 Claude Code 和 Codex 的 5 小时窗口、7 天额度、剩余百分比和刷新时间。状态判断会优先看长期额度：如果 7 天额度耗尽，即使 5 小时窗口充足，也会提示真正的上限在周额度。

![额度查询](docs/images/quota-status.png)

### 飞书菜单与当前计划

发送 `菜单` 后，可以直接在飞书卡片里查询额度、查看当前计划、立即预热、设置明日计划。当前计划会分开展示今日和明日；已执行、未执行、失败、已取消的节点会有明确状态。

![菜单与当前计划](docs/images/menu-and-current-plan.png)

### 自动编排明日计划

只需要选择一个开始时间，系统会按当前可用额度优先选择一个 AI 工具，并生成两次预热节点。默认策略会尽量吃满单个工具两段 5 小时窗口，形成约 7.5 小时、接近 200% 的重点使用区间。采用计划前，你仍然可以调整两次预热时间。

![明日计划](docs/images/tomorrow-plan.png)

## 现在支持什么

- 在飞书/Lark 私聊里查询 Claude Code 和 Codex 额度。
- 展示 5 小时窗口、7 天额度或月度额度的剩余百分比与刷新时间。
- 当 5 小时窗口 100% 且尚未开始时，提示“发送任意消息时触发”。
- 按周额度优先级选择可规划工具，避免把 7 天额度耗尽的工具排进计划。
- 从一个开始时间生成单 Agent 明日计划，两次预热至少间隔 5 小时。
- 支持查看今日/明日计划，展示每个预热节点的执行状态。
- 支持立即预热，但只在工具确实适合预热时提供入口。
- 支持静默时段，夜间非紧急提醒会延后汇总。
- 通过 macOS LaunchAgent 常驻运行，不需要一直开着命令行窗口。

## 飞书/Lark 入口

支持的文字命令：

```text
额度
查看额度
quota
菜单
帮助
menu
help
```

除了这几个入口命令，其它操作都通过飞书/Lark 卡片按钮完成。项目当前只绑定独立机器人的私聊，不把群聊作为主动提醒目标。

## 安装要求

- macOS，支持 `launchd`
- Python 3.10+
- 本机已登录 Claude Code CLI 或 Codex CLI
- 已配置可用的 `lark-cli`
- 一个飞书/Lark 自建应用机器人私聊

敏感信息只应该保存在本机：飞书应用凭证、访问令牌、chat_id、open_id、本地状态文件、Claude Code / Codex 登录信息都不要提交到仓库。

## 快速开始

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

`--dry-run` 不会发送飞书/Lark 消息，也不会执行真实预热。

## 首次绑定飞书私聊

1. 在本机配置好飞书/Lark 自建应用和 `lark-cli`。
2. 打开这个机器人的私聊。
3. 发送 `额度` 或 `菜单`。
4. 额度管家会把这个私聊记录到 `~/.quota-butler/state.json`，之后主动提醒会发到这里。

如果要重新录制首次绑定流程，可以先备份 `~/.quota-butler/state.json`，移除其中的 `notification_target` 字段，再在机器人私聊里发送 `额度`。

## 后台服务

`deploy/install.sh` 会安装一个 macOS LaunchAgent：

```text
com.quota-butler
```

安装后，额度管家会在后台定时检查额度状态、处理计划任务和发送提醒。普通用户不需要一直运行命令行。

飞书/Lark 消息接收桥是独立服务。额度管家默认它已经能把机器人消息和卡片回调转发到：

```bash
python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml
```

## 本地文件

运行时文件都在仓库外：

```text
~/.quota-butler/config.yaml
~/.quota-butler/state.json
~/.quota-butler/plan-tasks/
本机 lark-cli profile
Claude Code / Codex auth files
```

## 卸载

```bash
bash deploy/uninstall.sh
```

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile quota_butler/handler.py quota_butler/main.py quota_butler/notify.py quota_butler/schedule_flow.py quota_butler/state.py
```

当前核心逻辑都在测试里覆盖：额度状态解析、计划生成、计划采用/取消、静默时段、预热回执、飞书卡片文案和历史状态迁移。
