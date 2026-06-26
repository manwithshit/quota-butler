import { describe, it, expect } from 'vitest';
import { buildPlan, parseAgents } from '../src/planner.js';
import type { Usage } from '../src/providers/index.js';
import type { PlanRequest } from '../src/schedule_flow.js';

function usage(util: number, resetsAt: Date | null = null): Usage {
  return { provider: 'x', fiveHour: { utilization: util, resetsAt, windowSeconds: 18000 } };
}

function req(partial: Partial<PlanRequest>): PlanRequest {
  return {
    targetDate: '2026-06-20',
    timeMode: 'point',
    workStart: '09:00',
    workEnd: '16:31',
    agentStrategy: 'auto',
    firstWarmup: '06:30',
    secondWarmup: '11:31',
    ...partial,
  };
}

function hm(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

describe('buildPlan', () => {
  it('single agent → warmups at start-2.5h and +5h, ignoring current 5h reset', () => {
    // 当前 5h 窗口 10:00 重置（落在工作区间内），但第二预热点恒为 firstWarmup+5h=11:30，与之无关。
    const plan = buildPlan(req({}), { cc: usage(55, new Date(2026, 5, 20, 10, 0)) });
    expect(plan.agents).toEqual(['cc']);
    expect(plan.events.map((e) => [e.agent, hm(e.at), e.purpose])).toEqual([
      ['cc', '06:30', '准备第一个窗口'],
      ['cc', '11:31', '恢复后准备第二个窗口'],
    ]);
  });

  it('adjusting start time recalculates warmups when no reset in range', () => {
    const plan = buildPlan(req({ workStart: '12:00', workEnd: '19:31', firstWarmup: '09:30', secondWarmup: '14:31', agentStrategy: 'cc' }), {
      cc: usage(20),
    });
    expect(plan.events.map((e) => hm(e.at))).toEqual(['09:30', '14:31']);
  });

  it('auto uses one agent for a short range even when two available', () => {
    const plan = buildPlan(req({ timeMode: 'range', workStart: '09:00', workEnd: '13:00' }), {
      cc: usage(70),
      codex: usage(20),
    });
    expect(plan.agents).toEqual(['codex']);
    expect(plan.reason).toContain('只使用 Codex');
  });

  it('both strategy is rejected because one plan only uses one agent', () => {
    expect(() => buildPlan(
      req({ timeMode: 'range', workStart: '09:00', workEnd: '18:00', agentStrategy: 'both' }),
      { cc: usage(20), codex: usage(30) },
    )).toThrow('一次只编排一个');
  });

  it('keeps warmups from the request instead of recalculating from a cross-midnight range', () => {
    const plan = buildPlan(req({ timeMode: 'range', workStart: '22:00', workEnd: '02:00' }), {
      cc: usage(20),
    });
    expect(plan.workStart.getDate()).toBe(20);
    expect(plan.workEnd.getDate()).toBe(21); // 次日
    expect(plan.agents).toEqual(['cc']);
    expect(plan.events.map((e) => hm(e.at))).toEqual(['06:30', '11:31']);
  });

  it('rejects a selected agent that is unavailable', () => {
    expect(() => buildPlan(req({ agentStrategy: 'cc' }), { codex: usage(20) })).toThrow('Claude Code');
  });

  it('parseAgents normalizes provider names', () => {
    expect(parseAgents('Claude Code,codex,cc')).toEqual(['cc', 'codex']);
  });
});
