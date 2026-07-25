// 确定性 V3 明日计划计算器：auto 默认 Claude Code；Codex 周额度只在新周期到点时激活。

import type { PlanRequest } from './schedule_flow.js';
import { normalizeHHmm } from './schedule_flow.js';
import type { Usage } from './providers/index.js';
import { weeklyActivationFromUsage } from './weekly_activation.js';

export const SUPPORTED_AGENTS = ['cc', 'codex'] as const;
export const AGENT_LABELS: Record<string, string> = { cc: 'Claude Code', codex: 'Codex' };

export interface PlanEvent {
  agent: string;
  kind: string;
  at: Date;
  purpose: string;
  slot?: 'primary-first' | 'primary-second' | 'weekly-activation';
  windowResetAt?: Date;
  windowKey?: string;
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
  const selectedUsage = availableUsages[firstAgent]!;
  const firstWarmup = combine(request, request.firstWarmup);
  let events: PlanEvent[];
  let reason: string;
  if (!selectedUsage.fiveHour && selectedUsage.sevenDay) {
    const activation = weeklyActivationFromUsage(selectedUsage, start, end);
    events = activation ? [{
      agent: firstAgent,
      kind: 'weekly-activation',
      at: activation.at,
      purpose: '开启 Codex 新一周额度',
      slot: 'weekly-activation',
      windowResetAt: activation.resetAt,
      windowKey: activation.windowKey,
    }] : [];
    reason = activation
      ? `${AGENT_LABELS[firstAgent]} 会在新周周期就绪后发送第一条消息，开启下一周期。`
      : `${AGENT_LABELS[firstAgent]} 周周期进行中，明天可直接使用，无需每日预热。`;
  } else {
    const secondWarmup = combine(request, request.secondWarmup);
    const sortedWarmups = [firstWarmup, secondWarmup].sort((a, b) => a.getTime() - b.getTime());
    if (end.getTime() <= sortedWarmups[1]!.getTime()) end = new Date(sortedWarmups[1]!.getTime() + 5 * HOUR);
    events = [
      {
        agent: firstAgent,
        kind: 'warmup',
        at: sortedWarmups[0]!,
        purpose: '准备第一个窗口',
        slot: 'primary-first',
      },
      {
        agent: firstAgent,
        kind: 'warmup',
        at: sortedWarmups[1]!,
        purpose: '恢复后准备第二个窗口',
        slot: 'primary-second',
      },
    ];
    reason = `当前计划只使用 ${AGENT_LABELS[firstAgent]}，用两次预热最大化单一工具的可用窗口。`;
  }

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
    throw new Error('请先采用 Claude Code 明日计划，再按需追加 Codex 新周期预热');
  }
  // 每日预热只对有 5h 窗口的 Claude Code 有意义；Codex 仅作不可用时的兜底。
  if ('cc' in usages) return ['cc'];
  return [available[0]!];
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
