import { describe, it, expect } from 'vitest';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WarmupScheduler } from '../src/scheduler.js';
import { StateStore } from '../src/state.js';
import type { LarkChannel } from '@larksuite/channel';

function tmpState(): StateStore {
  return new StateStore(join(tmpdir(), `qb-test-${Date.now()}-${Math.random()}.json`));
}

const fakeChannel = {} as unknown as LarkChannel;

describe('WarmupScheduler.arm', () => {
  it('skips past and already-executed nodes, arms only future ones', () => {
    const state = tmpState();
    const sch = new WarmupScheduler(fakeChannel, 'ou_x', state);
    const past = new Date(Date.now() - 3600000).toISOString();
    const future = new Date(Date.now() + 3600000).toISOString();
    const future2 = new Date(Date.now() + 7200000).toISOString();
    state.get().executedWarmups = [`p1:cc:${future}`]; // 这个未来节点已执行过

    sch.arm({
      plan_id: 'p1',
      status: 'active',
      events: [
        { agent: 'cc', kind: 'warmup', at: past, purpose: '' }, // 过期 → 跳过
        { agent: 'cc', kind: 'warmup', at: future, purpose: '' }, // 已执行 → 跳过
        { agent: 'codex', kind: 'warmup', at: future2, purpose: '' }, // 未来 → 布上
      ],
    });

    expect(sch.pending).toBe(1);
    sch.cancelAll();
    expect(sch.pending).toBe(0);
  });
});
