// V3 明日计划请求模型。移植自 Python schedule_flow.py（point/range 照搬）。

export const FLOW_VERSION = 5;
export const TIME_MODES = ['point', 'range'] as const;
export const AGENT_STRATEGIES = ['auto', 'cc', 'codex', 'both'] as const;

export type TimeMode = (typeof TIME_MODES)[number];
export type AgentStrategy = (typeof AGENT_STRATEGIES)[number];

export interface PlanRequest {
  targetDate: string; // 'YYYY-MM-DD'
  timeMode: TimeMode;
  workStart: string; // 'HH:mm'
  workEnd: string; // 'HH:mm'
  agentStrategy: AgentStrategy;
  firstWarmup: string;
  secondWarmup: string;
}

export function parsePlanRequest(
  value: Record<string, unknown> | null | undefined,
  availableAgentCount: number,
): PlanRequest {
  const raw = value ?? {};
  const targetDate = parseIsoDate(String(raw['target_date'] ?? ''));
  if (!targetDate) throw new Error('规划日期无效');
  const timeMode = String(raw['time_mode'] ?? 'point').trim().toLowerCase();
  if (!(TIME_MODES as readonly string[]).includes(timeMode)) throw new Error('时间模式无效');
  const strategy = String(raw['agent_strategy'] ?? 'auto').trim().toLowerCase();
  if (!(AGENT_STRATEGIES as readonly string[]).includes(strategy)) throw new Error('AI 工具选择无效');
  const workStart = normalizeHHmm(raw['work_start'] ?? '09:00');
  const firstWarmup = normalizeHHmm(raw['first_warmup'] ?? addMinutes(workStart, -150));
  const secondWarmup = normalizeHHmm(raw['second_warmup'] ?? addMinutes(firstWarmup, 301));
  validateWarmupTimes(firstWarmup, secondWarmup);
  let workEnd: string;
  if (timeMode === 'point') {
    void availableAgentCount;
    workEnd = addMinutes(secondWarmup, 300);
  } else {
    workEnd = normalizeHHmm(raw['work_end'] ?? '');
    validateWorkTime(workStart, workEnd);
  }
  return {
    targetDate,
    timeMode: timeMode as TimeMode,
    workStart,
    workEnd,
    agentStrategy: strategy as AgentStrategy,
    firstWarmup,
    secondWarmup,
  };
}

export function flowPayload(
  step: string,
  request: PlanRequest,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    cmd: 'quota',
    action: 'schedule_flow',
    flow_version: FLOW_VERSION,
    step,
    target_date: request.targetDate,
    request: requestToPayloadShape(request),
    ...extra,
  };
}

export function validateFlowContext(
  payload: Record<string, unknown>,
  today?: string,
): string {
  if (payload['flow_version'] !== FLOW_VERSION) throw new Error('该卡片已失效，请重新打开菜单');
  const target = parseIsoDate(String(payload['target_date'] ?? ''));
  if (!target) throw new Error('规划日期无效');
  const todayStr = today ?? todayLocalIso();
  if (target <= todayStr) throw new Error('该卡片已过期，请重新规划');
  return target;
}

export function normalizeHHmm(value: unknown): string {
  const text = String(value).trim().split(/\s+/)[0] ?? '';
  const parts = text.split(':');
  if (parts.length !== 2) throw new Error('时间格式必须为 HH:mm');
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) throw new Error('时间格式必须为 HH:mm');
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) throw new Error('时间格式必须为 HH:mm');
  return `${pad(hour)}:${pad(minute)}`;
}

export function validateWorkTime(workStart: string, workEnd: string): number {
  const start = minutesOf(normalizeHHmm(workStart));
  const end = minutesOf(normalizeHHmm(workEnd));
  let duration = end - start;
  if (duration <= 0) duration += 24 * 60; // 结束 ≤ 开始 → 视为次日（支持跨天/夜班）
  if (duration > 16 * 60) throw new Error('重度使用区间不能超过 16 小时');
  return duration;
}

export function requestToPayloadShape(request: PlanRequest): Record<string, unknown> {
  return {
    target_date: request.targetDate,
    time_mode: request.timeMode,
    work_start: request.workStart,
    work_end: request.workEnd,
    agent_strategy: request.agentStrategy,
    first_warmup: request.firstWarmup,
    second_warmup: request.secondWarmup,
  };
}

function addMinutes(value: string, minutes: number): string {
  const [h, m] = value.split(':').map(Number) as [number, number];
  const raw = h * 60 + m + minutes;
  if (raw < 0 || raw >= 24 * 60) throw new Error('默认计划不能跨天');
  const total = raw % (24 * 60);
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
}

export function validateWarmupTimes(firstWarmup: string, secondWarmup: string): number {
  const first = minutesOf(normalizeHHmm(firstWarmup));
  const second = minutesOf(normalizeHHmm(secondWarmup));
  const gap = Math.abs(second - first);
  if (gap < 5 * 60) throw new Error('两个预热时间至少需要相隔 5 小时');
  return gap;
}

function minutesOf(value: string): number {
  const [h, m] = value.split(':').map(Number) as [number, number];
  return h * 60 + m;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function parseIsoDate(v: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v.trim());
  if (!m) return null;
  const d = new Date(`${v}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : `${m[1]}-${m[2]}-${m[3]}`;
}

function todayLocalIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
