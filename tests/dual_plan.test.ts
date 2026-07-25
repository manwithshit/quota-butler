import { describe, expect, it, vi } from 'vitest';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

vi.mock('../src/agent_status.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/agent_status.js')>();
  return { ...actual, detectAgents: vi.fn() };
});

import { AgentState, detectAgents, type AgentStatus } from '../src/agent_status.js';
import { handleAction } from '../src/handler.js';
import { planRecord } from '../src/plan_record.js';
import { buildPlan } from '../src/planner.js';
import { StateStore } from '../src/state.js';
import type { Usage } from '../src/providers/index.js';
import type { PlanRequest } from '../src/schedule_flow.js';
import type { WarmupScheduler } from '../src/scheduler.js';

const mockDetect = detectAgents as unknown as ReturnType<typeof vi.fn>;

function fiveHour(provider: string, utilization: number): Usage {
  return {
    provider,
    fiveHour: { utilization, resetsAt: null, windowSeconds: 18000 },
    sevenDay: { utilization: 20, resetsAt: new Date('2099-06-24T00:00:00'), windowSeconds: 604800 },
  };
}

function weeklyOnly(utilization: number): Usage {
  return {
    provider: 'codex',
    fiveHour: null,
    sevenDay: { utilization, resetsAt: new Date('2099-06-24T00:00:00'), windowSeconds: 604800 },
  };
}

describe('dual-agent plan adoption', () => {
  it('appends one Codex weekly activation after the Claude Code plan and remains idempotent', async () => {
    const request: PlanRequest = {
      targetDate: '2099-06-20',
      timeMode: 'range',
      workStart: '09:00',
      workEnd: '18:00',
      agentStrategy: 'cc',
      firstWarmup: '06:30',
      secondWarmup: '11:31',
    };
    const record = planRecord(buildPlan(request, { cc: fiveHour('cc', 20) })) as unknown as Record<string, unknown>;
    record['status'] = 'active';
    const state = new StateStore(join(tmpdir(), `qb-dual-${Date.now()}-${Math.random()}.json`));
    state.recordUsageSnapshot('codex', weeklyOnly(100));
    const resetAt = new Date('2099-06-20T13:00:00');
    state.get().usageSnapshots.codex!.sevenDayResetAt = resetAt.toISOString();
    state.get().plansByDate['2099-06-20'] = record;
    state.get().activePlan = record;
    const armedPlans: Array<Array<Record<string, unknown>>> = [];
    const scheduler = {
      armPlans: (records: Array<Record<string, unknown>>) => {
        armedPlans.push(records);
        return { armed: 3, skipped: 0 };
      },
    } as unknown as WarmupScheduler;
    const receipts: string[] = [];

    const action = {
      action: 'append_codex_weekly_activation',
      target_date: '2099-06-20',
      expected_reset_at: resetAt.toISOString(),
    };
    await handleAction(
      action,
      {
        state,
        scheduler,
        send: async () => {},
        receipt: async (message) => {
          receipts.push(message);
        },
      },
    );
    await handleAction(action, {
      state,
      scheduler,
      send: async () => {},
      receipt: async (message) => {
        receipts.push(message);
      },
    });

    expect(receipts[0]).toContain('已追加 Codex 新周期预热');
    expect(receipts[1]).toBe('Codex 新周期预热已经追加，无需重复设置');
    expect(armedPlans).toHaveLength(1);
    const adopted = state.get().plansByDate['2099-06-20']!;
    expect(adopted['agents']).toEqual(['cc', 'codex']);
    const events = adopted['events'] as Array<Record<string, unknown>>;
    expect(events.filter((event) => event['kind'] === 'weekly-activation')).toMatchObject([{
      agent: 'codex',
      at: '2099-06-20T13:01:30',
      slot: 'weekly-activation',
      window_reset_at: resetAt.toISOString(),
    }]);
  });

  it('edits legacy dual records without slots by matching the primary agent', async () => {
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage: fiveHour('cc', 20) },
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: fiveHour('codex', 30) },
    };
    mockDetect.mockResolvedValue(statuses);
    const candidate = {
      plan_id: 'legacy-dual',
      status: 'proposed',
      plan_version: 3,
      agents: ['cc', 'codex'],
      work_start: '2099-06-20T09:00:00',
      work_end: '2099-06-20T18:00:00',
      reason: 'legacy',
      events: [
        { agent: 'cc', kind: 'warmup', at: '2099-06-20T06:30:00', purpose: 'primary first' },
        { agent: 'codex', kind: 'warmup', at: '2099-06-20T08:50:00', purpose: 'relay pin' },
        { agent: 'cc', kind: 'warmup', at: '2099-06-20T11:31:00', purpose: 'primary second' },
        { agent: 'codex', kind: 'warmup', at: '2099-06-20T13:50:00', purpose: 'relay' },
      ],
      request: {
        target_date: '2099-06-20',
        time_mode: 'range',
        work_start: '09:00',
        work_end: '18:00',
        agent_strategy: 'both',
      },
    };
    const state = new StateStore(join(tmpdir(), `qb-legacy-dual-${Date.now()}-${Math.random()}.json`));
    const scheduler = { armPlans: () => ({ armed: 4, skipped: 0 }) } as unknown as WarmupScheduler;

    await handleAction(
      {
        action: 'adopt_schedule',
        plan: candidate,
        form_value: { first_warmup: '07:00', second_warmup: '12:01' },
      },
      { state, scheduler, send: async () => {}, receipt: async () => {} },
    );

    const events = state.get().plansByDate['2099-06-20']?.['events'] as Array<Record<string, unknown>>;
    expect(events.filter((event) => event['agent'] === 'cc').map((event) => String(event['at']).slice(11, 16))).toEqual(['07:00', '12:01']);
    expect(events.filter((event) => event['agent'] === 'codex').map((event) => String(event['at']).slice(11, 16))).toEqual(['08:50', '13:50']);
  });

  it('cancels all remaining timers from a dual plan together', async () => {
    const state = new StateStore(join(tmpdir(), `qb-cancel-dual-${Date.now()}-${Math.random()}.json`));
    const future = new Date(Date.now() + 24 * 3600000);
    const target = future.toISOString().slice(0, 10);
    const plan = {
      plan_id: 'dual-cancel',
      status: 'active',
      work_start: future.toISOString(),
      work_end: new Date(future.getTime() + 8 * 3600000).toISOString(),
      agents: ['cc', 'codex'],
      events: [
        { agent: 'cc', kind: 'warmup', at: new Date(future.getTime() + 60000).toISOString(), purpose: '' },
        { agent: 'codex', kind: 'warmup', at: new Date(future.getTime() + 120000).toISOString(), purpose: '' },
      ],
    };
    state.get().plansByDate[target] = plan;
    state.get().activePlan = plan;
    const armed: Array<Array<Record<string, unknown>>> = [];
    const scheduler = {
      armPlans: (records: Array<Record<string, unknown>>) => {
        armed.push(records);
        return { armed: 0, skipped: 0 };
      },
    } as unknown as WarmupScheduler;

    await handleAction(
      { action: 'cancel_schedule', target_date: target },
      { state, scheduler, send: async () => {}, receipt: async () => {} },
    );

    expect(state.get().plansByDate[target]).toBeUndefined();
    expect(armed).toEqual([[]]);
  });
});
