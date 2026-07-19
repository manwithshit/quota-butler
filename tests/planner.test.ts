import { describe, it, expect } from 'vitest';
import { buildPlan, parseAgents } from '../src/planner.js';
import type { Usage } from '../src/providers/index.js';
import type { PlanRequest } from '../src/schedule_flow.js';

function usage(util: number, resetsAt: Date | null = null): Usage {
  return { provider: 'x', fiveHour: { utilization: util, resetsAt, windowSeconds: 18000 } };
}

function weeklyUsage(util: number): Usage {
  return {
    provider: 'codex',
    fiveHour: null,
    sevenDay: { utilization: util, resetsAt: new Date('2026-06-22T00:00:00Z'), windowSeconds: 604800 },
  };
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

  it('weekly-only Codex uses one connectivity warmup instead of two fake 5h windows', () => {
    const plan = buildPlan(req({ agentStrategy: 'codex' }), { codex: weeklyUsage(38) });
    expect(plan.agents).toEqual(['codex']);
    expect(plan.events.map((e) => [e.agent, hm(e.at), e.purpose])).toEqual([
      ['codex', '06:30', '开工前连通预热'],
    ]);
    expect(plan.reason).toContain('周额度');
    expect(plan.reason).toContain('不再安排 5 小时窗口接力');
  });

  it('both strategy restores the historical two-agent relay plan', () => {
    const plan = buildPlan(
      req({ timeMode: 'range', workStart: '09:00', workEnd: '18:00', agentStrategy: 'both' }),
      { cc: usage(20), codex: usage(30) },
    );
    expect(plan.agents).toEqual(['cc', 'codex']);
    expect(plan.events.map((event) => [event.agent, hm(event.at), event.slot])).toEqual([
      ['cc', '06:30', 'primary-first'],
      ['codex', '08:50', 'relay-pin'],
      ['cc', '11:31', 'primary-second'],
      ['codex', '13:50', 'relay'],
    ]);
    expect(plan.reason).toContain('接力');
  });

  it('mixed 5h + weekly-only dual plan does not invent Codex 5h windows', () => {
    const plan = buildPlan(
      req({ timeMode: 'range', workStart: '09:00', workEnd: '18:00', agentStrategy: 'both' }),
      { cc: usage(20), codex: weeklyUsage(38) },
    );
    expect(plan.agents).toEqual(['cc', 'codex']);
    expect(plan.events.map((event) => [event.agent, hm(event.at), event.slot])).toEqual([
      ['cc', '06:30', 'primary-first'],
      ['cc', '11:31', 'primary-second'],
      ['codex', '13:50', 'backup-connect'],
    ]);
    expect(plan.events.filter((event) => event.agent === 'codex')).toHaveLength(1);
    expect(plan.reason).toContain('周额度模式');
  });

  it('short explicit dual plan warms both tools without inventing an out-of-range relay', () => {
    const plan = buildPlan(
      req({ timeMode: 'range', workStart: '09:00', workEnd: '13:00', agentStrategy: 'both' }),
      { cc: usage(20), codex: weeklyUsage(38) },
    );
    expect(plan.events.filter((event) => event.agent === 'codex').map((event) => hm(event.at))).toEqual(['06:30']);
    expect(plan.reason).toContain('都在开工前完成连通预热');
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
