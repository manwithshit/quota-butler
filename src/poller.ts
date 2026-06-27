// 周期轮询：检测额度恢复 → 私聊 owner 提醒；22:00 睡前询问；安静时段静默。
// 移植自 Python main.py。全部主动卡片发给 owner DM。

import type { LarkChannel } from '@larksuite/channel';
import { detectAgents, isSchedulable, type AgentStatus } from './agent_status.js';
import { buildBedtimeCard, buildRecoveryCard, type Card } from './notify.js';
import { usageTier } from './providers/index.js';
import { planIsExpired, type StateStore } from './state.js';

const RECOVERY_FRESHNESS_MS = 4 * 3600000; // 4h：限流/网络抖动导致重置后晚一点才读到，也仍能补推恢复卡
// 重置点精准复查：在已知的 5h 窗口重置时刻 +90s 单独查一次，及时抓到回血、立刻发恢复卡，
// 不靠 15 分钟盲轮询碰运气（借鉴 Usage4Claude 的 resetVerify 思路）。
const RESET_CHECK_DELAY_MS = 90_000;
const RESET_SCHEDULE_HORIZON_MS = 6 * 3600000; // 只为 6h 内的重置点排，避免布太远的定时器
// 同窗口容差：oauth/usage 后端每次现算 resets_at，秒级会漂移；精确等值会让同一窗口
// 被当成"新窗口"重复发卡。借鉴 Python 老版 TOLERANCE_SECONDS=60，这里取 90s。
const WINDOW_MATCH_TOLERANCE_MS = 90_000;
// 发送冷却兜底：同一 provider 的恢复卡在此窗口内最多发一张。即使去重 key 被击穿、
// 或崩溃后队列重发，也不会再出现"短时间多张"。5h 窗口本就远长于此，不会误杀真新窗口。
const RECOVERY_SEND_COOLDOWN_MS = 30 * 60_000;

export class Poller {
  private timer?: ReturnType<typeof setInterval>;
  private resetTimers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(
    private readonly channel: LarkChannel,
    private readonly ownerId: string | undefined,
    private readonly state: StateStore,
    private readonly intervalMs = 15 * 60000,
  ) {}

  start(): void {
    void this.tick();
    this.timer = setInterval(() => void this.tick(), this.intervalMs);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    for (const t of this.resetTimers.values()) clearTimeout(t);
    this.resetTimers.clear();
  }

  /** 在每个 provider 已知的 5h 重置时刻 +90s 排一个一次性复查 tick（替换旧的）。
   *  当前没读到的 provider（CC 令牌过期/限流 → 没有实时 usage）也用 last-good 快照里的
   *  resetAt 照排：这样 CC 恢复可读时正好落在重置边界被抓到，而不是干等 15 分钟盲轮询。 */
  private scheduleResetChecks(statuses: Record<string, AgentStatus>, now: Date): void {
    const st = this.state.get();
    const providers = new Set([
      ...Object.keys(statuses),
      ...Object.keys(st.usageSnapshots ?? {}),
    ]);
    for (const provider of providers) {
      const reset =
        statuses[provider]?.usage?.fiveHour?.resetsAt ?? snapshotFiveHourReset(st, provider);
      if (!reset) continue;
      const delay = reset.getTime() + RESET_CHECK_DELAY_MS - now.getTime();
      if (delay <= 0 || delay > RESET_SCHEDULE_HORIZON_MS) continue;
      const existing = this.resetTimers.get(provider);
      if (existing) clearTimeout(existing);
      this.resetTimers.set(
        provider,
        setTimeout(() => {
          this.resetTimers.delete(provider);
          void this.tick(); // 到点复查：读到回血→检测出翻篇→发恢复卡，并据新 resetsAt 重排
        }, delay),
      );
    }
  }

  private async tick(): Promise<void> {
    if (!this.ownerId) return;
    const now = new Date();
    const st = this.state.get();
    let statuses: Record<string, AgentStatus>;
    try {
      // 只读感知：带上已知档位缓存，免费档 Codex token 过期时不触发 codex exec 刷新（省月额度）。
      statuses = await detectAgents(undefined, { sensing: true, knownTiers: st.providerTiers });
    } catch (e) {
      console.error('[poller] detect 失败：', e);
      return;
    }
    // 过了结束时间的计划自动清除（否则一直挂 active）。
    if (planIsExpired(st.activePlan, now)) {
      st.activePlan = null;
      st.executedWarmups = [];
    }
    // 恢复检测必须在 recordUsageSnapshot 之前：它要拿"上一次"的 providerSnapshots /
    // usageSnapshots 当对比基准；若先记新快照就把基准覆盖了，限流后第一次读通也检测不出翻篇。
    const detected = this.newlyRecovered(statuses, now);
    if (detected.length) {
      this.markRecoveries(detected);
      this.enqueueNotifications(detected);
      for (const [provider] of detected) this.state.appendEvent({ type: 'recovery', agent: provider });
    }
    for (const [p, s] of Object.entries(statuses)) {
      if (!s.usage) continue;
      this.state.recordUsageSnapshot(p, s.usage);
      // 记住档位：下一拍（含进程重启后）就能在感知时认出免费档，跳过烧额度的刷新。
      st.providerTiers = { ...st.providerTiers, [p]: usageTier(s.usage) };
    }
    // 每天首次观测记一张"日初"快照，供日报算当日消耗。
    this.state.recordDayStart(isoDate(now), statuses);
    // 到期的 snooze：移入持久队列后清掉 pendingRecovery；安静时段到期也不再被丢弃。
    const snoozed = this.dueSnoozed(now);
    if (snoozed) {
      this.enqueueNotifications([snoozed]);
      st.pendingRecovery = null;
    }

    // 睡前卡放宽到 22:00–23:59 窗口 + 当日去重：避免恰好没有 tick 落在 22:xx（或 daemon 22 点后才起）而永久漏发。
    const hour = now.getHours();
    const bedtimeDue = (hour === 22 || hour === 23) && st.lastBedtimePromptDate !== isoDate(now);
    try {
      // 安静时段 / 计划工作区间内不打扰：队列里的提醒留到可打扰时统一补发（不丢）。
      if (!isQuiet(now) && !this.activePlanCovers(now)) {
        await this.flushNotifications();
      }
      if (bedtimeDue) {
        await this.sendCard(buildBedtimeCard(statuses, st.lastPlanRequest, {
          eventLog: st.eventLog,
          dayStart: st.dayStartUsage[isoDate(now)],
          activePlan: st.activePlan,
          now,
        }));
        st.lastBedtimePromptDate = isoDate(now);
      }
    } catch (e) {
      console.error('[poller] 主动提醒失败：', e);
    }

    // 更新 providerSnapshots（供下一轮恢复检测对比上一窗口）。
    // 合并而非覆盖：这一拍没读到的 provider（如限流），保留它上一次的基准——
    // 否则基准被抹掉，等它恢复了也对比不出"窗口翻篇"，恢复提醒就漏了。
    const snaps: Record<string, { utilization: number; resetAt: string | null }> = {
      ...(st.providerSnapshots ?? {}),
    };
    for (const [p, s] of Object.entries(statuses)) {
      // 只跟踪有 5h 窗口的（免费档 Codex 仅月度窗口，不做 5h 恢复检测）。
      if (s.usage?.fiveHour) snaps[p] = {
        utilization: s.usage.fiveHour.utilization,
        resetAt: s.usage.fiveHour.resetsAt ? s.usage.fiveHour.resetsAt.toISOString() : null,
      };
    }
    st.providerSnapshots = snaps;
    st.lastRunAt = now.toISOString();
    this.state.save();
    // 据最新 resetsAt 重排"重置点 +90s"复查（抓回血更及时，不靠盲轮询）。
    this.scheduleResetChecks(statuses, now);
  }

  private newlyRecovered(statuses: Record<string, AgentStatus>, now: Date): Array<[string, string]> {
    const st = this.state.get();
    const previous = st.providerSnapshots ?? {};
    const notified = st.lastRecoveryNotifiedWindows ?? {};
    const results: Array<[string, string]> = [];
    // 注意：计划工作区间内不再"直接丢弃检测"，改为照常检测，由 tick 决定是否延后发送（见 flushNotifications）。
    for (const [provider, s] of Object.entries(statuses)) {
      if (!isSchedulable(s) || !s.usage) continue;
      let before = previous[provider];
      if (!before?.resetAt) {
        // providerSnapshots 缺失（限流期被清 / 重启）→ 回退到持久化 last-good
        // （usageSnapshots，只在成功时写、从不抹）作为对比基准，避免漏报这次翻篇。
        const ug = st.usageSnapshots?.[provider];
        if (ug?.fiveHourResetAt) before = { utilization: ug.fiveHourUtil ?? 100, resetAt: ug.fiveHourResetAt };
      }
      if (!before?.resetAt) continue;
      const resetAt = new Date(before.resetAt);
      if (Number.isNaN(resetAt.getTime())) continue;
      const age = now.getTime() - resetAt.getTime();
      if (age < 0 || age > RECOVERY_FRESHNESS_MS) continue;
      if (!s.usage.fiveHour || s.usage.fiveHour.utilization > 5) continue;
      const windowKey = `${provider}:${resetAt.toISOString()}`;
      // 容差去重：已通知窗口的 resetAt 与当前在 90s 内即视为同一窗口，避免后端时间漂移重发。
      if (sameNotifiedWindow(notified[provider], provider, resetAt)) continue;
      results.push([provider, windowKey]);
    }
    return results;
  }

  private markRecoveries(recovered: Array<[string, string]>): void {
    const notified = { ...(this.state.get().lastRecoveryNotifiedWindows ?? {}) };
    for (const [provider, windowKey] of recovered) notified[provider] = windowKey;
    this.state.get().lastRecoveryNotifiedWindows = notified;
  }

  /** 把待发提醒入持久队列（按 provider:windowKey 去重）。 */
  private enqueueNotifications(items: Array<[string, string]>): void {
    const st = this.state.get();
    const queue = st.pendingNotifications ?? (st.pendingNotifications = []);
    for (const [provider, windowKey] of items) {
      if (!queue.some((q) => q.provider === provider && q.windowKey === windowKey)) {
        queue.push({ provider, windowKey });
      }
    }
  }

  /** 逐条补发队列：发成功一条即出队 + 落盘进度；某条失败则抛出，余下留到下个 tick 重试（不丢、不风暴）。 */
  private async flushNotifications(): Promise<void> {
    const st = this.state.get();
    const queue = st.pendingNotifications ?? [];
    const sentAt = st.lastRecoverySentAt ?? (st.lastRecoverySentAt = {});
    while (queue.length) {
      const head = queue[0]!;
      // 冷却兜底：同一 provider 距上次实际发卡不足 30min，视为重复，丢弃不发（不刷屏）。
      const last = sentAt[head.provider] ? new Date(sentAt[head.provider]!).getTime() : 0;
      if (last && Date.now() - last < RECOVERY_SEND_COOLDOWN_MS) {
        queue.shift();
        this.state.save();
        continue;
      }
      await this.sendCard(buildRecoveryCard(head.provider, head.windowKey));
      sentAt[head.provider] = new Date().toISOString();
      queue.shift();
      this.state.save();
    }
  }

  private dueSnoozed(now: Date): [string, string] | null {
    const pending = this.state.get().pendingRecovery as
      | { provider?: string; windowKey?: string; dueAt?: string }
      | null;
    const provider = pending?.provider ?? '';
    const windowKey = pending?.windowKey ?? '';
    const dueText = pending?.dueAt ?? '';
    if (!provider || !windowKey || !dueText) return null;
    const dueAt = new Date(dueText);
    if (Number.isNaN(dueAt.getTime())) {
      this.state.get().pendingRecovery = null;
      return null;
    }
    if (now.getTime() < dueAt.getTime()) return null;
    // 到期即返回；是否能立刻发由 tick 的"可打扰"判断决定。安静时段到期不再清空、不再丢。
    return [provider, windowKey];
  }

  private activePlanCovers(now: Date): boolean {
    const active = this.state.get().activePlan as Record<string, unknown> | null;
    if (!active || active['status'] !== 'active') return false;
    const start = new Date(String(active['work_start']));
    const end = new Date(String(active['work_end']));
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return false;
    return start.getTime() <= now.getTime() && now.getTime() <= end.getTime();
  }

  private async sendCard(card: Card): Promise<void> {
    if (!this.ownerId) return;
    await this.channel.send(this.ownerId, { card });
  }
}

/** 已通知窗口 key（`provider:ISO`）与当前 resetAt 是否同一窗口（±90s 容差）。 */
function sameNotifiedWindow(notifiedKey: string | undefined, provider: string, resetAt: Date): boolean {
  if (!notifiedKey) return false;
  if (notifiedKey === `${provider}:${resetAt.toISOString()}`) return true;
  const iso = notifiedKey.startsWith(`${provider}:`) ? notifiedKey.slice(provider.length + 1) : '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return Math.abs(t - resetAt.getTime()) <= WINDOW_MATCH_TOLERANCE_MS;
}

/** last-good 快照里该 provider 的 5h resetAt（当前读不到时用来照排重置点复查）。 */
function snapshotFiveHourReset(state: ReturnType<StateStore['get']>, provider: string): Date | null {
  const iso = state.usageSnapshots?.[provider]?.fiveHourResetAt;
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function isQuiet(now: Date): boolean {
  const h = now.getHours();
  return h >= 23 || h < 8;
}

function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
