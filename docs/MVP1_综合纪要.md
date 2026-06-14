---
categories: [项目, 综合纪要]
项目: 额度管家 · quota-butler
阶段: MVP1（代码完成 · 已上 GitHub private · 已本机验证）
更新日期: 2026-06-13
配套: PRD_MVP1.md, DEV_PLAN_MVP1.md, TEST_PLAN_MVP1.md
---

# 📘 额度管家 quota-butler · MVP1 综合纪要

> 给懒人哥回家 review 用。一篇看完：这事怎么起的、做了什么、现在能干什么、cover 了哪些场景、还不能干什么。
> 代码仓库（权威事实源）：**github.com/manwithshit/quota-butler**（private）。本机 clone：家里机 `~/projects/quota-butler`、vault Mac `…/02_项目/胡思乱想的项目/quota-butler`。产品文档在本目录。

---

## 一、前因后果（这事怎么起的）

1. **起点**：你问"看看 30_Projects 里有没有一个讲 token 雷达相关的项目"。vault 里没有叫"token 雷达"的目录，但有一个高度对应的 **额度管家 quota-butler**（监控 CC token/额度 → 提醒 → 代预热），就是它。
2. **你的要求**：先问"你能做什么、代码相关的工作"，确认就是这个项目后，让我"建仓"，然后逐阶段推进、**每一步都留文档**，一路做到 S5，最后真点了一次卡片验证 bridge 回传。
3. **关键背景**：我（Claude）这个 session **本身就跑在 `lark-channel-bridge` 里**。这让我能直接拍板 S3 那个"飞书按钮回调能不能接住"的阻塞项——答案是能（机制 A），不用再调研。
4. **一句话定义**（来自 PRD）：跑在 Mac 上的轻量哨兵——**感知 CC 额度 → 规则判断 → 飞书带按钮提醒 → 人点「开」→ 替你向 CC 发预热消息开窗**。不是看板，是会替你动手的管家。

---

## 二、项目进展（做了什么）

### 交付物总览

| 指标 | 值 |
|------|----|
| 代码仓库 | github.com/manwithshit/quota-butler（private）|
| 文件数 | 21（含 tests / deploy / 文档） |
| Python 代码 | 835 行，纯 stdlib **零第三方依赖** |
| 单元测试 | **32 个，全绿**（含容错故障注入）|
| git commit | 7 个 |
| 阶段 | S0–S5 **全部代码完成** |

### 六阶段进度

| 阶段 | 内容 | 状态 | 验证方式 |
|------|------|------|----------|
| S0 | 脚手架：config / state / provider 抽象 | ✅ | `--dry-run` + 单测 |
| S1 | 感知层：Keychain token → oauth/usage → five_hour | ✅ | **本机真打接口** |
| S2 | 规则判断 + 去重（窗口换挡 + 容差去重）| ✅ | 5 组单测 |
| S3 | 飞书卡片（带【开/不开】，机制 A 回调）| ✅ | **真发到群** |
| S4 | 反向动作：开 → `claude -p` 预热 → 回执 | ✅ | **真跑 claude -p + 真回执** |
| S5 | launchd 常驻 + 部署脚本 | ✅ | **launchd 实测 + env -i 复现** |

### 🎯 完成线已闭合（全链路真实验证）

```
① 感知（真打接口）
   → ② 提醒（真发卡到群）
      → ③ 拍板（你真点【不开】，bridge 真把 [card-click] 送回 session）
         → ④ 代执行（skip 真静默 / warmup 真跑 claude -p + 真回执「✅ 已开窗」）
```

四步每一步都不是模拟，是真实端到端跑过的。

---

## 三、踩坑实录（4 个真 bug，全是真跑/真部署抓出来的）

> 价值点：这些坑**只有真跑、真发、真部署**才暴露，纯写代码 + 单测发现不了。

1. **发卡必须 `--as bot`**：lark-cli 默认 user 身份，缺 `im:message.send_as_user` scope → missing_scope 报错。
2. **`handler --dry-run` 原会真烧 token**：dry-run 最初只挡回执没挡 `claude -p`，白烧一次。6/15 后是真钱，已修。
3. **`resets_at` 微秒会漂移**：oauth/usage 后端每次现算，同窗口两次读到 `.959751` vs `.777681`。字符串精确匹配做去重 → 卡片重建后再点会**双烧 token**。已抽 `window.same_window`（60s 容差）统一去重。
4. **launchd 受限环境两坑**（env -i 复现）：
   - PATH 不含 `/usr/local/bin` → `lark-cli`/`claude` FileNotFoundError；
   - 缺 `LARK_CHANNEL` → lark-cli 回退默认 app，报 230002「Bot can NOT be out of the chat」，**推送静默失败**。
   - install.sh 自动注入两者修复。

---

## 四、当前具备的能力（能干什么）

- ✅ **感知 CC 真实额度**：读 macOS Keychain token，打 `oauth/usage`，拿 `five_hour` 利用率 + `resets_at`（token 只留内存，不打印/不写盘/不外传）。
- ✅ **规则触发 + 去重**：窗口换挡规则（距 reset < 阈值），可选防浪费叠加（利用率 < 阈值）；同窗口只提醒一次、只预热一次（容差去重）。
- ✅ **飞书主动推送**：lark-cli 发 CardKit 2.0 卡片，带【🔥 开】【不开】两个回调按钮。
- ✅ **回调闭环（机制 A）**：用户点击 → bridge 把 `[card-click]` 回传 session → `handler.py` 确定性处理。
- ✅ **代预热开窗**：点「开」→ 真发 `claude -p` → 飞书回执；点「不开」→ 静默。
- ✅ **常驻调度**：launchd 定时拉起，跑完即退；一键 install/uninstall。
- ✅ **provider 可扩展**：感知/预热抽象成接口，留好 Codex 扩展位。

### Cover 的场景

| 场景 | 是否 cover |
|------|-----------|
| 5h 窗口即将重置时提醒我 | ✅ |
| 我远程点一下就替我开窗预热 | ✅ |
| 挂机零干预定时巡检 | ✅（launchd）|
| 同一窗口不重复打扰 / 不重复烧 token | ✅（去重）|
| 预热改用 Codex 规避 6/15 计费 | ⚠️ 配置位留好，**Codex provider 未实现**（见下）|

---

## 五、不具备的能力（明确不做 / 还没做）

> 这些是 MVP1 **有意砍掉**的，进 [[BACKLOG]]，不是遗漏。

- ❌ **Codex 接入**：`warmup_provider: codex` 配置位留了，但 `providers/codex.py` **未实现**，现在切 codex 会报错。MVP1 只跑 CC。
- ❌ **token 自动刷新**：CC token 过期时 MVP1 只报错跳过，不自动刷新（需用一次 `claude` CLI 让它刷新 Keychain）。
- ❌ **第二种触发场景**：只做了"窗口换挡"一条规则。
- ❌ **自动预热**：必须人点「开」才动，不自动开窗。
- ❌ **SQLite / 复杂状态机**：只用一个 JSON。
- ❌ **多档 snooze / 复杂调度**。
- ❌ **其它 provider**（Antigravity / Gemini 等）。

### 已知约束与风险

> [!warning] 6/15 计费红线
> 自 **2026-06-15** 起 CC `claude -p` 独立计费、不走订阅。"开"=`claude -p` 预热，6/15 后会扣钱。缓解：`warmup_provider` 切 codex（但 codex provider 尚未实现，需先补 BACKLOG 项）。

- 本机 token 独立跑可能过期（MVP1 报错跳过）。
- 代码仓库已推 GitHub private remote（manwithshit/quota-butler），并 clone 到 vault Mac 标准代码位、本机实跑验证（32 测试绿、感知层 token 过期 fallback 正常）。

---

## 六、当前状态 & 回家后的下一步

> [!check] 现在的状态
> - 代码：S0–S5 全完成，**32/32 测试绿**，7 commit，**已推 GitHub private remote 并本机验证**。
> - 运行：**launchd 已卸载，项目暂停**，不会自动跑。配置 `~/.quota-butler/config.yaml`、状态 `~/.quota-butler/state.json` 保留。
> - 完成线：已 100% 真实验证。

### 建议你 review 的点

1. **代码走查**：`~/projects/quota-butler`，建议从 `README.md` → `quota_butler/main.py`（主流程）→ `providers/claude.py`（感知+预热）→ `handler.py`（回调）。
2. **要不要推 remote**：迁到你那个外部 GitHub project，还是就地建 remote。
3. **要不要重新启动哨兵**：`bash deploy/install.sh` 即可挂机；注意 6/15 计费。
4. **下一步选 BACKLOG 哪项**：Codex provider（一并解决计费 + token 刷新）通常是性价比最高的下一步。

### 重新启动 / 停用速查

```bash
cd ~/projects/quota-butler
bash deploy/install.sh      # 启动挂机
bash deploy/uninstall.sh    # 停用（当前状态）
python3 -m quota_butler.main --dry-run   # 手动跑一轮看感知，不发飞书
python3 -m unittest discover -s tests    # 跑测试
```

---

*纪要 v1 · 2026-06-13 · 配套 [[PRD_MVP1]] / [[DEV_PLAN_MVP1]] / [[TEST_PLAN_MVP1]]*
