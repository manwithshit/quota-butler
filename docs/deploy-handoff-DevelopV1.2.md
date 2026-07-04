# DevelopV1.2 部署交接提示词

> 面向运行机器上的操作者（或新 Claude 会话）。本文档自包含，无需其他上下文。
> 生成于 2026-07-04。替代桌面上的《260703_额度管家DevelopV1.1小修复交接提示词.md》。

## 版本说明

- 分支：`DevelopV1.2`（基于 `main` = `DevelopV1.1` = `5ca6a33`）
- 内容：第一批热修（P0-1 / P0-4 / P0-7 / P1-9 / P1-5）+ scheduled warmup/recovery 去重桥接，详见 `docs/optimization-report-2026-07.md`
- 验证状态：已在开发机通过 `npm test`（83 个测试）、`npm run typecheck`、`npm run build`（2026-07-04）

### 改动清单

| 项 | 文件 | 内容 |
|---|---|---|
| P0-1 | `src/state.ts`、`src/run.ts` | `state.json` 原子写（tmp+rename）；损坏时备份为 `state.json.corrupt-<时间戳>` 并重置，启动后私聊 owner 一条"状态已重置"警告 |
| P1-9 | `src/state.ts`、`src/config.ts` | `state.json` / 配置文件权限收紧为 0600，存量安装启动时自动修复 |
| P0-7 | `src/warmup_lock.ts`（新）、`src/handler.ts`、`src/scheduler.ts` | 预热互斥：同一 provider 手动/定时预热不再并发；撞锁时回执"正在预热中"或记 skip 事件 |
| Warmup/Recovery | `src/recovery_window.ts`（新）、`src/scheduler.ts`、`src/poller.ts` | 定时预热成功后标记当前 fiveHour recovery window 已处理；poller 尊重 `lastWarmedWindows`，避免同一窗口又发恢复卡；resetAt 90 秒内漂移仍视为同窗 |
| P0-4 | `src/notify.ts` | 删除"两个都用"按钮和 dual 时间轴死代码，换工具卡只留 cc / codex |
| P1-5 | `src/poller.ts` | 睡前卡只在 22:00–22:59 发（23 点起是安静时段） |
| 测试 | `tests/state_persistence.test.ts`、`tests/warmup_mutex.test.ts`（新），`tests/poller.test.ts`、`tests/scheduler.test.ts`（扩展） | 原子写/权限/损坏恢复；预热互斥/定时撞手动/卡片无 both；warmed window 不重复发 recovery，下一窗口不误杀 |

## 部署步骤

在运行机器的 quota-butler 仓库目录内执行（不要删除旧版本目录）：

```bash
quota-butler stop                      # 1. 停止当前守护
git fetch origin DevelopV1.2
git checkout DevelopV1.2
npm install
npm run build                          # 2. 构建
npm link                               # 3. 让全局命令指向本目录
quota-butler start                     # 4. 启动守护
quota-butler status                    # 5. 确认运行中
```

可选：`quota-butler selftest` 做一次自检。

## 验收清单

部署后当天观察：

1. **启动无异常**：`quota-butler status` 显示运行中；日志无报错。若飞书收到一条"状态已重置"警告，说明旧 `state.json` 损坏被备份重置（备份文件在同目录 `state.json.corrupt-*`），属预期行为，但当天的计划/去重状态需重新设置。
2. **文件权限**：`ls -l` 检查 `state.json` 和配置文件权限为 `-rw-------`（600）；`state.json.tmp` 不应残留。
3. **预热互斥**：飞书里快速双击预热按钮，应只执行一次，第二次收到"正在预热中，请等待回执，不要重复点击"。
4. **定时预热后不重复恢复提醒**：若某个 fiveHour 窗口已由定时预热成功处理，后续 poller 不应再为同一个窗口发"额度已恢复"卡；下一轮 fiveHour 窗口仍应正常提醒。
5. **换工具卡**：卡片上只有 cc / codex 两个选项，没有"两个都用"。
6. **睡前卡**：23:00 后不再发睡前卡（22 点档正常发）。
7. **常规功能回归**：飞书收发、额度恢复通知、安静时段、计划/预热定时执行均正常（同 DevelopV1.1 行为）。

## 回滚步骤

```bash
quota-butler stop
git checkout main        # 即 DevelopV1.1（5ca6a33）
npm install
npm run build
npm link
quota-butler start
```

注意：若 DevelopV1.2 运行期间发生过"状态已重置"，回滚后不会自动恢复旧状态；如需找回，备份在 `state.json.corrupt-<时间戳>`，可停守护后手动改名回 `state.json` 再启动（仅当确认该文件内容完好时）。

## 后续计划

DevelopV1.2 在 dogfood 机器稳定运行一天、验收清单全过后，按 README《分支与部署约定》通过 PR 合入 `main`。第二批及以后的迭代项见 `docs/optimization-report-2026-07.md` 的 P1/P2 清单。
