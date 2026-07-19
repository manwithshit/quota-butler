# DevelopV1.4 双 Agent + Codex 周额度 Dogfood 方案

## 1. 历史结论

- `d346b34 fix: harden quota windows and scheduled warmups` 已实现双 Agent 计划：
  - `agent_strategy=both`
  - 主 Agent 两次预热
  - 接力 Agent“提前垫窗 + 接力刷新”
  - 每个 Agent 的预热任务独立安装与执行
- `223a6d5 feat: simplify single-agent planning flow` 主动删除了 `both` 入口和接力逻辑，将产品收敛为单 Agent。
- `1ab5ed1 migrate quota butler to ts npx app` 迁移到 TypeScript 后保留了部分手动双 Agent 辅助代码，但正式路由一直返回“未开放”。
- 因此，历史上确实提交过双 Agent 代码，但它不是独立的 V1.3/V1.4 分支，而是主干历史中后来被产品决策删除的一段实现。

## 2. V1.4 行为定义

### 默认行为

- `auto` 仍然只选择一个 Agent，避免升级后默认计划突然增加真实请求。
- 只有用户明确选择“两个都用”时，才生成双 Agent 计划。
- 每个日期仍只保存一个计划；双 Agent 事件放在同一个计划中，不建立两个互相覆盖的日期计划。

### 额度形态矩阵

| 主工具 / 接力工具 | 计划行为 |
|---|---|
| 5h / 5h | 恢复历史逻辑：主工具两次预热；接力工具提前垫窗并在接力点刷新 |
| 5h / weekly-only | 主工具两次预热；Codex 只在接力前做一次连通预热 |
| weekly-only 单工具 | 开工前一次连通预热，不生成第二个 5h 节点 |
| monthly-only 单工具 | 可查询额度，但不参与计划或预热 |

### 不变量

- 传统 5h + 周额度账户的原有解析、恢复提醒、两次预热和去重逻辑保持不变。
- weekly-only 不创建、标记或展示虚假的 5h 窗口。
- 后台感知遇到 weekly-only / monthly-only 时不执行 `codex exec` 刷新，避免消耗长周期额度。
- 周额度耗尽时不参与计划；若重置发生在计划开工前，则允许进入候选。
- 双 Agent 的接力事件不能被“调整主工具预热时间”的表单误改。

## 3. 自动化测试方案

### Provider 与档位

- Codex 返回 `5h + weekly`、`weekly-only`、`monthly-only` 三种响应的解析。
- `usageTier` 三档分类。
- nullable `fiveHour` 快照的持久化与旧 5h 快照兼容。
- weekly-only / monthly-only 后台刷新闸门；主动查询仍允许刷新。

### 规划器

- 单 CC 5h 计划的两个时间点保持不变。
- 单 Codex weekly-only 只生成一次预热。
- 显式 `both` 在 5h / 5h 下恢复四事件接力。
- 显式 `both` 在 5h / weekly-only 下生成三事件，不伪造 Codex 5h。
- 只有一个 Agent 可规划时拒绝 `both`。
- `auto` 保持单 Agent。

### 卡片与采用

- 双 Agent 都可规划时显示“两个都用”；只有一个可规划时隐藏。
- 计划预览重新提供“选择 / 更换 AI 工具”入口。
- 混合双 Agent 卡显示三项任务，但表单只编辑主工具的两个预热时间。
- 采用计划后保存两个 Agent，并给三个事件独立布置定时器。
- 修改主工具时间不能覆盖 Codex 接力事件。

### 恢复与调度

- 5h 恢复提醒、容差去重、预热后抑制重复提醒保持通过。
- weekly-only 以周重置点为复查触发器。
- monthly-only 不发送恢复提醒。
- 双 Agent 所有事件独立 arm，重启后可按同一计划恢复。

## 4. 一周 Dogfood 清单

1. 每天查询一次额度，确认 Codex 只显示周额度且剩余百分比正确。
2. 建立一次 Claude Code 单工具计划，确认仍为两个 5h 预热节点。
3. 建立一次 Codex 单工具计划，确认只有一个连通预热节点。
4. 建立一次“两个都用”计划，确认当前真实组合为 Claude Code 两次 + Codex 一次。
5. 查看当前计划，确认两个 Agent 与每个事件状态都可见。
6. 至少让一个双 Agent 计划真实跑到预热时间，核对三个回执、去重和重启恢复。
7. 在 Codex 周额度刷新前后各查询一次，确认只收到周额度恢复提醒。
8. 取消一次尚未执行完的双 Agent 计划，确认所有剩余定时器一起撤销。

## 5. 已知边界

- “查看计划后直接再次设定”仍未实现；当前需要先取消同日期计划。
- 历史双 Agent 实现也是“一个日期的一张双 Agent 计划”，不是同日期两张独立计划。
- Dogfood 若发现双 Agent 体验不稳定，可直接回退到 `DevelopV1.3` 的 `a03a4b4`；V1.3 不包含双 Agent 恢复逻辑。

## 6. 交付前验证结果

- `npm run typecheck`：通过。
- Vitest：13 个测试文件、113 条测试全部通过。
- `npm run build`：通过。
- `git diff --check`：通过。
- 本机真实只读自检：Claude Code 与 Codex 均为 connected。
- 本机真实档位：Claude Code 为 `has-5h`，Codex 为 `weekly-only`。
- 真实配置双 Agent 干跑：按本地时间生成 Claude Code 06:30、11:31 两个预热节点，以及 Codex 13:50 一个接力连通节点；未落盘、未执行预热。
