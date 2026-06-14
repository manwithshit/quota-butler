---
categories: [项目, 设计草案]
项目: 额度管家 · quota-butler
阶段: Phase 2（read_usage 已实现 · warmup 留 BACKLOG）
创建日期: 2026-06-13
更新日期: 2026-06-14
配套: BACKLOG.md, facts/01_本机实测_接口与凭据.md
---

# 🧩 设计 · Codex provider

> ✅ **更新 2026-06-14**：`read_usage()` 已实现（`providers/codex.py`），并用本机实测 dump 的
> 真实字段写了解析（免费档月度窗口已验证）。`warmup()` **仍未实现**——免费档无 5h 窗口可换挡、
> 且 `codex exec` 刷新会烧月度额度，留 [[BACKLOG]]。以下为原始设计稿，保留作背景。
> 事实依据：[[facts/01_本机实测_接口与凭据]] 第二节。

---

## 一、为什么 Codex 是性价比最高的下一步

| 收益 | 说明 |
|------|------|
| 💰 **规避 6/15 计费** | 6/15 起 CC `claude -p` 独立计费；`codex exec` 走 ChatGPT 订阅、不计费。`warmup_provider: codex` 一切就规避 |
| 🔄 **token 刷新一箭双雕** | Codex access_token ~1h 过期，但跑一条 `codex exec "ok"` 会**自动刷新 auth.json + 顺便 warm-up**——刷新和预热是同一个动作 |
| 🧱 **验证现有抽象** | provider 接口（`read_usage`/`warmup`）目前只有 CC 一个实现；加 Codex 是对"留扩展位"承诺的第一次兑现 |

> 一句话：Codex 不只是"多一个 provider"，它同时解决了**计费**和**token 刷新**两个 MVP1 遗留风险。

---

## 二、接入点（现有代码怎么扩展）

现成抽象已经留好位，**无需改主流程**：

```
providers/base.py     Provider 接口：read_usage() / warmup()  ← 不动
providers/__init__.py get_provider(name)                       ← 加一个分支
providers/codex.py    新建：CodexProvider                      ← 新代码
config.py             warmup_provider 已支持取值 codex          ← 不动
main.py               感知端固定 cc（见下"感知 vs 预热"）        ← 可能微调
handler.py            get_provider(cfg.warmup_provider).warmup() ← 自动生效
```

`get_provider` 只需加：

```python
if name in ("codex", "cx"):
    return CodexProvider()
```

---

## 三、感知 vs 预热：两条路，先只接预热

| 用途 | MVP1 现状 | Phase 2 建议 |
|------|-----------|-------------|
| **预热**（warmup）| `warmup_provider`，默认 cc | **先接这条**：`codex` 用 `codex exec` 预热，规避计费 |
| **感知**（read_usage）| 固定 cc（main.py 硬编码）| 可选后做：感知 Codex 额度需另配 `sense_provider`，MVP1 没这需求 |

> 建议 Phase 2 **只先实现 `CodexProvider.warmup()`**，`read_usage()` 可先 `raise NotImplementedError`（或一并做，见下）。理由：规避计费是当下唯一刚需，感知 Codex 额度是 nice-to-have。这样改面最小、风险最低。

---

## 四、CodexProvider 详细设计

### 4.1 warmup()（刚需，先做）

```python
def warmup(self, prompt: str) -> str:
    # codex exec 走订阅、不计费；且会自动刷新 ~/.codex/auth.json 里的 token
    out = subprocess.run(["codex", "exec", prompt],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise ProviderError(f"codex exec 失败: {out.stderr[:200]}")
    return out.stdout.strip()[:200]
```

- **本机事实**：`codex` = codex-cli 0.130.0（`/usr/local/bin/codex`）。⚠️ launchd PATH 已含 `/usr/local/bin`，但 handler 由 bridge 承接、不在 launchd 里，PATH 通常 OK；仍建议 deploy 文档注明 codex 路径要求。
- **风险**：`codex exec` 首次/久未用时要走 OAuth 刷新，可能比 `claude -p` 慢；timeout 给足（建议 120s）。

### 4.2 read_usage()（可选，后做）

事实依据（facts/01 第二节，端点已确认、当时 token 过期返回规整 401）：

- 凭据：`~/.codex/auth.json` → `tokens.access_token` + `tokens.account_id`
- 请求：`GET https://chatgpt.com/backend-api/wham/usage`
  - Header：`Authorization: Bearer <access_token>` + `chatgpt-account-id: <account_id>`
  - 备选端点：`https://chatgpt.com/backend-api/codex/usage`
- 响应结构（来自 CodexBar 权威实现，**本机未实测验证，token 当时过期**）：
  - `rate_limit.primary_window` → 5h 主窗口
  - `rate_limit.secondary_window` → 7 天次窗口
  - `additional_rate_limits[]` → 按模型/code-review 细分

**Codex → 统一 Usage 的字段映射（待实测校准）**：

| 统一结构 | Codex 来源（推测） |
|----------|------------------|
| `five_hour.utilization` | `rate_limit.primary_window.used_percent`（字段名待实测）|
| `five_hour.resets_at` | `rate_limit.primary_window.resets_at` / `reset_time`（待实测）|
| `seven_day.*` | `rate_limit.secondary_window.*` |

> ⚠️ 字段名是推测，**第一步必须像扫地僧当初验 CC 那样，本机用 fresh token 真打一次 wham/usage 把结构 dump 出来**，再写解析。别照抄 CodexBar 的字段名。

### 4.3 Token 刷新策略（401 → 自愈）

```
read_usage():
  token = read auth.json
  resp = GET wham/usage
  if resp == 401 (token_expired):
      run `codex exec "ok"`        # 自动刷新 auth.json + 顺带 warm-up
      token = re-read auth.json    # 拿新 token
      resp = GET wham/usage        # 重试一次
  parse resp
```

- **一箭双雕**：刷新动作本身就是一次 warm-up。
- **不自己实现 OAuth 刷新**：用 refresh_token 走 OpenAI OAuth 需要 Codex 的 client_id，本机未验证，**不建议 v1 做**（facts/01 明确不推荐）。
- **并发注意**：`codex exec` 会写 `auth.json`，若与 Codex CLI 同时跑可能竞争；MVP 阶段单进程串行，风险低。

---

## 五、配置变化

`config.example.yaml` 已有 `warmup_provider`，无需加字段。只需文档补一句：

```yaml
# 预热用哪个 provider：cc（默认）/ codex
# codex 走 ChatGPT 订阅、不计费，规避 6/15 CC claude -p 计费
warmup_provider: codex
```

如未来要感知 Codex 额度，再引入 `sense_provider`（默认 cc）。

---

## 六、测试计划（沿用故障注入套路）

| # | 用例 | 做法 |
|---|------|------|
| CX-1 | warmup 正常 | mock `codex exec` 返回 0 → 返回回执 |
| CX-2 | warmup 失败 | mock 非 0 → `ProviderError` |
| CX-3 | read_usage 401 自愈 | mock 首次 401 → 触发 `codex exec` → mock 二次 200 → 解析成功 |
| CX-4 | read_usage 二次仍失败 | mock 两次 401 → `ProviderError`，不死循环 |
| CX-5 | 字段映射 | 用真实 dump 的样例 JSON → 映射成 Usage 正确 |
| CX-6 | auth.json 缺失/损坏 | `ProviderError` |

全程 mock subprocess + urllib，**不烧订阅、不读真凭据**——与 `test_claude_provider` 同款。

---

## 七、分阶段落地（建议）

1. **P2-a｜只做 warmup**（半天）：`CodexProvider.warmup()` + `get_provider` 分支 + CX-1/2 测试。**立刻可规避 6/15 计费**，改面最小。
2. **P2-b｜感知 + 自愈**（1–2 天）：先本机 fresh token 真打 `wham/usage` dump 结构 → 写 `read_usage()` + 401 自愈 + CX-3~6。
3. **P2-c｜文档**：BACKLOG 勾掉 Codex 项，README 补 codex 用法 + 路径要求。

---

## 八、开放问题（等懒人哥定）

1. **优先级**：只做 P2-a（规避计费）就够，还是连感知一起做（P2-b）？
2. **6/15 后默认值**：要不要把 `warmup_provider` 默认值从 `cc` 改成 `codex`，让规避计费成为默认行为？
3. **Codex token 长期闲置**：你最近少用 Codex（facts/01 显示 6/5 就过期了），靠 quota-butler 的 `codex exec` 反而能帮你保活 token——这算 bonus 还是你不希望它替你动 Codex？

---

*草案 v1 · 2026-06-13 · 仅设计未实现 · 配套 [[BACKLOG]] / [[facts/01_本机实测_接口与凭据]]*
