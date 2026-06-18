# Quota Butler 私人 bridge fork 安装与验收

## 目标

让飞书卡片 callback 经 bridge 权限检查后直接执行：

```bash
python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml
```

payload 使用 stdin JSON 传入，不经过 shell，也不依赖活跃 Agent run。

私人 fork 会在通过权限校验后，把可信的操作者与会话信息覆盖写入 payload：

```json
{
  "_operator_open_id": "ou_xxx",
  "_chat_id": "oc_xxx"
}
```

Quota Butler 用操作者 `open_id` 隔离首次使用配置。卡片自己携带的同名字段不会被信任，
因此不能冒充其他用户读取或覆盖偏好。

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

规划流程当前为 V3：

1. 新用户先手动填写日常 AI 使用场景，并按操作者保存。
2. 任务类型在手机端使用 2×2 按钮，保留“编码开发 / 内容创作 / 调研分析 / 混合任务”。
3. 强度使用点击按钮，工作时间使用原生时间选择器。
4. 确认卡可点击修改场景、任务、强度或时间，再生成计划。
5. 旧版本规划卡会被拒绝，避免跨版本采用错误计划。

新用户要先加入 bridge 的管理员/访问策略，才能点击 quota 卡片。不要为了开放规划流程
移除这层权限，因为同一命令还包含采用计划和真实 Codex 预热能力。

## 群内验收

1. 发送 `菜单`，打开菜单卡。
2. 点击“帮我安排明天”，首次使用应先看到日常场景输入框。
3. 输入场景并点“保存并继续”，确认四个任务按钮按 2×2 完整显示。
4. 依次点击任务类型、强度，并用时间选择器确认工作时间。
5. 在确认卡分别点击“修改任务 / 修改强度 / 修改时间 / 修改场景”，确认均能返回对应设置。
6. 点击“生成计划”，确认计划卡包含人话时间线、计算依据及“采用计划 / 调整设置 / 仅提醒”。
7. 暂不采用旧计划卡；只对刚生成的 V3 卡继续采用、查看和取消验收。
8. 点击“当前额度”，确认状态卡显示进度条。

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
