import type { Usage } from './providers/index.js';
import type { UsageSnapshot } from './state.js';

const ACTIVATION_DELAY_MS = 90_000;
const PREWORK_LEAD_MS = 10 * 60_000;

export interface WeeklyActivationOption {
  at: Date;
  resetAt: Date;
  windowKey: string;
}

/**
 * Codex 周额度不会在重置点自动开始新周期；必须发出第一条消息。
 * 只有已知重置点严格早于计划结束时，才创建一次“新周期激活”任务。
 */
export function weeklyActivationForRange(
  resetAt: Date | null,
  workStart: Date,
  workEnd: Date,
): WeeklyActivationOption | null {
  if (!resetAt || Number.isNaN(resetAt.getTime())) return null;
  if (resetAt.getTime() >= workEnd.getTime()) return null;

  const afterReset = new Date(resetAt.getTime() + ACTIVATION_DELAY_MS);
  const beforeWork = new Date(workStart.getTime() - PREWORK_LEAD_MS);
  const at = resetAt.getTime() <= workStart.getTime()
    ? new Date(Math.max(beforeWork.getTime(), afterReset.getTime()))
    : afterReset;
  if (at.getTime() >= workEnd.getTime()) return null;
  return {
    at,
    resetAt,
    windowKey: `codex:sevenDay:${resetAt.toISOString()}`,
  };
}

export function weeklyActivationFromUsage(
  usage: Usage | undefined,
  workStart: Date,
  workEnd: Date,
): WeeklyActivationOption | null {
  if (!usage || usage.fiveHour || !usage.sevenDay) return null;
  return weeklyActivationForRange(usage.sevenDay.resetsAt ?? null, workStart, workEnd);
}

export function weeklyActivationFromSnapshot(
  snapshot: UsageSnapshot | undefined,
  workStart: Date,
  workEnd: Date,
): WeeklyActivationOption | null {
  if (!snapshot || snapshot.fiveHourUtil != null || snapshot.sevenDayUtil == null) return null;
  return weeklyActivationForRange(parseDate(snapshot.sevenDayResetAt), workStart, workEnd);
}

export type WeeklyCycleState = 'active' | 'exhausted' | 'ready' | 'unknown';

export function weeklyCycleState(
  weekly: { utilization: number; resetsAt?: Date | null },
  now = new Date(),
): WeeklyCycleState {
  const resetAt = weekly.resetsAt ?? null;
  if (resetAt && resetAt.getTime() <= now.getTime()) return 'ready';
  if (weekly.utilization >= 100) return resetAt ? 'exhausted' : 'unknown';
  return resetAt ? 'active' : 'unknown';
}

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
