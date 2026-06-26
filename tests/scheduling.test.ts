import { describe, it, expect } from 'vitest';
import { usableForPlanning } from '../src/agent_status.js';
import { activePlanIndex, planIsExpired, StateStore, type State } from '../src/state.js';
import { buildCurrentPlansCard, buildManualWarmupCard } from '../src/notify.js';
import { AgentState, type AgentStatus } from '../src/agent_status.js';
import { buildPlan } from '../src/planner.js';
import type { Usage } from '../src/providers/index.js';
import type { PlanRequest } from '../src/schedule_flow.js';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { handleAction } from '../src/handler.js';

function u(util5: number, util7?: number): Usage {
  return {
    provider: 'x',
    fiveHour: { utilization: util5, resetsAt: null, windowSeconds: 18000 },
    sevenDay: util7 == null ? null : { utilization: util7, resetsAt: null, windowSeconds: 604800 },
  };
}

const req: PlanRequest = {
  targetDate: '2026-06-20', timeMode: 'range', workStart: '09:00', workEnd: '13:00', agentStrategy: 'auto',
  firstWarmup: '06:30', secondWarmup: '11:31',
};

describe('usableForPlanning', () => {
  it('excludes only when weekly quota is bottomed out (ignores current 5h)', () => {
    expect(usableForPlanning(u(1, 50))).toBe(true); // 周还剩 50%
    expect(usableForPlanning(u(1, 99))).toBe(false); // 周仅剩 1% → 木桶见底
    expect(usableForPlanning(u(99))).toBe(true); // 5h 用满也无所谓（预热时会满，无周数据）
    expect(usableForPlanning(u(99, 95))).toBe(false); // 周见底→排除，无视 5h
    expect(usableForPlanning(u(1))).toBe(true); // 无周数据→视为可用
  });

  it('excludes free-tier Codex (monthly-only window, no 5h)', () => {
    // 免费档 Codex：无 5h 窗口、只有月度窗口 → 不能预热/接力 → 不进规划候选。
    const freeCodex: Usage = {
      provider: 'codex',
      fiveHour: null,
      monthly: { utilization: 20, resetsAt: null, windowSeconds: 2592000 },
    };
    expect(usableForPlanning(freeCodex)).toBe(false);
  });
});

describe('planIsExpired', () => {
  it('expires a plan once its work_end has passed', () => {
    const now = new Date('2026-06-23T16:00:00');
    expect(planIsExpired({ work_end: '2026-06-23T15:00:00' }, now)).toBe(true); // 已过
    expect(planIsExpired({ work_end: '2026-06-23T18:00:00' }, now)).toBe(false); // 未到
    expect(planIsExpired(null, now)).toBe(false);
  });
});

describe('planner ranks by weekly quota first', () => {
  it('prefers the agent with more weekly headroom even if its 5h is more used', () => {
    // cc：5h 已用 10（剩 90）、周剩 80；codex：5h 已用 5（剩 95）、周仅剩 30。
    const plan = buildPlan(req, { cc: u(10, 20), codex: u(5, 70) });
    expect(plan.agents).toEqual(['cc']); // 周额度多的 cc 胜出（旧逻辑会选 codex）
  });
});

describe('current plans and immediate warmup UX', () => {
  it('keeps today and tomorrow plans separate', () => {
    const state = {
      activePlan: null,
      plansByDate: {
        '2026-06-23': { status: 'active', plan_id: 'today', work_start: '2026-06-23T09:00:00', work_end: '2026-06-23T16:31:00', agents: ['codex'], events: [] },
        '2026-06-24': { status: 'active', plan_id: 'tomorrow', work_start: '2026-06-24T09:00:00', work_end: '2026-06-24T16:31:00', agents: ['codex'], events: [] },
      },
    } as unknown as State;
    const plans = activePlanIndex(state, new Date('2026-06-23T13:00:00'));
    expect(Object.keys(plans).sort()).toEqual(['2026-06-23', '2026-06-24']);
    expect(state.activePlan).toMatchObject({ plan_id: 'today' });
  });

  it('current-plan card renders today and tomorrow together', () => {
    const card = buildCurrentPlansCard({
      '2026-06-23': { status: 'active', plan_id: 'today', work_start: '2026-06-23T09:00:00', work_end: '2026-06-23T16:31:00', agents: ['codex'], events: [] },
      '2026-06-24': { status: 'active', plan_id: 'tomorrow', work_start: '2026-06-24T10:00:00', work_end: '2026-06-24T17:31:00', agents: ['cc'], events: [] },
    }, new Date('2026-06-23T13:00:00'));
    const text = JSON.stringify(card);
    expect(text).toContain('今日计划');
    expect(text).toContain('明日计划');
    expect(text).toContain('09:00–16:31');
    expect(text).toContain('10:00–17:31');
  });

  it('cancels a tomorrow plan whenever the current-plan card offers that cancel button', async () => {
    const state = new StateStore(join(tmpdir(), `qb-cancel-${Date.now()}-${Math.random()}.json`));
    const tomorrow = new Date(Date.now() + 24 * 3600000);
    tomorrow.setHours(9, 0, 0, 0);
    const targetDate = tomorrow.toISOString().slice(0, 10);
    const warmup = new Date(tomorrow.getTime() - 90 * 60000).toISOString();
    state.get().plansByDate[targetDate] = {
      status: 'active',
      plan_id: 'tomorrow',
      work_start: tomorrow.toISOString(),
      work_end: new Date(tomorrow.getTime() + 6 * 3600000).toISOString(),
      agents: ['codex'],
      events: [{ agent: 'codex', at: warmup, purpose: '准备第一个窗口' }],
    };

    const card = buildCurrentPlansCard({ [targetDate]: state.get().plansByDate[targetDate]! }, new Date());
    const text = JSON.stringify(card);
    expect(text).toContain('取消明日计划');
    expect(text).toContain('未执行');

    const receipts: string[] = [];
    await handleAction(
      { action: 'cancel_schedule', target_date: targetDate },
      {
        state,
        send: async () => {},
        receipt: async (message) => {
          receipts.push(message);
        },
      },
    );

    expect(receipts).toEqual(['✅ 已取消计划，未执行任务已删除']);
    expect(state.get().plansByDate[targetDate]).toBeUndefined();
  });

  it('immediate warmup no-op card stays terse when nothing can warm up now', () => {
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage: u(0, 100) },
      codex: {
        provider: 'codex',
        state: AgentState.CONNECTED,
        usage: { provider: 'codex', fiveHour: { utilization: 20, resetsAt: new Date(Date.now() + 3600000), windowSeconds: 18000 } },
      },
    };
    const whole = JSON.stringify(buildManualWarmupCard(statuses));
    expect(whole).toContain('Claude Code：7 天额度已耗尽，暂不可预热');
    expect(whole).toContain('Codex：当前 5 小时窗口已在进行中，无需立即预热');
    expect(whole).not.toContain('选择要立即预热');
    expect(whole).not.toContain('暂时没有需要立即预热');
  });
});
