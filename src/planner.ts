// 确定性 V3 明日计划计算器：一次只规划一个 AI 工具，用两次预热最大化单工具窗口。

import type { PlanRequest } from './schedule_flow.js';
import { normalizeHHmm } from './schedule_flow.js';
import type { Usage } from './providers/index.js';

export const SUPPORTED_AGENTS = ['cc', 'codex'] as const;
export const AGENT_LABELS: Record<string, string> = { cc: 'Claude Code', codex: 'Codex' };

export interface PlanEvent {
  agent: string;
  kind: string;
  at: Date;
  purpose: string;
}

export interface SchedulePlan {
  agents: string[];
  workStart: Date;
  workEnd: Date;
  events: PlanEvent[];
  reason: string;
  request: PlanRequest;
  planVersion: number;
}

const HOUR = 3600000;

export function buildPlan(request: PlanRequest, availableUsages: Record<string, Usage>): SchedulePlan {
  const start = combine(request, request.workStart);
  let end = combine(request, request.workEnd);
  // 结束 ≤ 开始 → 视为次日（支持跨午夜/夜班）。预热点、relay 全用绝对时间，跨天自然成立。
  if (end.getTime() <= start.getTime()) end = new Date(end.getTime() + 24 * HOUR);
  const selected = selectAgents(request.agentStrategy, availableUsages);

  const firstAgent = selected[0]!;
  const firstWarmup = combine(request, request.firstWarmup);
  const secondWarmup = combine(request, request.secondWarmup);
  const sortedWarmups = [firstWarmup, secondWarmup].sort((a, b) => a.getTime() - b.getTime());
  if (end.getTime() <= sortedWarmups[1]!.getTime()) end = new Date(sortedWarmups[1]!.getTime() + 5 * HOUR);

  const events: PlanEvent[] = [
    { agent: firstAgent, kind: 'warmup', at: sortedWarmups[0]!, purpose: '准备第一个窗口' },
    { agent: firstAgent, kind: 'warmup', at: sortedWarmups[1]!, purpose: '恢复后准备第二个窗口' },
  ];

  const reason = `当前计划只使用 ${AGENT_LABELS[firstAgent]}，用两次预热最大化单一工具的可用窗口。`;

  events.sort((a, b) => a.at.getTime() - b.at.getTime() || a.agent.localeCompare(b.agent));
  return { agents: selected, workStart: start, workEnd: end, events, reason, request, planVersion: 3 };
}

export function parseAgents(value: unknown): string[] {
  let raw: unknown[];
  if (typeof value === 'string') raw = value.replace(/，/g, ',').split(',');
  else if (Array.isArray(value)) raw = value;
  else raw = [];
  const agents: string[] = [];
  for (const item of raw) {
    const agent = normalizeAgent(String(item));
    if (!agents.includes(agent)) agents.push(agent);
  }
  return agents;
}

function selectAgents(strategy: string, usages: Record<string, Usage>): string[] {
  const available = (SUPPORTED_AGENTS as readonly string[]).filter((a) => a in usages);
  if (available.length === 0) throw new Error('当前没有可用于规划的 Agent');
  if (strategy === 'cc' || strategy === 'codex') {
    if (!(strategy in usages)) throw new Error(`${AGENT_LABELS[strategy]} 当前不可用`);
    return [strategy];
  }
  if (strategy === 'both') {
    throw new Error('当前流程一次只编排一个 AI 工具');
  }
  const ranked = rankAgents(available, usages);
  return [ranked[0]!];
}

function rankAgents(agents: string[], usages: Record<string, Usage>): string[] {
  // 周额度（木桶上限）剩余多的优先，其次 5 小时剩余多的优先。
  return [...agents].sort((a, b) => {
    const wa = weeklyRemaining(usages[a]!);
    const wb = weeklyRemaining(usages[b]!);
    if (wa !== wb) return wb - wa;
    // 规划候选必有 5h 窗口（usableForPlanning 已保证），仍做空值兜底。
    const fa = usages[a]!.fiveHour ? 100 - usages[a]!.fiveHour!.utilization : 0;
    const fb = usages[b]!.fiveHour ? 100 - usages[b]!.fiveHour!.utilization : 0;
    if (fa !== fb) return fb - fa;
    return (SUPPORTED_AGENTS as readonly string[]).indexOf(a) - (SUPPORTED_AGENTS as readonly string[]).indexOf(b);
  });
}

function weeklyRemaining(usage: Usage): number {
  return usage.sevenDay ? 100 - usage.sevenDay.utilization : 100;
}

function combine(request: PlanRequest, hhmm: string): Date {
  const n = normalizeHHmm(hhmm);
  const [h, m] = n.split(':').map(Number) as [number, number];
  const [y, mo, d] = request.targetDate.split('-').map(Number) as [number, number, number];
  return new Date(y, mo - 1, d, h, m, 0, 0);
}

function normalizeAgent(value: string): string {
  let key = value.trim().toLowerCase();
  if (['claude', 'claude-code', 'claude code'].includes(key)) key = 'cc';
  if (!(SUPPORTED_AGENTS as readonly string[]).includes(key)) {
    throw new Error(`unsupported scheduler agent: ${value}`);
  }
  return key;
}
