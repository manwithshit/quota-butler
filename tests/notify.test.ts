import { describe, it, expect } from 'vitest';
import {
  usageBar,
  buildStatusCard,
  buildRecoveryCard,
  buildScheduleCard,
  buildBedtimeCard,
  buildTimeModeCard,
  buildCommandMenuCard,
  buildCurrentPlansCard,
  buildSupplementPlanCard,
  type Card,
  type DailyReportContext,
} from '../src/notify.js';
import { AgentState, type AgentStatus } from '../src/agent_status.js';
import { buildPlan } from '../src/planner.js';
import type { Usage } from '../src/providers/index.js';
import type { PlanRequest } from '../src/schedule_flow.js';
import type { UsageSnapshot } from '../src/state.js';

function md(card: Card): string {
  return card.body.elements.map((e) => (e['content'] as string) ?? '').join('\n');
}

function usage(util5: number, util7?: number): Usage {
  return {
    provider: 'codex',
    fiveHour: { utilization: util5, resetsAt: new Date('2026-06-22T00:53:00Z'), windowSeconds: 18000 },
    sevenDay: util7 == null ? null : { utilization: util7, resetsAt: null, windowSeconds: 604800 },
  };
}

describe('usageBar', () => {
  it('clamps and keeps minority side visible', () => {
    expect(usageBar(0)).toBe('░░░░░░░░░░');
    expect(usageBar(100)).toBe('██████████');
    expect(usageBar(63)).toBe('██████░░░░');
    expect(usageBar(99)).toBe('█████████░'); // 不再像满
    expect(usageBar(1)).toBe('█░░░░░░░░░'); // 不再像空
  });
});

describe('buildStatusCard', () => {
  it('shows remaining percentage', () => {
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage: usage(63) },
    };
    const text = md(buildStatusCard(statuses));
    expect(text).toContain('████░░░░░░ 还剩 **37%**');
  });

  it('warns when weekly quota caps the 5h window', () => {
    const statuses: Record<string, AgentStatus> = {
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: usage(1, 99) },
    };
    const text = md(buildStatusCard(statuses));
    expect(text).toContain('还剩 **99%**'); // 5h
    expect(text).toContain('还剩 **1%**'); // 周
    expect(text).toContain('7 天额度仅剩');
    expect(text).toContain('真正的上限');
  });

  it('token-stale does not tell a logged-in user to re-login, and shows snapshot', () => {
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.TOKEN_STALE, detail: 'CC token 已过期' },
    };
    const snap: Record<string, UsageSnapshot> = {
      cc: {
        fiveHourUtil: 20,
        fiveHourResetAt: null,
        sevenDayUtil: null,
        capturedAt: new Date(Date.now() - 3 * 3600000).toISOString(),
      },
    };
    const text = md(buildStatusCard(statuses, snap));
    expect(text).toContain('额度令牌已过期');
    expect(text).toContain('无需重新登录');
    expect(text).not.toContain('claude auth login');
    expect(text).toContain('上次成功'); // 快照回显
    expect(text).toContain('还剩 80%');
  });
});

function planReq(partial: Partial<PlanRequest>): PlanRequest {
  return {
    targetDate: '2026-06-20', timeMode: 'point', workStart: '09:00', workEnd: '16:31',
    agentStrategy: 'auto', firstWarmup: '06:30', secondWarmup: '11:31', ...partial,
  };
}

describe('睡前/明日计划卡：已移除"复用上次"', () => {
  const last = { timeMode: 'point', workStart: '09:30', workEnd: '14:30', agentStrategy: 'auto' };

  it('睡前卡只有「设置明天计划 / 明天不用」，无复用按钮', () => {
    const whole = JSON.stringify(buildBedtimeCard(undefined, last));
    expect(whole).not.toContain('复用上次');
    expect(whole).toContain('设置明天计划');
    expect(whole).toContain('schedule_intent');
    expect(whole).toContain('tomorrow_skip');
  });

  it('明日计划入口（时间模式卡）也不出现复用按钮', () => {
    const whole = JSON.stringify(buildTimeModeCard('2026-06-23', last));
    expect(whole).not.toContain('复用上次');
    expect(whole).toContain('选择重度使用时间');
    expect(whole).toContain('生成计划');
  });
});

describe('日报（晚卡上半段）', () => {
  it('汇总额度/消耗/预热/恢复/计划，并接到明天计划询问', () => {
    const now = new Date(2026, 5, 23, 22, 0);
    const tsToday = (h: number) => new Date(2026, 5, 23, h, 0).toISOString();
    const statuses: Record<string, AgentStatus> = {
      cc: {
        provider: 'cc',
        state: AgentState.CONNECTED,
        usage: {
          provider: 'cc',
          fiveHour: { utilization: 40, resetsAt: null, windowSeconds: 18000 },
          sevenDay: { utilization: 30, resetsAt: new Date(2026, 5, 26, 0, 0), windowSeconds: 604800 },
        },
      },
    };
    const ctx: DailyReportContext = {
      now,
      dayStart: { cc: { fiveHourUtil: 10, sevenDayUtil: 20, monthlyUtil: null } },
      eventLog: [
        { ts: tsToday(6), type: 'warmup', agent: 'cc', result: 'ok' },
        { ts: tsToday(11), type: 'warmup', agent: 'cc', result: 'ok' },
        { ts: tsToday(12), type: 'warmup', agent: 'cc', result: 'skip' },
        { ts: tsToday(9), type: 'recovery', agent: 'cc', window: 'fiveHour' },
        { ts: tsToday(10), type: 'recovery', agent: 'cc', window: 'sevenDay' },
      ],
      activePlan: { status: 'active', work_start: '2026-06-23T10:00:00', work_end: '2026-06-23T18:00:00', agents: ['cc'] },
    };
    const text = md(buildBedtimeCard(statuses, null, ctx));
    expect(text).toContain('今日小结');
    expect(text).toContain('周剩 70%'); // 100-30
    expect(text).toContain('今天用掉 周额度 ≈10%'); // 30-20
    expect(text).toContain('✅ 2'); // 两次成功
    expect(text).toContain('⏭️ 1'); // 一次跳过
    expect(text).toContain('5h 恢复 1 次');
    expect(text).toContain('周额度恢复 1 次');
    expect(text).toContain('已采用计划');
    expect(text).toContain('🌙 **明天有重度使用 AI 的计划吗？**'); // 仍接到原询问
  });

  it('无历史时给出最小日报，不报错', () => {
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage: usage(20) },
    };
    const text = md(buildBedtimeCard(statuses, null, { now: new Date(2026, 5, 23, 22, 0) }));
    expect(text).toContain('今日小结');
    expect(text).toContain('无定时预热任务');
  });
});

describe('buildRecoveryCard', () => {
  it('renders distinct copy for five-hour and weekly recoveries', () => {
    const five = JSON.stringify(buildRecoveryCard('cc', 'cc:fiveHour:2026-06-24T11:00:00.000Z', 'fiveHour'));
    const weekly = JSON.stringify(buildRecoveryCard('cc', 'cc:sevenDay:2026-06-24T11:00:00.000Z', 'sevenDay'));

    expect(five).toContain('5 小时额度已恢复');
    expect(weekly).toContain('周额度已刷新');
  });

  it('shows immediate warmup for weekly recovery cards', () => {
    const weekly = JSON.stringify(buildRecoveryCard('cc', 'cc:sevenDay:2026-06-24T11:00:00.000Z', 'sevenDay'));

    expect(weekly).toContain('立即预热');
  });
});

describe('buildScheduleCard', () => {
  it('single agent timeline: value-prop + colors + verb labels', () => {
    const plan = buildPlan(planReq({}), { cc: usage(30) });
    const card = buildScheduleCard(plan);
    const text = md(card);
    const whole = JSON.stringify(card);
    expect(text).toContain('09:00–16:31');
    expect(text).toContain('06:30');
    expect(text).toContain('11:31');
    expect(text).toContain('200%');
    expect(whole).toContain('采用计划');
    expect(whole).toContain('开始计时');
    expect(whole).toContain('续上额度');
    expect(whole).toContain('blue-200');
    expect(whole).toContain('grey-200');
    expect(whole).toContain('weighted');
    expect(text).not.toContain('准备第一个窗口'); // 旧技术风文案不出现在可见区（仅在内嵌 payload）
  });

  it('schedule card includes two warmup pickers but no tool switching/remind-only buttons', () => {
    const plan = buildPlan(planReq({}), { cc: usage(20), codex: usage(30) });
    const whole = JSON.stringify(buildScheduleCard(plan));
    expect(whole).toContain('first_warmup');
    expect(whole).toContain('second_warmup');
    expect(whole).not.toContain('更换 AI 工具');
    expect(whole).not.toContain('仅提醒');
  });
});

describe('menu and current plan cards', () => {
  it('menu has three actions and summarizes tomorrow preheat times', () => {
    const card = buildCommandMenuCard({
      '2099-06-24': {
        status: 'active',
        plan_id: 'tomorrow',
        work_start: '2099-06-24T09:00:00',
        work_end: '2099-06-24T16:31:00',
        agents: ['cc'],
        events: [
          { agent: 'cc', kind: 'warmup', at: '2099-06-24T06:30:00', purpose: '准备第一个窗口' },
          { agent: 'cc', kind: 'warmup', at: '2099-06-24T11:31:00', purpose: '恢复后准备第二个窗口' },
        ],
      },
    }, new Date('2099-06-23T13:00:00'));
    const whole = JSON.stringify(card);
    expect(whole).toContain('查询额度');
    expect(whole).toContain('设置明天计划');
    expect(whole).toContain('立即预热');
    expect(whole).not.toContain('查看当前计划');
    expect(whole).toContain('| 时间 | 模型 |');
    expect(whole).toContain('| 06:30 | Claude Code |');
    expect(whole).toContain('| 11:31 | Claude Code |');
    expect(whole).not.toContain('09:00–16:31');
  });

  it('current plan card offers supplement for the missing model', () => {
    const card = buildCurrentPlansCard({
      '2099-06-24': {
        status: 'active',
        plan_id: 'tomorrow',
        work_start: '2099-06-24T09:00:00',
        work_end: '2099-06-24T16:31:00',
        agents: ['cc'],
        events: [
          { agent: 'cc', kind: 'warmup', at: '2099-06-24T06:30:00', purpose: '准备第一个窗口' },
          { agent: 'cc', kind: 'warmup', at: '2099-06-24T11:31:00', purpose: '恢复后准备第二个窗口' },
        ],
      },
    }, new Date('2099-06-23T13:00:00'));
    const whole = JSON.stringify(card);
    expect(whole).toContain('也安排 Codex 预热');
    expect(whole).toContain('取消整日计划');
    expect(whole).toContain('| 时间 | 模型 | 状态 |');
    expect(whole).toContain('| 06:30 | Claude Code | 未执行 |');
    expect(whole).not.toContain('09:00–16:31');
  });

  it('supplement card reuses existing start and defaults to offset warmups', () => {
    const card = buildSupplementPlanCard({
      plan_id: 'tomorrow',
      work_start: '2026-06-24T09:00:00',
      agents: ['cc'],
    }, 'codex', '06:33', '11:34');
    const whole = JSON.stringify(card);
    expect(whole).toContain('为 Codex 安排明天预热');
    expect(whole).toContain('明天计划会变成 **Claude Code + Codex**');
    expect(whole).toContain('| 时间 | 模型 | 说明 |');
    expect(whole).toContain('06:33');
    expect(whole).toContain('11:34');
    expect(whole).toContain('采用补充计划');
  });
});
