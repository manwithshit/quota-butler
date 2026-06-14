---
categories: [项目, BACKLOG]
项目: 额度管家 · quota-butler
创建日期: 2026-06-13
---

# 🗂️ BACKLOG · 额度管家 quota-butler

> MVP1 之后再处理。MVP1 范围见 [[PRD_MVP1]]。
> 规则：这里只记"以后做"，**别让它们偷偷溜进 MVP1**。

---

## Phase 2 · 闭环增强

- [ ] **Codex 接入**：感知 + 预热都加 Codex provider（端点已确认 `wham/usage`，token 走 `codex exec "ok"` 刷新，一箭双雕：既刷 token 又 warm-up）→ **详细设计见 [[设计_Codex_provider]]**（Phase 2 首选，分 P2-a 只做 warmup 规避计费 / P2-b 加感知+自愈）
- [ ] **第二触发场景**：把"防浪费"与"新窗口开了"拆成两条独立规则，分别推不同卡片
- [ ] **多账号 / 多 Workspace**：bridge 已支持，按需点亮

## Phase 2 · 自动化

- [ ] **自动预热**：满足条件免确认直接开窗（MVP1 坚持人工点「开」）
- [ ] **CC token 自动刷新**：独立跑时 token 过期的 fallback（MVP1 先报错跳过）
- [ ] **多档 snooze**：稍后提醒 / 今天别再烦我 等

## 工程化 / 开源

- [ ] provider 接口正式抽象（感知 + 预热双侧），文档化扩展方式
- [ ] 其它 provider 扩展位：Antigravity / Gemini
- [ ] README / 安装脚本打磨到可开源
- [ ] 出小红书内容（开发故事 / 工具介绍）

## 待验证 / 风险跟踪

- [ ] `lark-channel-bridge` 是否支持飞书卡片 **action 回调**（决定 FR3 走按钮还是"回复『开』"）
- [ ] `lark-channel-bridge` 升级 0.1.33 → 0.3.0 的收益与风险
- [ ] 6/15 计费红线落地后，实测 `claude -p` 预热的真实扣费情况

---

## 已被推翻 / 不做（备忘，避免重复讨论）

- ❌ 段誉那套"估算刷新时间 / 读 jsonl 自己算窗口 / Node.js //status 间接获取"——已被扫地僧本机实测推翻（`resets_at` 后端直接给）
- ❌ SQLite 重状态机——过度设计，用 JSON
- ❌ MVP1 必含飞书双向闭环 + 双 provider——已收敛为 MVP1 单 CC + 单场景闭环

---

*v1 · 2026-06-13*
