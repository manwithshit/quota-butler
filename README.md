# 额度管家 Quota Butler

中文 | [English](README.en.md)

额度管家是一个运行在 Mac 本地、通过飞书/Lark 私聊交互的 Claude Code / Codex 额度助手。它不接大模型聊天，也不会把你的消息发给模型推理；它只做确定性的额度查询、状态提醒和预热计划编排。

它的目标是让你最大化利用已经拥有的额度：看得见 5 小时窗口、7 天额度或月度额度，知道什么时候恢复，提前安排明天的重度使用时间，并让本机在合适的时间点自动预热。

## 选择版本

### 稳定版（推荐）

稳定版来自默认的 `main` 分支，适合日常长期运行：

```bash
npx github:manwithshit/quota-butler run
```

如需固定安装当前稳定快照或回滚：

```bash
npx github:manwithshit/quota-butler#v0.1.0 run
```

### V1.4 Public Preview（公开测试版）

V1.4 已通过自动化测试和作者设备自测，现开放给更多用户共同验证。它适配了 Codex 仅周额度模式，重新设计了明日计划和新周周期激活逻辑。

```bash
npx github:manwithshit/quota-butler#v1.4.0-beta.1 run
```

公开测试版可能仍存在边界问题，不建议用于绝对不能错过的定时任务。欢迎通过 [GitHub Issues](https://github.com/manwithshit/quota-butler/issues) 提交复现步骤、飞书截图和脱敏日志。

## 功能预览

### 查询额度

同时展示 Claude Code 和 Codex 的 5 小时窗口、7 天额度或月度额度、剩余百分比和刷新时间。支持新版 Codex 的“仅周额度”模式，不会误报为月度免费档。状态判断会优先看长期额度：如果 7 天额度耗尽，即使 5 小时窗口充足，也会提示真正的上限在周额度。

### 飞书菜单与当前计划

发送 `菜单` 后，可以直接在飞书卡片里查询额度、查看当前计划、立即预热、设置明日计划。当前计划会分开展示今日和明日；已执行、未执行、失败、已取消的节点会有明确状态。

### 自动编排明日计划

只需要选择一个开始时间，系统会优先为 Claude Code 生成两次 5 小时窗口预热。Codex 仅周额度处于当前周期时可以直接使用，不再安排每日预热；只有已知重置点早于计划结束时，已采用的计划才会提供“追加 Codex 新周期预热”，在新周期就绪后发送第一条消息开始计时。Claude Code 不可用时，系统会按 Codex 周周期状态生成兜底计划。

## 安装要求

- macOS，支持 `launchd`
- Node.js 20.12+
- 本机已安装并登录 Claude Code CLI 或 Codex CLI
- 一个飞书/Lark 账号

首次运行会在终端显示二维码。用飞书/Lark 扫码后，会自动创建并绑定额度管家的个人机器人，不需要手动去开放平台创建应用，也不需要配置 lark-cli。

## 快速开始

```bash
npx github:manwithshit/quota-butler run
```

首次运行：

1. 终端出现二维码。
2. 用飞书/Lark App 扫码完成应用创建。
3. 打开新建的额度管家机器人私聊。
4. 发送 `额度` 或 `菜单`。

确认能正常收发后，可以让它后台常驻：

```bash
npx github:manwithshit/quota-butler start
npx github:manwithshit/quota-butler status
npx github:manwithshit/quota-butler stop
```

需要代理时，在启动前设置 `QUOTA_BUTLER_PROXY`（例如 `http://127.0.0.1:7890`）；未设置时后台进程保持直连。

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

其它消息会默认回复菜单，方便发现可用操作。项目当前只绑定独立机器人的私聊，不使用群聊作为主动提醒目标。

## 命令一览

```text
quota-butler run        前台运行，首次扫码
quota-butler start      安装并启动 macOS 后台常驻
quota-butler stop       停止后台常驻
quota-butler status     查看后台状态
quota-butler selftest   离线自检，不连接飞书
quota-butler report     预览晚间回顾卡
```

## 本地文件

运行时文件都在仓库外：

```text
~/.quota-butler/config.json
~/.quota-butler/state.json
~/.quota-butler/logs/
Claude Code / Codex 登录文件
```

敏感信息只保存在本机。飞书应用凭证、访问令牌、open_id、chat_id、本地状态文件、Claude Code / Codex 登录信息都不要提交到仓库。

## 分支与部署约定

项目采用稳定版和开发集成版分支分流，避免正在运行的机器被未验证改动直接覆盖。

```text
main          Stable / 稳定版。默认给长期运行机器使用，只合入已验证改动。
DevelopV1.4   Public Preview / 公开测试版。收集真实环境反馈和修复。
agent/*       单项修改分支。通过测试和 PR 后再进入对应发布通道。
```

建议流程：

1. 功能开发在独立分支完成，并通过 `npm test`、`npm run typecheck`、`npm run build`。
2. 合入 Public Preview 后，在真实机器上持续验证并按 beta tag 发布预览版。
3. 确认飞书收发、额度恢复、安静时段和计划/预热都正常后，再通过 PR 合入 `main` 并发布正式版。

部署时不要删除旧版本目录。先 `quota-butler stop` 停止当前守护，再在目标分支 checkout 内构建并 `npm link`，最后 `quota-butler start`。如需回滚，回到旧版本目录重新 `npm link` 并启动。

## Public Preview 已知限制

- Mac 睡眠或关机可能导致定时节点错过；恢复后会跳过明显过时的任务并发送回执。
- Codex 新周期激活基于计划创建时最后读到的重置点；执行前暂不重新查询用户是否已经手动开启周期。
- Public Preview 已通过自动化测试和作者设备自测，但仍需要更多真实账户、网络与睡眠环境共同验证。

## 开发

```bash
npm install
npm test
npm run typecheck
npm run build
node dist/cli.mjs selftest
```

核心逻辑覆盖在测试里：额度状态解析、计划生成、计划采用/取消、静默时段、预热回执、飞书卡片文案和 provider 状态分类。
