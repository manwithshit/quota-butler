# Quota Butler V3 私人 bridge 部署

## 运行方式

现有 `codex` profile 的 launchd 服务负责接收飞书文字与卡片事件。不要再启动 `quota_butler.chat_router --watch`，否则会形成第二个 listener。

bridge 以固定 argv 启动：

```bash
python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml
```

完整 callback payload 通过 stdin JSON 传入，不经过 shell。

需要保留的环境变量：

- `QUOTA_BUTLER_ROOT`
- `QUOTA_BUTLER_PYTHON`
- `QUOTA_BUTLER_CONFIG`

不要把 App Secret、access token 或 OAuth token 写入仓库、日志或聊天。

## 正式入口

bridge 直接识别以下精确文字，不经过 Claude/Codex 对话：

- `额度`
- `查看额度`
- `quota`

三者统一调用 `query_status`。

卡片 callback 使用：

```json
{"cmd":"quota","action":"query_status"}
```

主要 V3 action：

- `query_status`
- `schedule_intent`
- `schedule_flow`
- `adjust_schedule_agents`
- `adjust_schedule_time`
- `adopt_schedule`
- `view_schedule`
- `cancel_schedule`
- `warmup_now`
- `recovery_snooze`
- `scheduled_warmup`

bridge 会使用已验证的操作者和会话覆盖 `_operator_open_id`、`_chat_id`，并把 CardKit 的 `form_value` 转发给 handler。

## 手动验收

1. 在飞书发送“额度”，确认收到 Claude Code / Codex 状态卡。
2. 打开菜单，点击“明日计划”。
3. 选择“从某时开始”，将时间设为 09:00。
4. 确认预览卡明确展示每次预热时间，例如 06:30 和 11:30。
5. 点击“调整时间”，改为 12:00；确认所有节点重新计算，例如 09:30 和 14:30。
6. 两个工具都可用时点击“更换 AI 工具”，确认只有 Claude Code、Codex、两个都用三个选择。
7. 只检测到一个工具时，确认不显示无意义选择器，只显示当前工具与“重新检测”。
8. 点击“采用计划”，确认独立 launchd 任务被创建。
9. 点击“查看当前计划”，确认待执行节点可见；再点击“取消计划”清理任务。

真实预热只应在用户点击“立即预热”或明确采用计划后执行。
