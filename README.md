# Quota Butler V3

运行在用户 Mac 上、通过飞书交互的 Claude Code / Codex 额度管家。

V3 只做三件事：

1. 查询两个 Agent 的额度与登录状态。
2. 额度窗口恢复时，在 08:00–23:00 之间询问是否立即预热。
3. 根据明天的重度使用时间，生成可调整、可采用的预热计划。

完整产品定义见 [docs/PRD_V3.md](docs/PRD_V3.md)。

## 用户入口

- 飞书文字：`额度`、`查看额度`、`quota`
- 菜单：当前额度、明日计划、查看当前计划
- 每天 22:00：询问明天是否有重度使用计划

明日计划只询问开始时间或时间区间。计划预览会明确展示：

- 工作时间与采用的 AI 工具
- 每次预热的具体时间
- 第一个窗口、第二个窗口或双工具接力的目的
- 采用计划、更换 AI 工具、修改使用时间、仅提醒

## 安全边界

- Token 只在内存中使用，不打印、不提交。
- Codex 401 时最多自动刷新一次。
- Claude 登录失效时只提示 `claude auth login`，不使用预热命令修复登录。
- 点击“立即预热”或“采用计划”即构成最终授权，不再二次确认。
- 计划中的每个 Agent、每个时间节点使用独立 launchd 任务。
- 23:00–08:00 不发送额度恢复提醒。
- 状态文件使用跨进程锁，避免轮询、按钮和定时任务互相覆盖。

## 本地开发

当前实现代码放在：

```text
/Users/earonwong/重要但不同步icloud/02_项目/胡思乱想的项目/quota-butler
```

本机运行状态与配置不进仓库：

- `~/.quota-butler/config.yaml`：额度管家运行配置、主动推送目标。
- `~/.quota-butler/state.json`：额度窗口、计划任务与提醒状态。
- `~/.lark-channel/config.json`：当前 Feishu/Lark bridge profile。
- `~/.lark-channel/profiles/codex/`：`codex` profile 的 bridge 日志、lark-cli 投影配置与本机加密 secret。

```bash
mkdir -p ~/.quota-butler
cp config.example.yaml ~/.quota-butler/config.yaml

python3 -m unittest discover -s tests -v
python3 -m quota_butler.query --dry-run
python3 -m quota_butler.schedule --intent tomorrow --dry-run
```

`--dry-run` 不发送飞书消息，也不会执行真实预热。

## 常驻服务

```bash
bash deploy/install.sh
launchctl list | grep com.quota-butler
```

主任务每隔 `interval_min` 分钟检查一次额度，并在每天 22:00 精确唤醒一次。卡片按钮和文字入口由现有私人 bridge fork 承接，不需要、也不应启动第二个飞书 listener。

安装脚本默认复用 `~/.lark-channel/profiles/codex/lark-cli`，确保 launchd 主动提醒与当前 bridge 使用同一机器人身份。

如果要从旧群切到新的独立机器人，更新的是本机 bridge profile 和加密 keystore，不是仓库代码；App Secret 只能留在本机加密存储里，不应写入 README、YAML、日志或 commit。

私人 bridge 配置与验收见 [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md)。
