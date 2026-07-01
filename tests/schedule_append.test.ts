import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../src/agent_status.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/agent_status.js')>();
  return { ...actual, detectAgents: vi.fn() };
});

import { detectAgents, AgentState, type AgentStatus } from '../src/agent_status.js';
import { handleAction, type HandlerCtx } from '../src/handler.js';
import { StateStore } from '../src/state.js';
import type { Usage } from '../src/providers/index.js';

const mockDetect = detectAgents as unknown as ReturnType<typeof vi.fn>;

function usage(provider: string): Usage {
  return {
    provider,
    fiveHour: { utilization: 0, resetsAt: null, windowSeconds: 18000 },
    sevenDay: { utilization: 20, resetsAt: null, windowSeconds: 604800 },
  };
}

function status(provider: string): AgentStatus {
  return { provider, state: AgentState.CONNECTED, usage: usage(provider) };
}

function stateWithTomorrowPlan(): { state: StateStore; target: string } {
  const state = new StateStore(join(tmpdir(), `qb-append-${Date.now()}-${Math.random()}.json`));
  const target = '2026-06-24';
  state.get().plansByDate[target] = {
    status: 'active',
    plan_id: 'base-plan',
    plan_version: 3,
    agents: ['cc'],
    work_start: `${target}T09:00:00`,
    work_end: `${target}T16:31:00`,
    reason: 'test',
    events: [
      { agent: 'cc', kind: 'warmup', at: `${target}T06:30:00`, purpose: '准备第一个窗口' },
      { agent: 'cc', kind: 'warmup', at: `${target}T11:31:00`, purpose: '恢复后准备第二个窗口' },
    ],
    request: { target_date: target, time_mode: 'point', work_start: '09:00', work_end: '16:31', agent_strategy: 'auto' },
  };
  return { state, target };
}

describe('supplement another model into tomorrow plan', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-23T13:00:00'));
    mockDetect.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('opens a supplement card with offset warmup defaults', async () => {
    const { state, target } = stateWithTomorrowPlan();
    const cards: unknown[] = [];
    const receipts: string[] = [];
    mockDetect.mockResolvedValue({ codex: status('codex') });

    await handleAction(
      { action: 'append_schedule_agent', target_date: target, plan_id: 'base-plan', agent: 'codex' },
      {
        state,
        send: async (card) => { cards.push(card); },
        receipt: async (message) => { receipts.push(message); },
      },
    );

    expect(receipts).toEqual([]);
    const whole = JSON.stringify(cards[0]);
    expect(whole).toContain('为 Codex 补充明天预热');
    expect(whole).toContain('06:33');
    expect(whole).toContain('11:34');
  });

  it('merges the supplement into the same date plan and re-arms future warmups', async () => {
    const { state, target } = stateWithTomorrowPlan();
    const receipts: string[] = [];
    const scheduler = { armPlans: vi.fn(() => ({ armed: 4, skipped: 0 })) };
    mockDetect.mockResolvedValue({ codex: status('codex') });

    await handleAction(
      {
        action: 'adopt_schedule_append',
        target_date: target,
        previous_plan_id: 'base-plan',
        agent: 'codex',
        form_value: { first_warmup: '06:33', second_warmup: '11:34' },
      },
      {
        state,
        scheduler: scheduler as unknown as NonNullable<HandlerCtx['scheduler']>,
        send: async () => {},
        receipt: async (message) => { receipts.push(message); },
      },
    );

    const record = state.get().plansByDate[target]!;
    expect(receipts).toEqual(['✅ 已补充 Codex 预热，已重新布置 4 个预热任务']);
    expect(Object.keys(state.get().plansByDate)).toEqual([target]);
    expect(record['plan_id']).not.toBe('base-plan');
    expect(record['agents']).toEqual(['cc', 'codex']);
    expect((record['events'] as unknown[])).toHaveLength(4);
    expect(JSON.stringify(record)).toContain(`${target}T06:33:00`);
    expect(scheduler.armPlans).toHaveBeenCalledTimes(1);
  });

  it('explains why the missing model cannot be planned', async () => {
    const { state, target } = stateWithTomorrowPlan();
    const receipts: string[] = [];
    const cards: unknown[] = [];
    mockDetect.mockResolvedValue({
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: { provider: 'codex', fiveHour: null, monthly: { utilization: 20, resetsAt: null, windowSeconds: 2592000 } } },
    });

    await handleAction(
      { action: 'append_schedule_agent', target_date: target, plan_id: 'base-plan', agent: 'codex' },
      {
        state,
        send: async (card) => { cards.push(card); },
        receipt: async (message) => { receipts.push(message); },
      },
    );

    expect(cards).toEqual([]);
    expect(receipts[0]).toContain('Codex 明天不可规划，原因是：没有 5 小时窗口');
  });
});
