# Quota Butler 私人 bridge fork 安装与验收

## 目标

让飞书卡片 callback 经 bridge 权限检查后直接执行：

```bash
python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml
```

payload 使用 stdin JSON 传入，不经过 shell，也不依赖活跃 Agent run。

## 本机 fork

```bash
cd /Users/earonwong/重要但不同步icloud/02_项目/胡思乱想的项目/lark-channel-bridge-quota
pnpm ci:local

export QUOTA_BUTLER_ROOT='/Users/earonwong/重要但不同步icloud/02_项目/胡思乱想的项目/quota-butler'
export QUOTA_BUTLER_PYTHON=/usr/bin/python3
export QUOTA_BUTLER_CONFIG=/Users/earonwong/.quota-butler/config.yaml
node bin/lark-channel-bridge.mjs start --profile codex
```

服务复用现有 `codex` profile、机器人和加密凭据。launchd 会固定记录 fork CLI
绝对路径和三个 Quota Butler 环境变量。

安全要求：

- 不把 app secret 写入仓库、聊天或日志。
- 不把 access token 写入 `config.yaml`。
- bridge 使用已经加入 `额度管家提醒群` 的机器人应用。
- `warmup_provider` 必须保持 `codex`，真实验收禁止误触 Claude Code 计费路径。

## Handler 契约

卡片 callback value 形如：

```json
{"cmd":"quota","action":"query_status"}
```

bridge 只接受已授权管理员的 `quota` 命令，以固定 argv 调用 handler。主要 action：

- `schedule_intent`
- `query_status`
- `adopt_schedule`
- `view_schedule`
- `cancel_schedule`
- `oneup_start`
- `oneup_snooze`
- `oneup_mute_today`

## 群内验收

1. 发送 `菜单`，打开菜单卡。
2. 点击“帮我安排明天”，确认返回明日协作时间线。
3. 点击“采用计划”，确认收到任务数量回执。
4. 发送 `查看计划`，确认能看到待执行任务。
5. 点击“取消计划”，确认任务被删除。
6. 点击“当前额度”，确认状态卡显示进度条。

bridge 关闭后，文字 router 仍可临时兜底：

```bash
python3 -m quota_butler.chat_router --watch --interval 3
```

## 回滚到官方 bridge

```bash
cd /Users/earonwong/重要但不同步icloud/02_项目/胡思乱想的项目/lark-channel-bridge-quota
node bin/lark-channel-bridge.mjs stop --profile codex
npx -y lark-channel-bridge@0.3.1 start --profile codex
```

官方 bridge 不识别 `cmd: quota`，回滚后按钮不可用，但文字 router 仍可兜底。
