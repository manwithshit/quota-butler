// 进程内预热调度：按 active plan 给每个未来节点布 setTimeout，到点执行预热并私聊 owner 回执。
// 启动时从 state 重新布点；过期/已执行节点跳过。替代 Python 版的 per-task launchd。

import type { LarkChannel } from '@larksuite/channel';
import { getProvider } from './providers/index.js';
import { PROVIDER_LABEL } from './notify.js';
import { activePlanIndex, planIsExpired, type StateStore } from './state.js';

// 预热提示词：必须是一句"秒回、不触发任何工具"的 ping——否则像问"什么项目"会让
// claude/codex 以 agent 身份去翻文件，慢甚至超时。回执仍带模型回复，照样能看出"真跑了"。
export const WARMUP_PROMPT = '你好，请用一句话简短回复即可。';
const FIRE_GRACE_MS = 5 * 60000; // 预热触发宽限：超过这么久没触发就视为过时、跳过

interface EventRec {
  agent: string;
  kind?: string;
  type?: string;
  at: string;
  purpose: string;
}

export interface ArmResult {
  armed: number;
  skipped: number;
}

export class WarmupScheduler {
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private quietFlushTimer?: ReturnType<typeof setTimeout>;

  constructor(
    private readonly channel: LarkChannel,
    private readonly ownerId: string | undefined,
    private readonly state: StateStore,
  ) {}

  /** 启动时按 state 里的 active plan 重新布点。 */
  rearmFromState(): void {
    const st = this.state.get();
    const plans = activePlanIndex(st);
    this.scheduleQuietFlush(new Date());
    if (Object.keys(plans).length === 0) return;
    this.armPlans(Object.values(plans));
  }

  /** 给多个计划布置未来预热定时器（用于今日 + 明日并存）。 */
  armPlans(records: Array<Record<string, unknown>>): ArmResult {
    this.cancelAll();
    const total: ArmResult = { armed: 0, skipped: 0 };
    for (const record of records) {
      const result = this.armOne(record);
      total.armed += result.armed;
      total.skipped += result.skipped;
    }
    return total;
  }

  /** 给一个计划布置全部未来预热定时器（先清旧）。 */
  arm(record: Record<string, unknown>): ArmResult {
    return this.armPlans([record]);
  }

  private armOne(record: Record<string, unknown>): ArmResult {
    const st = this.state.get();
    if (planIsExpired(record, new Date())) {
      activePlanIndex(st);
      st.executedWarmups = [];
      this.state.save();
      return { armed: 0, skipped: 0 };
    }
    const planId = String(record['plan_id'] ?? '');
    const events = (record['events'] as EventRec[] | undefined) ?? [];
    const now = Date.now();
    const executed = new Set(this.state.get().executedWarmups);
    let armed = 0;
    let skipped = 0;
    for (const ev of events) {
      if (!isWarmupEvent(ev)) continue;
      const key = warmupKey(planId, ev);
      const at = new Date(ev.at).getTime();
      if (Number.isNaN(at) || at <= now || executed.has(key)) {
        skipped += 1; // 过期 / 已执行：跳过（过期预热没意义）
        continue;
      }
      const timer = setTimeout(() => void this.fire(planId, ev), at - now);
      this.timers.set(key, timer);
      armed += 1;
    }
    console.log(`[scheduler] ${planId} 已布置 ${armed} 个预热节点，跳过 ${skipped} 个（过期/已执行）。`);
    return { armed, skipped };
  }

  cancelAll(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
    if (this.quietFlushTimer) clearTimeout(this.quietFlushTimer);
    this.quietFlushTimer = undefined;
  }

  /** 当前已布置的定时器数（测试/诊断用）。 */
  get pending(): number {
    return this.timers.size;
  }

  private async fire(planId: string, ev: EventRec): Promise<void> {
    const plans = activePlanIndex(this.state.get());
    const active = Object.values(plans).find((plan) => plan['plan_id'] === planId) ?? null;
    if (!active || active['plan_id'] !== planId || active['status'] !== 'active') return; // 计划已变/取消
    const key = warmupKey(planId, ev);
    const st = this.state.get();
    if (st.executedWarmups.includes(key)) return;
    const label = PROVIDER_LABEL[ev.agent] ?? ev.agent;
    // 迟到保护：睡眠/卡顿导致远超计划时刻才触发的，直接跳过——过时预热没意义。
    if (Date.now() > new Date(ev.at).getTime() + FIRE_GRACE_MS) {
      st.executedWarmups.push(key);
      this.state.appendEvent({ type: 'warmup', agent: ev.agent, result: 'skip', detail: hm(ev.at) });
      this.state.save();
      await this.notify(`⏭️ 跳过 ${label} 的预热（${hm(ev.at)} 已过时，可能因睡眠/关机错过）。`, { respectQuiet: true });
      return;
    }
    st.executedWarmups.push(key);
    this.state.save();
    try {
      const reply = await getProvider(ev.agent).warmup(WARMUP_PROMPT);
      this.state.appendEvent({ type: 'warmup', agent: ev.agent, result: 'ok', detail: hm(ev.at) });
      this.state.save();
      await this.notify(`✅ ${label} 已按计划预热（${hm(ev.at)}）。\n模型回复：「${reply || '(空)'}」`, { respectQuiet: true });
    } catch (e) {
      this.state.appendEvent({ type: 'warmup', agent: ev.agent, result: 'fail', detail: (e as Error).message.slice(0, 80) });
      this.state.save();
      await this.notify(`❌ ${label} 预热失败（${hm(ev.at)}）：${(e as Error).message}`, { respectQuiet: true });
    }
  }

  /** 测试用：delayMs 后通过真实定时器路径跑一次预热并私聊回执。不碰 activePlan/state。
   *  用来在白天验证"定时器触发 → 预热执行 → 飞书回执"整条链路是否通。 */
  testFire(agent: string, delayMs = 8000): void {
    const label = PROVIDER_LABEL[agent] ?? agent;
    setTimeout(() => {
      void (async () => {
        try {
          const reply = await getProvider(agent).warmup(WARMUP_PROMPT);
          await this.notify(
            `✅【测试】${label} 预热已执行（真打了一次 API）。\n模型回复：「${reply || '(空)'}」\n注：用的是 \`claude -p\` headless 调用，不会出现在你交互式 Claude Code 里，但额度计在同一账号。`,
          );
        } catch (e) {
          await this.notify(`❌【测试】${label} 预热失败：${(e as Error).message}`);
        }
      })();
    }, delayMs);
  }

  private async notify(text: string, opts: { respectQuiet?: boolean } = {}): Promise<void> {
    if (!this.ownerId) return;
    if (opts.respectQuiet && isQuiet(new Date())) {
      const dueAt = nextNonQuietTime(new Date());
      const queue = this.state.get().pendingQuietMessages ?? (this.state.get().pendingQuietMessages = []);
      queue.push({ text, dueAt: dueAt.toISOString(), kind: 'warmup' });
      this.state.save();
      this.scheduleQuietFlush(new Date());
      console.log(`[scheduler] quiet message queued dueAt=${dueAt.toISOString()}`);
      return;
    }
    try {
      await this.channel.send(this.ownerId, { text });
    } catch {
      // 回执失败不致命
    }
  }

  private scheduleQuietFlush(now: Date): void {
    if (this.quietFlushTimer) clearTimeout(this.quietFlushTimer);
    const queue = this.state.get().pendingQuietMessages ?? [];
    if (queue.length === 0) return;
    const dueAt = queue
      .map((m) => new Date(m.dueAt).getTime())
      .filter((t) => !Number.isNaN(t))
      .sort((a, b) => a - b)[0] ?? nextNonQuietTime(now).getTime();
    const delay = Math.max(0, dueAt - now.getTime());
    this.quietFlushTimer = setTimeout(() => void this.flushQuietMessages(), delay);
  }

  private async flushQuietMessages(): Promise<void> {
    if (!this.ownerId) return;
    if (isQuiet(new Date())) {
      this.scheduleQuietFlush(new Date());
      return;
    }
    const queue = this.state.get().pendingQuietMessages ?? [];
    while (queue.length) {
      const head = queue[0]!;
      const dueAt = new Date(head.dueAt).getTime();
      if (!Number.isNaN(dueAt) && dueAt > Date.now()) break;
      try {
        await this.channel.send(this.ownerId, { text: head.text });
      } catch {
        break;
      }
      queue.shift();
      this.state.save();
    }
    this.scheduleQuietFlush(new Date());
  }
}

function warmupKey(planId: string, ev: EventRec): string {
  return `${planId}:${ev.agent}:${ev.at}`;
}

function isWarmupEvent(ev: EventRec): boolean {
  return String(ev.kind ?? ev.type ?? 'warmup') === 'warmup';
}

function hm(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

function isQuiet(now: Date): boolean {
  const h = now.getHours();
  return h >= 23 || h < 8;
}

function nextNonQuietTime(now: Date): Date {
  const d = new Date(now);
  if (d.getHours() >= 23) d.setDate(d.getDate() + 1);
  d.setHours(8, 0, 0, 0);
  return d;
}
