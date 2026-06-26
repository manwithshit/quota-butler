// 本地状态持久化（~/.quota-butler/state.json）。单进程内存态 + 落盘。
// 移植自 Python state.py，并新增 usageSnapshots（last-good 快照，cc-switch 同款）。

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import type { ProviderTier, Usage } from './providers/index.js';

const STATE_PATH = join(homedir(), '.quota-butler', 'state.json');

export interface UsageSnapshot {
  fiveHourUtil: number | null; // 免费档 Codex 无 5h 窗口 → null
  fiveHourResetAt: string | null;
  sevenDayUtil: number | null;
  monthlyUtil?: number | null; // 免费档 Codex 月度窗口
  capturedAt: string; // ISO
}

/** 上次采用计划的设置参数（不含日期），供"复用上次"用；不随计划过期清除。 */
export interface LastPlanRequest {
  timeMode: string;
  workStart: string;
  workEnd: string;
  agentStrategy: string;
}

/** 日报用的事件流水（预热执行 / 额度恢复），保留最近几天、自动裁剪。 */
export interface DailyEvent {
  ts: string; // ISO
  type: 'warmup' | 'recovery';
  agent?: string;
  result?: 'ok' | 'fail' | 'skip';
  detail?: string;
}

/** 某天"日初"各窗口利用率快照，用于算当日消耗。 */
export interface DayQuotaSnap {
  fiveHourUtil: number | null;
  sevenDayUtil: number | null;
  monthlyUtil: number | null;
}

const EVENTLOG_KEEP_MS = 3 * 86400000; // 事件流水保留 3 天
const DAYSTART_KEEP_DAYS = 4; // 日初快照保留 4 天

export interface State {
  usageSnapshots: Record<string, UsageSnapshot>;
  // 以下字段在 M2/M3 启用：
  activePlan: unknown;
  plansByDate: Record<string, Record<string, unknown>>;
  proposedPlanId: string | null;
  pendingRecovery: unknown;
  // 安静时段/计划期内检测到的恢复提醒先入此队列，待可打扰时统一补发（不丢）。
  pendingNotifications: Array<{ provider: string; windowKey: string }>;
  lastWarmedWindows: Record<string, string>;
  lastRecoveryNotifiedWindows: Record<string, string>;
  lastBedtimePromptDate: string | null;
  providerSnapshots: Record<string, { utilization: number; resetAt: string | null }>;
  // 上次成功读到的各家档位（monthly-only=免费档 Codex / has-5h=付费档/CC）。
  // 跨进程持久化：免费档一旦认出，后续只读感知就不再触发会烧月额度的 codex exec 刷新。
  providerTiers: Record<string, ProviderTier>;
  executedWarmups: string[]; // `${planId}:${agent}:${at}`，防进程重启后重复触发
  lastPlanRequest: LastPlanRequest | null; // "复用上次"用
  lastAction: string | null;
  lastRunAt: string | null;
  eventLog: DailyEvent[]; // 日报事件流水（预热/恢复）
  dayStartUsage: Record<string, Record<string, DayQuotaSnap>>; // date → provider → 日初快照
}

function defaultState(): State {
  return {
    usageSnapshots: {},
    activePlan: null,
    plansByDate: {},
    proposedPlanId: null,
    pendingRecovery: null,
    pendingNotifications: [],
    lastWarmedWindows: {},
    lastRecoveryNotifiedWindows: {},
    lastBedtimePromptDate: null,
    providerSnapshots: {},
    providerTiers: {},
    executedWarmups: [],
    lastPlanRequest: null,
    lastAction: null,
    lastRunAt: null,
    eventLog: [],
    dayStartUsage: {},
  };
}

export class StateStore {
  private state: State;
  constructor(private readonly path: string = STATE_PATH) {
    this.state = load(path);
  }
  get(): State {
    return this.state;
  }
  save(): void {
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(this.state, null, 2), 'utf-8');
  }
  /** 追加一条日报事件（预热/恢复）并裁剪过期条目。调用方负责 save()。 */
  appendEvent(event: Omit<DailyEvent, 'ts'> & { ts?: string }): void {
    this.state.eventLog.push({ ts: event.ts ?? new Date().toISOString(), ...event });
    const cutoff = Date.now() - EVENTLOG_KEEP_MS;
    this.state.eventLog = this.state.eventLog.filter((e) => {
      const t = new Date(e.ts).getTime();
      return Number.isNaN(t) || t >= cutoff;
    });
  }

  /** 每天首次观测时记一张"日初"快照（已有当天的则跳过），用于算当日消耗。调用方负责 save()。 */
  recordDayStart(date: string, statuses: Record<string, { usage?: Usage }>): void {
    if (this.state.dayStartUsage[date]) return;
    const snap: Record<string, DayQuotaSnap> = {};
    for (const [provider, s] of Object.entries(statuses)) {
      if (!s.usage) continue;
      snap[provider] = {
        fiveHourUtil: s.usage.fiveHour ? s.usage.fiveHour.utilization : null,
        sevenDayUtil: s.usage.sevenDay ? s.usage.sevenDay.utilization : null,
        monthlyUtil: s.usage.monthly ? s.usage.monthly.utilization : null,
      };
    }
    if (Object.keys(snap).length === 0) return; // 一个都没读到就先不记，等下一拍
    this.state.dayStartUsage[date] = snap;
    const dates = Object.keys(this.state.dayStartUsage).sort();
    while (dates.length > DAYSTART_KEEP_DAYS) delete this.state.dayStartUsage[dates.shift()!];
  }

  /** 读 usage 成功后存快照，供令牌过期 / 接口失败时回显。 */
  recordUsageSnapshot(provider: string, usage: Usage): void {
    this.state.usageSnapshots[provider] = {
      fiveHourUtil: usage.fiveHour ? usage.fiveHour.utilization : null,
      fiveHourResetAt: usage.fiveHour?.resetsAt ? usage.fiveHour.resetsAt.toISOString() : null,
      sevenDayUtil: usage.sevenDay ? usage.sevenDay.utilization : null,
      monthlyUtil: usage.monthly ? usage.monthly.utilization : null,
      capturedAt: new Date().toISOString(),
    };
  }
}

/** 计划是否已过结束时间（过期应清除：否则一直挂 active、挡新计划、查看显示旧的）。 */
export function planIsExpired(activePlan: unknown, now: Date): boolean {
  if (!activePlan || typeof activePlan !== 'object') return false;
  const end = new Date(String((activePlan as Record<string, unknown>)['work_end']));
  return !Number.isNaN(end.getTime()) && end.getTime() <= now.getTime();
}

export function planDate(record: unknown): string {
  const r = record as Record<string, unknown> | null;
  if (!r) return '';
  const workStart = String(r['work_start'] ?? '');
  if (workStart.length >= 10) return workStart.slice(0, 10);
  const req = r['request'] as Record<string, unknown> | undefined;
  const target = String(req?.['target_date'] ?? '');
  return /^\d{4}-\d{2}-\d{2}$/.test(target) ? target : '';
}

export function activePlanIndex(state: State, now = new Date()): Record<string, Record<string, unknown>> {
  const plans: Record<string, Record<string, unknown>> = {};
  for (const [day, raw] of Object.entries(state.plansByDate ?? {})) {
    if (raw && raw['status'] === 'active' && !planIsExpired(raw, now)) plans[day] = raw;
  }
  const active = state.activePlan as Record<string, unknown> | null;
  if (active && active['status'] === 'active' && !planIsExpired(active, now)) {
    const day = planDate(active);
    if (day && !plans[day]) plans[day] = active;
  }
  state.plansByDate = plans;
  const today = localDate(now);
  const tomorrow = localDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
  state.activePlan = plans[today] ?? plans[tomorrow] ?? plans[Object.keys(plans).sort()[0]!] ?? null;
  return plans;
}

function localDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function load(path: string): State {
  try {
    const raw = JSON.parse(readFileSync(path, 'utf-8')) as Partial<State>;
    return { ...defaultState(), ...raw };
  } catch {
    return defaultState();
  }
}
