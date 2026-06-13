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
│   ├── notify.py            # 飞书卡片（lark-cli）
│   └── main.py              # 入口：感知→判断→推送
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

## 阶段进度（MVP1）

- [x] S0 脚手架与配置
- [x] S1 感知层（CC 额度）
- [x] S2 规则判断 + 去重
- [x] S3 飞书卡片（机制 A：`__claude_cb` 回调）
- [ ] S4 反向动作（开 → 预热）—— 逻辑就位，待端到端联调
- [ ] S5 launchd 常驻 + 联调

## ⚠️ 红线

1. **6/15 起 CC `claude -p` 独立计费**，预热会扣钱。`warmup_provider: codex` 可规避。
2. **token 过期**：MVP1 不自动刷新，报错跳过；用一次 `claude` CLI 即可刷新 Keychain。
