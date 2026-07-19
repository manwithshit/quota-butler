// 确定性 V3 明日计划计算器：auto 默认只规划一个工具，both 显式规划双 Agent。
// 传统 5h 档用两次预热接力；仅周额度档只做一次连通预热。

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
  slot?: 'primary-first' | 'primary-second' | 'relay-pin' | 'relay' | 'backup-connect';
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
    events = [{
      agent: firstAgent,
      kind: 'warmup',
      at: firstWarmup,
      purpose: '开工前连通预热',
      slot: 'primary-first',
    }];
    reason = `${AGENT_LABELS[firstAgent]} 当前使用周额度，开工前执行一次连通预热，不再安排 5 小时窗口接力。`;
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

  if (selected.length === 2) {
    const relayAgent = selected[1]!;
    const relayUsage = availableUsages[relayAgent]!;
    const relayAt = new Date(start.getTime() + 5 * HOUR - 10 * 60_000);
    const hasRelayPhase = relayAt.getTime() < end.getTime();
    if (hasRelayPhase && relayUsage.fiveHour) {
      events.push(
        {
          agent: relayAgent,
          kind: 'warmup',
          at: new Date(relayAt.getTime() - 5 * HOUR),
          purpose: '提前垫好接力窗口',
          slot: 'relay-pin',
        },
        {
          agent: relayAgent,
          kind: 'warmup',
          at: relayAt,
          purpose: '接力刷新，无缝顶上',
          slot: 'relay',
        },
      );
    } else {
      events.push({
        agent: relayAgent,
        kind: 'warmup',
        at: hasRelayPhase ? relayAt : firstWarmup,
        purpose: relayUsage.sevenDay && !relayUsage.fiveHour ? '接力前连通预热' : '备用工具连通预热',
        slot: 'backup-connect',
      });
    }
    if (!hasRelayPhase) {
      reason = `${AGENT_LABELS[firstAgent]} 与 ${AGENT_LABELS[relayAgent]} 都在开工前完成连通预热，可按需切换。`;
    } else {
      reason = relayUsage.sevenDay && !relayUsage.fiveHour
        ? `前半段由 ${AGENT_LABELS[firstAgent]} 使用短窗口，后半段由周额度模式的 ${AGENT_LABELS[relayAgent]} 接力。`
        : `前半段优先保持 ${AGENT_LABELS[firstAgent]} 连续工作，后半段由 ${AGENT_LABELS[relayAgent]} 接力。`;
    }
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
    if (available.length < 2) throw new Error('Claude Code + Codex 当前不能同时使用');
    return rankAgentsForDual(available, usages);
  }
  const ranked = rankAgents(available, usages);
  return [ranked[0]!];
}

function rankAgentsForDual(agents: string[], usages: Record<string, Usage>): string[] {
  const ranked = rankAgents(agents, usages);
  return ranked.sort((a, b) => {
    const aHasFive = usages[a]!.fiveHour ? 1 : 0;
    const bHasFive = usages[b]!.fiveHour ? 1 : 0;
    return bHasFive - aHasFive;
  });
}

function rankAgents(agents: string[], usages: Record<string, Usage>): string[] {
  // 周额度（木桶上限）剩余多的优先，其次 5 小时剩余多的优先。
  return [...agents].sort((a, b) => {
    const wa = weeklyRemaining(usages[a]!);
    const wb = weeklyRemaining(usages[b]!);
    if (wa !== wb) return wb - wa;
    // 仅周额度档没有 5h 窗口，平手时排在有短窗口可接力的工具之后。
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
