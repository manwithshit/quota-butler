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

## 本机路径

- 项目代码：`/Users/earonwong/重要但不同步icloud/02_项目/胡思乱想的项目/quota-butler`
- 额度管家配置：`~/.quota-butler/config.yaml`
- 额度管家状态：`~/.quota-butler/state.json`
- bridge 根配置：`~/.lark-channel/config.json`
- `codex` bridge profile：`~/.lark-channel/profiles/codex/`
- bridge 日志：`~/.lark-channel/profiles/codex/logs/`
- launchd 定时日志：项目目录下的 `quota-butler.log` 与 `quota-butler.err.log`

## 切换机器人

如果改用独立机器人，优先切换 `~/.lark-channel` 里的 `codex` profile：

1. 将新 App Secret 写入 bridge 的加密 keystore。
2. 将 bridge 根配置和 `lark-cli` 投影配置里的 App ID 同步到新机器人。
3. 清空 `~/.quota-butler/config.yaml` 里的 `feishu.chat_id` / `feishu.user_id`，让私聊自动绑定成为唯一主动提醒目标。
4. 重启 `ai.lark-channel-bridge.bot.codex` 与 `com.quota-butler` 两个 launchd 服务。
5. 查看 bridge 日志，确认 `connected` 事件显示的是新机器人名称。

文字触发的 `额度` 会优先回复 bridge 传入的来源消息 `_message_id`，避免私聊场景里 `chat_id` / `open_id` 被飞书判定为跨 App 或机器人不在会话。bridge 也会把会话类型传给额度管家：只有 `_chat_type = p2p` 的独立机器人私聊会被记录为 `notification_target`，主动恢复提醒和睡前卡会复用这个私聊目标；群聊、话题和其他会话类型不会成为主动提醒目标。

## 正式入口

bridge 直接识别以下精确文字，不经过 Claude/Codex 对话：

- `额度`
- `查看额度`
- `quota`

三者统一调用 `query_status`。

独立机器人模式下，普通文字不进入通用 Codex/Claude 对话。除上述额度查询外，只额外识别菜单入口：

- `菜单` / `帮助` / `menu` / `help` 调用 `menu`

其他普通文字只返回固定提示，不触发大模型、不触发计划流程。计划、预热、取消等操作都从菜单卡片和后续 callback 进入。

卡片 callback 使用：

```json
{"cmd":"quota","action":"query_status"}
```

主要 V3 action：

- `query_status`
- `manual_warmup`
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

bridge 会使用已验证的操作者和会话覆盖 `_operator_open_id`、`_chat_id`、`_chat_type`，并把 CardKit 的 `form_value` 转发给 handler。

## 手动验收

1. 在飞书发送“额度”，确认收到 Claude Code / Codex 状态卡。
2. 检查 `~/.quota-butler/state.json`，确认 `notification_target.chat_type` 为 `p2p`。
3. 在飞书发送“菜单”，确认收到菜单卡。
4. 发送任意非支持文本，例如“明日计划”，确认只收到固定提示。
5. 打开菜单，点击“立即预热”，确认出现可预热工具选择。
6. 打开菜单，点击“设置明日计划”，确认直接进入开始时间选择器。
7. 将开始时间设为 09:00。
8. 确认预览卡明确展示每次预热时间，例如 06:30 和 11:30。
9. 点击“调整时间”，改为 12:00；确认所有节点重新计算，例如 09:30 和 14:30。
10. 两个工具都可用时点击“更换 AI 工具”，确认只有 Claude Code、Codex、两个都用三个选择。
11. 只检测到一个工具时，确认不显示无意义选择器，只显示当前工具与“重新检测”。
12. 点击“采用计划”，确认独立 launchd 任务被创建。
13. 点击“查看当前计划”，确认待执行节点可见；再点击“取消计划”清理任务。

真实预热只应在用户点击“立即预热”或明确采用计划后执行。

计划预热执行后会更新当前计划节点状态：

- `已执行`
- `未执行`
- `失败`

若预热发生在 23:00–08:00，系统只记录结果，不即时打扰；离开免打扰后向独立机器人私聊补发一次完成/失败提醒。若预热发生在非免打扰时段，则即时发送完成/失败提醒。
