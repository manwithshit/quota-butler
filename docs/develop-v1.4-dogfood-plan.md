# V1.4 Public Preview 测试方案

> 状态：已完成。V1.4 于 2026-07-25 结束 Public Preview，并以 `v1.4.0` 转为稳定版。

V1.4 采用公开测试方式：维护者继续在自己的 Mac 上 dogfood，同时邀请 GitHub 用户安装 beta tag，在更多账户、网络与睡眠环境中共同验证。

## 1. 发布通道

| 通道 | 安装目标 | 适用人群 |
|---|---|---|
| Stable | `main` / `v0.1.0` | 希望长期稳定运行的用户 |
| V1.4 Public Preview | `v1.4.0-beta.1` | 愿意反馈边界问题的测试用户 |

稳定版：

```bash
npx github:manwithshit/quota-butler run
```

V1.4 Public Preview：

```bash
npx github:manwithshit/quota-butler#v1.4.0-beta.1 run
```

## 2. V1.4 行为定义

### 明日计划

- 默认推荐 Claude Code，因为它仍有每日可预热的 5 小时窗口。
- Claude Code 计划保留两次预热，用于准备两段 5 小时窗口。
- 不再提供“两个都用”的前置选择。
- 采用 Claude Code 计划后，只有 Codex 已知周重置点严格早于计划结束时，当前计划才显示“追加 Codex 新周期预热”。
- 同一个 Codex 周重置点最多追加一次，重复点击不会创建重复任务。

### Codex 仅周额度

- 当前周周期进行中：正常使用，不做每日预热。
- 周额度耗尽且未到重置点：显示“周额度已耗尽，等待下一个周期刷新。”
- 已到重置点但尚未发送新消息：显示“新一周额度已就绪，发送任意消息开始计时。”
- 周重置发生在计划开始前：在开工前 10 分钟激活，但不会早于重置点后 90 秒。
- 周重置发生在工作时段内：在重置点后 90 秒激活。
- 没有可靠重置时间，或重置时间不早于计划结束：不自动追加。
- Claude Code 不可用时，Codex 可以作为单工具兜底；当前周期活跃时无需定时任务，新周期在计划内到来时只创建一次周期激活。

### 内部语义

- Claude Code 的 5 小时操作使用 `warmup` 事件。
- Codex 的新周期操作使用独立的 `weekly-activation` 计划事件和 `weekly_activation` 日志事件。
- Codex 周期激活成功后不会标记或制造 5 小时窗口。
- 后台感知已知 weekly-only / monthly-only Codex 时，不通过 `codex exec` 刷新凭证，避免无意消耗长周期额度。

## 3. 自动化验证

V1.4 发布前必须通过：

```bash
npm run typecheck
npx vitest run --api.host=127.0.0.1
npm run build
node dist/cli.mjs selftest
git diff --check
```

核心覆盖：

- Codex `5h + weekly`、`weekly-only`、`monthly-only` 三种响应解析。
- 周周期 active / exhausted / ready 三态文案。
- Claude Code 默认选择与两次预热回归。
- Codex 重置在计划前、计划中、计划结束点、跨午夜和未知重置时间的计算。
- 追加按钮显隐、重复点击幂等和旧双 Agent 计划兼容。
- `weekly-activation` 独立调度、回执、日志与恢复提醒去重。
- 守护重启后重新布置计划，取消后撤销剩余节点。
- 安静时段、状态持久化、网络代理和 5 小时恢复逻辑不回归。

## 4. 公开测试清单

建议测试者重点选择其中 2–3 项，不必为了覆盖全部场景消耗额外额度：

1. 查询额度，确认 weekly-only Codex 不显示虚假的 5 小时窗口。
2. 设置明日计划，确认默认推荐 Claude Code 且有两个预热节点。
3. 查看计划，确认 Codex 重置不在计划内时没有追加入口。
4. 取消计划后再次设置，确认旧定时器不会执行。
5. 建计划后重启额度管家，确认计划仍在且节点只执行一次。
6. 在真实 Codex 周重置点前后观察状态与恢复提醒。
7. 新周期就绪后追加 Codex 激活，重复点击确认只追加一次。
8. 激活执行后查询额度，确认新周周期开始且没有生成 5 小时窗口。
9. 观察晚间日报是否把 Claude Code 预热与 Codex 周期激活分开统计。

## 5. 已知限制

- Mac 睡眠或关机可能错过定时节点；明显过时的节点会在恢复后跳过并发送回执。
- Codex 新周期激活使用计划创建时最后读到的重置点，执行前暂不重新查询用户是否已经手动开启周期。
- “再次设置”仍要求先取消同日期计划；追加 Codex 周期激活不需要取消主计划。
- 主动查询 Codex 额度在凭证失效时可能需要刷新；后台只读感知会尊重已知档位并避免 weekly-only 的模型刷新。

## 6. 反馈要求

通过 GitHub 的 “V1.4 Public Preview 反馈”模板提交：

- beta 版本号；
- macOS、Mac 型号和 Node.js 版本；
- Codex 额度形态；
- 操作时间、计划时间与复现步骤；
- 飞书截图；
- 脱敏后的 `~/.quota-butler/logs/daemon.log` 相关片段。

不得提交 token、飞书应用凭证、open_id、chat_id 或 Claude Code / Codex 登录文件。

## 7. 正式版门槛

满足以下条件后，将 V1.4 squash merge 到 `main` 并发布 `v1.4.0`：

- 连续一周没有重复预热、错误追加或计划丢失；
- 至少一次真实 Claude Code 定时预热通过；
- 至少一次守护进程重启恢复通过；
- 至少一次真实 Codex 周周期激活通过；
- 自动化验证全部通过；
- 中英文 README、截图、Release Notes 与实际行为一致；
- 没有未说明的 P0/P1 级公开测试问题。

## 8. Beta 1 发布基线

- 分支：`DevelopV1.4`
- 核心实现提交：`aa43eab feat: align planning with Codex weekly activation`
- 类型检查：通过
- Vitest：14 个测试文件、122 条测试通过
- 构建：通过
- 本机真实自检：Claude Code 与 Codex 均为 connected
- 本机真实档位：Claude Code 为 `has-5h`，Codex 为 `weekly-only`

## 9. 正式版验收结果

- 维护者完成持续 dogfood，并确认 V1.4 可以转入稳定通道。
- 公开测试期没有收到新的 V1.4 P0/P1 问题。
- 明日计划、守护重启恢复、睡眠迟到保护、飞书长连接恢复和 Codex 周额度状态切换均在真实运行日志中得到验证。
- 发布前重新通过 14 个测试文件、122 条测试、类型检查、构建、本机 selftest 和 `git diff --check`。
- 正式版本号：`1.4.0`。
