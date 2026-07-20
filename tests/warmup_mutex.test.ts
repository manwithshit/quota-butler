import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { handleAction, type HandlerCtx } from '../src/handler.js';
import { WarmupScheduler } from '../src/scheduler.js';
import { StateStore } from '../src/state.js';
import { AgentState, type AgentStatus } from '../src/agent_status.js';
import { buildAgentControlCard } from '../src/notify.js';
import { resetWarmupLocks, tryBeginWarmup, endWarmup } from '../src/warmup_lock.js';
import type { PlanRequest } from '../src/schedule_flow.js';
import type { LarkChannel } from '@larksuite/channel';

// 慢速假 provider：让两次点击有并发窗口。
vi.mock('../src/providers/index.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/providers/index.js')>();
  return {
    ...actual,
    getProvider: () => ({
      name: 'cc',
      readUsage: async () => ({ provider: 'cc', fiveHour: null }),
      warmup: async () => {
        await new Promise((r) => setTimeout(r, 30));
        return '你好';
      },
    }),
  };
});

function tmpState(): StateStore {
  return new StateStore(join(tmpdir(), `qb-mutex-${Date.now()}-${Math.random()}.json`));
}

function ctxWithReceipts(state: StateStore): { ctx: HandlerCtx; receipts: string[] } {
  const receipts: string[] = [];
  const ctx: HandlerCtx = {
    state,
    send: async () => {},
    receipt: async (text: string) => void receipts.push(text),
  };
  return { ctx, receipts };
}

beforeEach(() => resetWarmupLocks());
afterEach(() => {
  resetWarmupLocks();
  vi.useRealTimers();
});

describe('预热互斥（P0-7）', () => {
  it('手动预热卡双击：只执行一次，第二次得到"正在预热中"回执', async () => {
    const { ctx, receipts } = ctxWithReceipts(tmpState());
    await Promise.all([
      handleAction({ action: 'warmup_now', provider: 'cc' }, ctx),
      handleAction({ action: 'warmup_now', provider: 'cc' }, ctx),
    ]);

    expect(receipts).toHaveLength(2);
    expect(receipts.filter((r) => r.includes('已预热'))).toHaveLength(1);
    expect(receipts.filter((r) => r.includes('正在预热中'))).toHaveLength(1);
  });

  it('上一次预热完成后可再次预热（锁会释放，失败也释放）', async () => {
    const { ctx, receipts } = ctxWithReceipts(tmpState());
    await handleAction({ action: 'warmup_now', provider: 'cc' }, ctx);
    await handleAction({ action: 'warmup_now', provider: 'cc' }, ctx);
    expect(receipts.filter((r) => r.includes('已预热'))).toHaveLength(2);
  });

  it('带 window_key 的恢复卡预热会标记窗口并清理待发队列', async () => {
    const state = tmpState();
    const { ctx } = ctxWithReceipts(state);
    const windowKey = 'cc:fiveHour:2026-07-04T04:05:00.000Z';
    state.get().pendingNotifications = [{ provider: 'cc', window: 'fiveHour', windowKey }];

    await handleAction({ action: 'warmup_now', provider: 'cc', window_key: windowKey }, ctx);

    expect(state.get().lastWarmedWindows.cc).toBe(windowKey);
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('无 window_key 的手动预热优先标记待发恢复窗口', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-04T05:00:00.000Z'));
    const state = tmpState();
    const { ctx } = ctxWithReceipts(state);
    const windowKey = 'cc:fiveHour:2026-07-04T04:05:00.000Z';
    state.get().pendingNotifications = [{ provider: 'cc', window: 'fiveHour', windowKey }];

    const action = handleAction({ action: 'warmup_now', provider: 'cc' }, ctx);
    await vi.advanceTimersByTimeAsync(31);
    await action;

    expect(state.get().lastWarmedWindows.cc).toBe(windowKey);
    expect(state.get().pendingNotifications).toHaveLength(0);
  });

  it('不同 provider 互不阻塞', () => {
    expect(tryBeginWarmup('cc')).toBe(true);
    expect(tryBeginWarmup('codex')).toBe(true);
    expect(tryBeginWarmup('cc')).toBe(false);
    endWarmup('cc');
    expect(tryBeginWarmup('cc')).toBe(true);
  });

  it('定时预热撞上进行中的手动预热：按 skip 记录、发跳过回执、不重复执行', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 4, 10, 0));
    const state = tmpState();
    const sends: Array<{ text?: string }> = [];
    const channel = { send: vi.fn(async (_id: string, msg: { text?: string }) => void sends.push(msg)) } as unknown as LarkChannel;
    const sch = new WarmupScheduler(channel, 'ou_x', state);
    const at = new Date(2026, 6, 4, 10, 1).toISOString();
    const plan = { plan_id: 'p1', status: 'active', work_end: new Date(2026, 6, 4, 20, 0).toISOString(), events: [{ agent: 'cc', kind: 'warmup', at, purpose: '' }] };
    state.get().plansByDate = { '2026-07-04': plan };
    state.get().activePlan = plan;
    sch.arm(plan);

    tryBeginWarmup('cc'); // 模拟手动预热进行中
    await vi.advanceTimersByTimeAsync(61_000);

    expect(state.get().executedWarmups).toContain(`p1:cc:${at}`);
    expect(state.get().eventLog).toMatchObject([{ type: 'warmup', agent: 'cc', result: 'skip' }]);
    expect(sends.some((m) => (m.text ?? '').includes('另一次预热正在进行'))).toBe(true);
    sch.cancelAll();
  });
});

describe('换工具卡（P0-4）', () => {
  it('两个工具都可用时只提供单工具入口', () => {
    const request: PlanRequest = {
      targetDate: '2026-07-05', timeMode: 'point', workStart: '09:00', workEnd: '16:31',
      agentStrategy: 'auto', firstWarmup: '06:30', secondWarmup: '11:31',
    };
    const usage = { provider: 'cc', fiveHour: { utilization: 10, resetsAt: null, windowSeconds: 18000 } };
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage },
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: { ...usage, provider: 'codex' } },
    };
    const text = JSON.stringify(buildAgentControlCard(request, statuses));
    expect(text).toContain('Claude Code');
    expect(text).toContain('Codex');
    expect(text).not.toContain('两个都用');
    expect(text).not.toContain('"both"');
  });

  it('只有一个工具可用时不提供双 Agent 入口', () => {
    const request: PlanRequest = {
      targetDate: '2026-07-05', timeMode: 'point', workStart: '09:00', workEnd: '16:31',
      agentStrategy: 'auto', firstWarmup: '06:30', secondWarmup: '11:31',
    };
    const usage = { provider: 'cc', fiveHour: { utilization: 10, resetsAt: null, windowSeconds: 18000 } };
    const text = JSON.stringify(buildAgentControlCard(request, {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage },
    }));
    expect(text).toContain('当前仅检测到 Claude Code');
    expect(text).not.toContain('两个都用');
    expect(text).not.toContain('"both"');
  });
});
