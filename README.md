# 额度管家 · quota-butler

> 跑在 Mac 上的轻量哨兵：**感知 Claude Code 额度 → 规则判断 → 飞书带按钮提醒 → 人点「开」→ 替你向 CC 发预热消息。**

闭环四步：感知 → 提醒 → 你拍板 → 代执行。不是看板，是会替你动手的管家。

产品事实源（PRD / 研发计划 / 测试计划）在 Obsidian：`30_Projects/额度管家-quota-butler/`。

## 设计要点

- **零第三方依赖**：纯 stdlib（`subprocess` + `urllib`）。装了 PyYAML 用它，没装也能跑。
- **感知**：读 macOS Keychain 里的 CC oauth token → 打 `oauth/usage` → 取 `five_hour`。
- **token 安全**：只留内存，不打印、不写盘、不外传。
- **状态**：一个 JSON 做去重，不上 SQLite。
- **回调机制 A**：飞书卡片按钮 `value` 带 `{"__claude_cb": true}`，经 `lark-channel-bridge` 回调到 CC session（已确认支持）。

## 目录

```
quota-butler/
├── quota_butler/
│   ├── config.py            # 配置（YAML，带零依赖 fallback）
│   ├── state.py             # JSON 状态读写
│   ├── providers/
│   │   ├── base.py          # Provider 接口 + Usage 数据结构
│   │   └── claude.py        # CC 实现：read_usage() / warmup()
│   ├── rules.py             # 触发规则 + 去重
│   ├── window.py            # 窗口同一性判断（resets_at 微秒漂移容差，共享）
│   ├── notify.py            # 飞书卡片 + 回执（lark-cli, --as bot）
│   ├── main.py              # 感知端入口：感知→判断→推送
│   └── handler.py           # S4 回调处理器：承接点击→预热→回执
├── config.example.yaml
├── deploy/com.quota-butler.plist  # launchd 模板
└── tests/
```

## 快速开始

```bash
# 1. 配置
mkdir -p ~/.quota-butler
cp config.example.yaml ~/.quota-butler/config.yaml
# 编辑 feishu.chat_id

# 2. 实测感知层（不发飞书）
python3 -m quota_butler.main --dry-run

# 3. 跑测试
python3 -m unittest discover -s tests -v
```

## 命令

| 命令 | 作用 |
|------|------|
| `python3 -m quota_butler.main` | 正常一轮：感知→判断→推送 |
| `... --dry-run` | 不真发飞书，只打印决策与卡片 JSON |
| `... --force` | 忽略阈值强制命中（联调用）|
| `... --config PATH` | 指定配置文件 |
| `python3 -m quota_butler.handler '<payload>'` | S4：承接卡片点击 → 预热 → 回执 |
| `... handler --dry-run '<payload>'` | 模拟点击，**不真烧 token、不真发飞书** |

## S4 · 回调闭环（点「开」→ 预热）

感知端（`main.py`）是 launchd 定时拉起、跑完即退的进程，**不常驻**，所以按钮点击
不会落回它。回调走 `lark-channel-bridge` 反向链路：

```
用户点【🔥 开】
  → 飞书 → lark-channel-bridge
  → bridge 把 [card-click] {action:"warmup", resets_at, ...} 送进一个 CC session
  → 承接侧执行：python3 -m quota_butler.handler '<payload-json>'
  → handler：warmup → claude -p 预热 → 飞书回执「✅ 已开窗」
             skip   → 静默写状态
```

**集成契约**（给 bridge / 承接 agent）：收到 `[card-click] {...}`（bridge 已去掉
`__claude_cb` marker）时，把那段 JSON 原样作为参数调 `python3 -m quota_butler.handler`。

**防重复**：同一个 `resets_at` 窗口若已预热（`state.last_warmed_reset_at` 命中），
再点「开」只回「该窗口已开过」，不重复烧 token。

## 阶段进度（MVP1）

- [x] S0 脚手架与配置
- [x] S1 感知层（CC 额度）— 本机实测跑通
- [x] S2 规则判断 + 去重
- [x] S3 飞书卡片（机制 A：`__claude_cb` 回调）— 本机真发到群验证
- [x] S4 反向动作（开 → 预热）— handler 真跑验证（预热+回执），待真点击验回传
- [x] S5 launchd 常驻 — 已安装，launchd 受限环境下感知+推送实测跑通

## S5 · launchd 部署

```bash
bash deploy/install.sh      # 生成并加载 ~/Library/LaunchAgents/com.quota-butler.plist
bash deploy/uninstall.sh    # 卸载（保留代码与配置）
launchctl list | grep com.quota-butler   # 看状态（第二列=上次退出码）
tail -f quota-butler.log    # 看运行日志
```

**launchd 环境两个必填坑（本机 env -i 实测复现）**，install.sh 已自动处理：

| 坑 | 现象 | 解法 |
|----|------|------|
| PATH 不含 `/usr/local/bin` | `lark-cli`/`claude` FileNotFoundError | plist 注入 PATH |
| 缺 `LARK_CHANNEL` | lark-cli 回退默认 app，报 230002「Bot can NOT be out of the chat」 | plist 注入 `LARK_CHANNEL`（默认 1）|

> 改了 `config.yaml` 的 `interval_min` 后需重跑 `install.sh` 才生效（间隔写死在 plist）。

## ⚠️ 红线

1. **6/15 起 CC `claude -p` 独立计费**，预热会扣钱。`warmup_provider: codex` 可规避。
2. **token 过期**：MVP1 不自动刷新，报错跳过；用一次 `claude` CLI 即可刷新 Keychain。
