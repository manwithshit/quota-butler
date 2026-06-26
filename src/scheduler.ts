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
  kind: string;
  at: string;
  purpose: string;
}

export class WarmupScheduler {
  private timers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(
    private readonly channel: LarkChannel,
    private readonly ownerId: string | undefined,
    private readonly state: StateStore,
  ) {}

  /** 启动时按 state 里的 active plan 重新布点。 */
  rearmFromState(): void {
    const st = this.state.get();
    const plans = activePlanIndex(st);
    if (Object.keys(plans).length === 0) return;
    this.armPlans(Object.values(plans));
  }

  /** 给多个计划布置未来预热定时器（用于今日 + 明日并存）。 */
  armPlans(records: Array<Record<string, unknown>>): void {
    this.cancelAll();
    for (const record of records) this.armOne(record);
  }

  /** 给一个计划布置全部未来预热定时器（先清旧）。 */
  arm(record: Record<string, unknown>): void {
    this.armPlans([record]);
  }

  private armOne(record: Record<string, unknown>): void {
    const st = this.state.get();
    if (planIsExpired(record, new Date())) {
      activePlanIndex(st);
      st.executedWarmups = [];
      this.state.save();
      return;
    }
    const planId = String(record['plan_id'] ?? '');
    const events = (record['events'] as EventRec[] | undefined) ?? [];
    const now = Date.now();
    const executed = new Set(this.state.get().executedWarmups);
    let armed = 0;
    let skipped = 0;
    for (const ev of events) {
      if (ev.kind !== 'warmup') continue;
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
  }

  cancelAll(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
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
      await this.notify(`⏭️ 跳过 ${label} 的预热（${hm(ev.at)} 已过时，可能因睡眠/关机错过）。`);
      return;
    }
    st.executedWarmups.push(key);
    this.state.save();
    try {
      const reply = await getProvider(ev.agent).warmup(WARMUP_PROMPT);
      this.state.appendEvent({ type: 'warmup', agent: ev.agent, result: 'ok', detail: hm(ev.at) });
      this.state.save();
      await this.notify(`✅ ${label} 已按计划预热（${hm(ev.at)}）。\n模型回复：「${reply || '(空)'}」`);
    } catch (e) {
      this.state.appendEvent({ type: 'warmup', agent: ev.agent, result: 'fail', detail: (e as Error).message.slice(0, 80) });
      this.state.save();
      await this.notify(`❌ ${label} 预热失败（${hm(ev.at)}）：${(e as Error).message}`);
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

  private async notify(text: string): Promise<void> {
    if (!this.ownerId) return;
    try {
      await this.channel.send(this.ownerId, { text });
    } catch {
      // 回执失败不致命
    }
  }
}

function warmupKey(planId: string, ev: EventRec): string {
  return `${planId}:${ev.agent}:${ev.at}`;
}

function hm(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}
