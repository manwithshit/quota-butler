// 卡片回调 / 文字命令路由。移植自 Python handler.py。
// M2：完整规划交互闭环；adopt 仅标记 active（预热实际调度在 M3）。

import { AgentState, detectAgents, isSchedulable, planningUsageForStatus, usableForPlanning, type PlanningUsageSnapshot } from './agent_status.js';
import {
  buildActivePlanCard,
  buildAgentControlCard,
  buildBedtimeCard,
  buildCommandMenuCard,
  buildCurrentPlansCard,
  buildManualWarmupCard,
  buildScheduleCard,
  buildStatusCard,
  buildTimeCard,
  buildTimeModeCard,
  type Card,
} from './notify.js';
import { buildPlan } from './planner.js';
import { planRecord, validatePlanRecord } from './plan_record.js';
import { getProvider } from './providers/index.js';
import {
  normalizeHHmm,
  parsePlanRequest,
  validateWarmupTimes,
  validateFlowContext,
  type PlanRequest,
} from './schedule_flow.js';
import { activePlanIndex, planDate, type StateStore } from './state.js';
import type { Usage } from './providers/index.js';
import { WARMUP_PROMPT, type WarmupScheduler } from './scheduler.js';

export interface HandlerCtx {
  state: StateStore;
  send: (card: Card) => Promise<void>;
  receipt: (text: string) => Promise<void>;
  scheduler?: WarmupScheduler;
  chatId?: string;
  userId?: string;
}

interface WarmupEventLike {
  kind?: unknown;
  type?: unknown;
  at?: unknown;
}

export async function handleAction(payload: Record<string, unknown>, ctx: HandlerCtx): Promise<void> {
  const action = String(payload['action'] ?? '');
  const st = ctx.state.get();
  st.lastAction = action;
  st.lastRunAt = new Date().toISOString();
  logAction(payload, st, ctx);

  switch (action) {
    case 'menu':
      return ctx.send(buildCommandMenuCard());

    case 'query_status': {
      const statuses = await detectAgents();
      for (const [p, s] of Object.entries(statuses)) if (s.usage) ctx.state.recordUsageSnapshot(p, s.usage);
      ctx.state.save();
      return ctx.send(buildStatusCard(statuses, st.usageSnapshots));
    }

    case 'daily_report': {
      const statuses = await detectAgents();
      for (const [p, s] of Object.entries(statuses)) if (s.usage) ctx.state.recordUsageSnapshot(p, s.usage);
      ctx.state.save();
      const now = new Date();
      const pad = (n: number) => String(n).padStart(2, '0');
      const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
      return ctx.send(
        buildBedtimeCard(statuses, st.lastPlanRequest, {
          eventLog: st.eventLog,
          dayStart: st.dayStartUsage[today],
          activePlan: st.activePlan,
          now,
        }),
      );
    }

    case 'schedule_intent': {
      const target = targetDate(payload);
      const plans = activePlanIndex(st);
      if (plans[target]) return ctx.send(buildCurrentPlansCard(plansForDisplay(plans, st.executedWarmups)));
      return ctx.send(buildTimeModeCard(target, st.lastPlanRequest));
    }

    case 'schedule_flow':
      return handleScheduleFlow(payload, ctx);

    case 'adjust_schedule_agents':
    case 'redetect_agents': {
      const request = requestFromPayload(payload, 1);
      const statuses = await detectAgents();
      return ctx.send(buildAgentControlCard(request, statuses));
    }

    case 'adjust_schedule_time':
      return ctx.send(buildTimeCard(requestFromPayload(payload, 1)));

    case 'adopt_schedule':
      return adoptSchedule(payload, ctx);

    case 'view_schedule': {
      const plans = activePlanIndex(st);
      if (Object.keys(plans).length === 0) return ctx.receipt('当前没有生效计划');
      return ctx.send(buildCurrentPlansCard(plansForDisplay(plans, st.executedWarmups)));
    }

    case 'cancel_schedule': {
      const target = String(payload['target_date'] ?? '');
      const plans = activePlanIndex(st);
      const targets = target ? [target] : Object.keys(plans);
      let removed = 0;
      for (const day of targets) {
        const plan = plans[day];
        if (!plan || !hasPendingWarmup(plan, st.executedWarmups)) continue;
        delete plans[day];
        removed += 1;
      }
      if (!removed) return ctx.receipt('没有可以取消的未执行任务');
      st.plansByDate = plans;
      st.activePlan = null;
      activePlanIndex(st);
      ctx.scheduler?.armPlans(Object.values(plans));
      ctx.state.save();
      return ctx.receipt('✅ 已取消计划，未执行任务已删除');
    }

    case 'warmup_now':
    case 'scheduled_warmup':
      return warmup(payload, ctx);

    case 'manual_warmup': {
      const statuses = await detectAgents();
      for (const [p, s] of Object.entries(statuses)) if (s.usage) ctx.state.recordUsageSnapshot(p, s.usage);
      ctx.state.save();
      return ctx.send(buildManualWarmupCard(statuses));
    }

    case 'manual_warmup_time':
    case 'manual_warmup_generate':
    case 'adopt_manual_warmup':
      return ctx.receipt('手动定时预热未开放，请使用「立即预热」或「设置明日计划」。');

    case 'debug_test_warmup': {
      const p = String(payload['provider'] ?? 'cc');
      const provider = p === 'codex' ? 'codex' : 'cc';
      if (!ctx.scheduler) return ctx.receipt('调度器未就绪（请在 daemon / run 进程内测试）');
      ctx.scheduler.testFire(provider, 8000);
      return ctx.receipt(`好，约 8 秒后用真实定时器触发一次【${provider}】测试预热，等回执。`);
    }

    case 'recovery_snooze': {
      const minutes = Math.max(1, Math.min(Number(payload['minutes'] ?? 30), 24 * 60));
      st.pendingRecovery = {
        provider: String(payload['provider'] ?? ''),
        windowKey: String(payload['window_key'] ?? ''),
        dueAt: new Date(Date.now() + minutes * 60000).toISOString(),
      };
      ctx.state.save();
      return ctx.receipt(`好，${minutes} 分钟后再提醒`);
    }

    case 'recovery_skip':
      st.pendingRecovery = null;
      ctx.state.save();
      return;

    case 'tomorrow_skip':
      return ctx.receipt('好的，收到。好好休息，也是在给大脑充电 🌙');

    case 'schedule_remind_only': {
      const activeForRemind = st.activePlan as Record<string, unknown> | null;
      if (activeForRemind && activeForRemind['status'] === 'active') {
        // 已采用计划后再点"仅提醒"自相矛盾：说实话 + 给出可取消的当前计划卡。
        await ctx.receipt('你已经采用了一个计划，「仅提醒」不生效。要改成仅提醒，请先取消下面这个计划。');
        return ctx.send(buildActivePlanCard(activeForRemind));
      }
      st.proposedPlanId = null; // 放弃当前预览
      ctx.state.save();
      return ctx.receipt('好的，仅在额度恢复时提醒，不会创建预热任务。');
    }

    default:
      return ctx.receipt('该卡片已失效，请重新打开菜单');
  }
}

async function handleScheduleFlow(payload: Record<string, unknown>, ctx: HandlerCtx): Promise<void> {
  try {
    validateFlowContext(payload);
  } catch (e) {
    return ctx.receipt((e as Error).message);
  }
  const step = String(payload['step'] ?? '');
  const requestRaw: Record<string, unknown> = { ...((payload['request'] as Record<string, unknown>) ?? {}) };
  if (requestRaw['target_date'] == null) requestRaw['target_date'] = payload['target_date'];

  if (step === 'edit_time_point' || step === 'edit_time_range') {
    requestRaw['time_mode'] = step.endsWith('point') ? 'point' : 'range';
    let request: PlanRequest;
    try {
      request = parsePlanRequest(requestRaw, 1);
    } catch {
      request = {
        targetDate: String(payload['target_date']),
        timeMode: step.endsWith('point') ? 'point' : 'range',
        workStart: '09:00',
        workEnd: step.endsWith('point') ? '16:31' : '17:00',
        agentStrategy: 'auto',
        firstWarmup: '06:30',
        secondWarmup: '11:31',
      };
    }
    return ctx.send(buildTimeCard(request));
  }

  if (step !== 'generate_plan') return ctx.receipt('该卡片已失效，请重新打开菜单');

  const form = payload['form_value'];
  if (form && typeof form === 'object') {
    for (const key of ['work_start', 'work_end', 'first_warmup', 'second_warmup']) {
      const v = (form as Record<string, unknown>)[key];
      if (v != null) requestRaw[key] = v;
    }
  }
  if (payload['agent_strategy']) requestRaw['agent_strategy'] = payload['agent_strategy'];

  const statuses = await detectAgents();
  for (const [provider, s] of Object.entries(statuses)) {
    if (s.usage) ctx.state.recordUsageSnapshot(provider, s.usage);
  }
  ctx.state.save();
  const planningAt = planningReferenceTime(requestRaw);
  const usages = collectPlanningUsages(statuses, ctx.state.get().usageSnapshots, planningAt);
  if (Object.keys(usages).length === 0) {
    return ctx.receipt('暂时没有可用于规划的 AI 工具（5 小时或周额度已见底）');
  }

  try {
    const request = parsePlanRequest(requestRaw, Object.keys(usages).length);
    const plan = buildPlan(request, usages);
    await ctx.send(buildScheduleCard(plan));
    ctx.state.get().proposedPlanId = planRecord(plan).plan_id;
    ctx.state.save();
  } catch (e) {
    const fallback: PlanRequest = {
      targetDate: String(requestRaw['target_date']),
      timeMode: String(requestRaw['time_mode'] ?? 'point') as PlanRequest['timeMode'],
      workStart: String(requestRaw['work_start'] ?? '09:00').split(/\s+/)[0]!,
      workEnd: String(requestRaw['work_end'] ?? '14:00').split(/\s+/)[0]!,
      agentStrategy: String(requestRaw['agent_strategy'] ?? 'auto') as PlanRequest['agentStrategy'],
      firstWarmup: String(requestRaw['first_warmup'] ?? '06:30').split(/\s+/)[0]!,
      secondWarmup: String(requestRaw['second_warmup'] ?? '11:31').split(/\s+/)[0]!,
    };
    await ctx.send(buildTimeCard(fallback, (e as Error).message));
  }
}

async function adoptSchedule(payload: Record<string, unknown>, ctx: HandlerCtx): Promise<void> {
  const candidate = payload['plan'];
  if (!candidate || typeof candidate !== 'object' || (candidate as Record<string, unknown>)['plan_version'] !== 3) {
    return ctx.receipt('该计划已失效，请重新规划');
  }
  const st = ctx.state.get();
  const planId = String((candidate as Record<string, unknown>)['plan_id'] ?? '');
  if (st.proposedPlanId && planId !== st.proposedPlanId) {
    return ctx.receipt('该计划不是最新预览，请采用最新计划');
  }
  let record;
  try {
      const adjusted = applyAdoptForm(candidate as Record<string, unknown>, payload['form_value']);
      const target = planDate(adjusted);
      const plans = activePlanIndex(st);
      if (target && plans[target]) {
        await ctx.receipt(`${target} 已有计划，请先取消后再重新设置`);
      return ctx.send(buildCurrentPlansCard(plansForDisplay(plans, st.executedWarmups)));
    }
    record = validatePlanRecord(adjusted);
    if (!hasFutureWarmup(record, new Date())) {
      return ctx.receipt('❌ 该计划的预热时间已过，请重新生成计划');
    }
  } catch (e) {
    return ctx.receipt(`❌ 计划不可采用：${(e as Error).message}`);
  }
  const statuses = await detectAgents(record.agents);
  for (const [provider, s] of Object.entries(statuses)) if (s.usage) ctx.state.recordUsageSnapshot(provider, s.usage);
  ctx.state.save();
  const planningAt = new Date(record.work_start);
  const usages = collectPlanningUsages(statuses, ctx.state.get().usageSnapshots, planningAt);
  const unavailable = record.agents.filter(
    (p) => !statuses[p] || !usages[p],
  );
  if (unavailable.length) {
    return ctx.receipt(`❌ AI 工具状态已变化，请重新生成计划：${unavailable.join('、')}`);
  }
  record.status = 'active';
  record.adopted_at = new Date().toISOString();
  const target = planDate(record);
  st.plansByDate = { ...activePlanIndex(st), [target]: record as unknown as Record<string, unknown> };
  activePlanIndex(st);
  st.proposedPlanId = null;
  const r = record.request as Record<string, unknown>;
  st.lastPlanRequest = {
    timeMode: String(r['time_mode'] ?? 'point'),
    workStart: String(r['work_start'] ?? ''),
    workEnd: String(r['work_end'] ?? ''),
    agentStrategy: String(r['agent_strategy'] ?? 'auto'),
  };
  ctx.state.save();
  const result = ctx.scheduler?.armPlans(Object.values(st.plansByDate));
  const armed = result?.armed ?? countFutureWarmups(record, new Date());
  const skipped = result?.skipped ?? Math.max(0, record.events.length - armed);
  const skipText = skipped > 0 ? `，跳过 ${skipped} 个过期/已执行任务` : '';
  return ctx.receipt(`✅ 已采用计划，已布置 ${armed} 个预热任务${skipText}`);
}

async function warmup(payload: Record<string, unknown>, ctx: HandlerCtx): Promise<void> {
  const provider = String(payload['provider'] ?? '');
  if (provider !== 'cc' && provider !== 'codex') return ctx.receipt('预热工具无效');
  const st = ctx.state.get();
  const windowKey = String(payload['window_key'] ?? '');
  if (windowKey && st.lastWarmedWindows[provider] === windowKey) {
    return ctx.receipt('这个窗口已经预热过了');
  }
  let reply: string;
  try {
    reply = await getProvider(provider).warmup(WARMUP_PROMPT);
  } catch (e) {
    return ctx.receipt(`❌ ${provider} 预热失败：${(e as Error).message}`);
  }
  if (windowKey) {
    st.lastWarmedWindows[provider] = windowKey;
    ctx.state.save();
  }
  return ctx.receipt(`✅ ${provider} 已预热，新的额度窗口已开始。\n模型回复：「${reply || '(空)'}」`);
}

function requestFromPayload(payload: Record<string, unknown>, availableCount: number): PlanRequest {
  return parsePlanRequest((payload['request'] as Record<string, unknown>) ?? {}, availableCount);
}

/** 当前是否有生效计划（自动或手动）；有则返回记录，供"先取消再设置"的闭环用。 */
function activePlanIfAny(st: { activePlan: unknown }): Record<string, unknown> | null {
  const a = st.activePlan as Record<string, unknown> | null;
  return a && a['status'] === 'active' ? a : null;
}

async function sendExistingPlan(plan: Record<string, unknown>, ctx: HandlerCtx): Promise<void> {
  await ctx.receipt('已有生效计划，要改的话先取消下面这张：');
  return ctx.send(buildActivePlanCard(plan));
}

function targetDate(payload: Record<string, unknown>): string {
  if (String(payload['intent'] ?? '') === 'tomorrow') return tomorrowIso();
  const raw = String(payload['target_date'] ?? '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  return tomorrowIso();
}

function tomorrowIso(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function collectPlanningUsages(
  statuses: Record<string, { provider: string; state: AgentState; usage?: Usage; detail?: string }>,
  snapshots: Record<string, PlanningUsageSnapshot>,
  planningAt: Date,
): Record<string, Usage> {
  const usages: Record<string, Usage> = {};
  for (const [provider, status] of Object.entries(statuses)) {
    const usage = planningUsageForStatus(status, snapshots[provider], planningAt);
    if (usage) usages[provider] = usage;
  }
  return usages;
}

function planningReferenceTime(raw: Record<string, unknown>): Date {
  const target = String(raw['target_date'] ?? '');
  const start = String(raw['work_start'] ?? '09:00').split(/\s+/)[0] ?? '09:00';
  const d = new Date(`${target}T${start}:00`);
  return Number.isNaN(d.getTime()) ? new Date() : d;
}

function hasFutureWarmup(plan: { events: WarmupEventLike[] }, now: Date): boolean {
  return countFutureWarmups(plan, now) > 0;
}

function countFutureWarmups(plan: { events: WarmupEventLike[] }, now: Date): number {
  return plan.events.filter((ev) => {
    const kind = String(ev.kind ?? ev.type ?? 'warmup');
    const at = new Date(String(ev.at)).getTime();
    return kind === 'warmup' && !Number.isNaN(at) && at > now.getTime();
  }).length;
}

function logAction(
  payload: Record<string, unknown>,
  st: { activePlan: unknown; plansByDate: Record<string, Record<string, unknown>> },
  ctx: Pick<HandlerCtx, 'chatId' | 'userId'>,
): void {
  const active = st.activePlan as Record<string, unknown> | null;
  const plan = payload['plan'] as Record<string, unknown> | undefined;
  const summary = {
    action: String(payload['action'] ?? ''),
    pid: process.pid,
    chat_id: ctx.chatId ?? '',
    user_id: ctx.userId ?? '',
    target_date: String(payload['target_date'] ?? ''),
    plan_id: String(payload['plan_id'] ?? plan?.['plan_id'] ?? ''),
    active_plan_id: String(active?.['plan_id'] ?? ''),
    plan_dates: Object.keys(st.plansByDate ?? {}).sort(),
  };
  console.log(`[handler] action ${JSON.stringify(summary)}`);
}

function plansForDisplay(plans: Record<string, Record<string, unknown>>, executedWarmups: string[]): Record<string, Record<string, unknown>> {
  return Object.fromEntries(
    Object.entries(plans).map(([day, plan]) => [day, { ...plan, executed_warmups: executedWarmups }]),
  );
}

function applyAdoptForm(candidate: Record<string, unknown>, formValue: unknown): Record<string, unknown> {
  if (!formValue || typeof formValue !== 'object') return candidate;
  const form = formValue as Record<string, unknown>;
  if (form['first_warmup'] == null && form['second_warmup'] == null) return candidate;
  const record = structuredClone(candidate) as Record<string, unknown>;
  const events = ([...((record['events'] as Array<Record<string, unknown>> | undefined) ?? [])] as Array<Record<string, unknown>>)
    .map((event) => ({ ...event }));
  if (events.length < 2) throw new Error('计划缺少两次预热时间');
  const first = normalizeHHmm(form['first_warmup'] ?? hhmmOf(events[0]?.['at']));
  const second = normalizeHHmm(form['second_warmup'] ?? hhmmOf(events[1]?.['at']));
  validateWarmupTimes(first, second);
  const workStart = new Date(String(record['work_start']));
  const warmups = [setTime(workStart, first), setTime(workStart, second)].sort((a, b) => a.getTime() - b.getTime());
  const workEnd = new Date(warmups[1]!.getTime() + 5 * 3600000);
  events.sort((a, b) => String(a['at']).localeCompare(String(b['at'])));
  events[0]!['at'] = localIso(warmups[0]!);
  events[1]!['at'] = localIso(warmups[1]!);
  record['events'] = events.sort((a, b) => String(a['at']).localeCompare(String(b['at'])));
  record['work_end'] = localIso(workEnd);
  record['request'] = {
    ...((record['request'] as Record<string, unknown>) ?? {}),
    first_warmup: hhmmOf(events[0]!['at']),
    second_warmup: hhmmOf(events[1]!['at']),
    work_end: hhmmOf(workEnd),
  };
  return record;
}

function hasPendingWarmup(plan: Record<string, unknown>, executedWarmups: string[]): boolean {
  const planId = String(plan['plan_id'] ?? '');
  const now = Date.now();
  for (const ev of (plan['events'] as Array<Record<string, unknown>> | undefined) ?? []) {
    const kind = String(ev['kind'] ?? ev['type'] ?? 'warmup');
    if (kind !== 'warmup') continue;
    const key = `${planId}:${String(ev['agent'])}:${String(ev['at'])}`;
    if (!executedWarmups.includes(key) && new Date(String(ev['at'])).getTime() > now) return true;
  }
  return false;
}

function setTime(base: Date, hhmm: string): Date {
  const [hour, minute] = hhmm.split(':').map(Number) as [number, number];
  return new Date(base.getFullYear(), base.getMonth(), base.getDate(), hour, minute, 0, 0);
}

function localIso(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function hhmmOf(value: unknown): string {
  const text = String(value ?? '');
  return text.includes('T') ? text.slice(11, 16) : text.slice(0, 5);
}
