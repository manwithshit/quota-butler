import { describe, it, expect } from 'vitest';
import {
  usageBar,
  buildStatusCard,
  buildRecoveryCard,
  buildScheduleCard,
  buildBedtimeCard,
  buildTimeModeCard,
  buildAgentControlCard,
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
    const text = md(buildStatusCard(statuses, {}, new Date('2026-07-20T00:00:00Z')));
    expect(text).toContain('████░░░░░░ 还剩 **37%**');
  });

  it('warns when weekly quota caps the 5h window', () => {
    const statuses: Record<string, AgentStatus> = {
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: usage(1, 99) },
    };
    const text = md(buildStatusCard(statuses, {}, new Date('2026-07-20T00:00:00Z')));
    expect(text).toContain('还剩 **99%**'); // 5h
    expect(text).toContain('还剩 **1%**'); // 周
    expect(text).toContain('7 天额度仅剩');
    expect(text).toContain('真正的上限');
  });

  it('shows weekly-only Codex instead of reporting a missing quota window', () => {
    const statuses: Record<string, AgentStatus> = {
      codex: {
        provider: 'codex',
        state: AgentState.CONNECTED,
        usage: {
          provider: 'codex',
          fiveHour: null,
          sevenDay: { utilization: 38, resetsAt: new Date('2026-07-20T04:15:19Z'), windowSeconds: 604800 },
        },
      },
    };
    const text = md(buildStatusCard(statuses, {}, new Date('2026-07-20T00:00:00Z')));
    expect(text).toContain('Codex · 周额度');
    expect(text).toContain('还剩 **62%**');
    expect(text).toContain('没有 5 小时窗口');
    expect(text).toContain('当前周周期进行中，无需每日预热');
    expect(text).not.toContain('未读到额度窗口');
  });

  it('shows exact exhausted and ready copy for weekly-only Codex', () => {
    const status = (utilization: number, resetsAt: Date): Record<string, AgentStatus> => ({
      codex: {
        provider: 'codex',
        state: AgentState.CONNECTED,
        usage: {
          provider: 'codex',
          fiveHour: null,
          sevenDay: { utilization, resetsAt, windowSeconds: 604800 },
        },
      },
    });
    const now = new Date('2026-07-20T12:00:00');
    expect(md(buildStatusCard(status(100, new Date('2026-07-21T12:00:00')), {}, now)))
      .toContain('周额度已耗尽，等待下一个周期刷新。');
    expect(md(buildStatusCard(status(100, new Date('2026-07-20T11:59:00')), {}, now)))
      .toContain('新一周额度已就绪，发送任意消息开始计时。');
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

  it('睡前卡只有「设置明日计划 / 明天不用」，无复用按钮', () => {
    const whole = JSON.stringify(buildBedtimeCard(undefined, last));
    expect(whole).not.toContain('复用上次');
    expect(whole).toContain('设置明日计划');
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
  it('汇总今日额度/消耗/预热/恢复/计划，并在没有明日计划时继续询问', () => {
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

  it('已有明日计划时展示预热时间，不再继续询问是否规划', () => {
    const now = new Date(2026, 6, 4, 22, 0);
    const statuses: Record<string, AgentStatus> = {
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: usage(1, 11) },
    };
    const ctx: DailyReportContext = {
      now,
      eventLog: [],
      activePlan: {
        status: 'active',
        work_start: '2026-07-05T09:00:00',
        work_end: '2026-07-05T16:31:00',
        agents: ['codex'],
        events: [
          { agent: 'codex', kind: 'warmup', at: '2026-07-05T06:30:00', purpose: '开工前' },
          { agent: 'codex', kind: 'warmup', at: '2026-07-05T11:31:00', purpose: '续上额度' },
        ],
      },
    };
    const card = buildBedtimeCard(statuses, null, ctx);
    const text = md(card);
    const whole = JSON.stringify(card);

    expect(text).toContain('今日无已执行预热；明日已安排 2 个预热节点');
    expect(text).toContain('📅 **明日已安排** 09:00–16:31（Codex）');
    expect(text).toContain('预热：06:30 · Codex、11:31 · Codex');
    expect(text).not.toContain('明天可规划');
    expect(text).not.toContain('🌙 **明天有重度使用 AI 的计划吗？**');
    expect(whole).toContain('查看明日计划');
    expect(whole).toContain('取消明日计划');
    expect(whole).not.toContain('设置明日计划');
    expect(whole).not.toContain('tomorrow_skip');
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

  it('uses activation copy for weekly-only Codex at the weekly boundary', () => {
    const weekly = JSON.stringify(buildRecoveryCard(
      'codex',
      'codex:sevenDay:2026-06-24T11:00:00.000Z',
      'sevenDay',
      true,
    ));
    expect(weekly).toContain('新一周额度已就绪');
    expect(weekly).toContain('发送任意消息开始新周期计时');
    expect(weekly).toContain('立即开启新周期');
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

  it('schedule card includes two warmup pickers and the explicit tool selector', () => {
    const plan = buildPlan(planReq({}), { cc: usage(20), codex: usage(30) });
    const whole = JSON.stringify(buildScheduleCard(plan));
    expect(whole).toContain('first_warmup');
    expect(whole).toContain('second_warmup');
    expect(whole).toContain('选择 / 更换 AI 工具');
    expect(whole).toContain('adjust_schedule_agents');
    expect(whole).not.toContain('仅提醒');
  });

  it('weekly-only Codex active cycle has no daily warmup picker', () => {
    const weekly: Usage = {
      provider: 'codex',
      fiveHour: null,
      sevenDay: { utilization: 38, resetsAt: new Date('2026-07-20T04:15:19Z'), windowSeconds: 604800 },
    };
    const card = buildScheduleCard(buildPlan(planReq({ agentStrategy: 'codex' }), { codex: weekly }));
    const text = md(card);
    const whole = JSON.stringify(card);
    expect(whole).not.toContain('"name":"first_warmup"');
    expect(whole).not.toContain('"name":"second_warmup"');
    expect(text).toContain('周额度');
    expect(text).toContain('无需预热，明天可直接使用');
    expect(text).not.toContain('200%');
    expect(text).not.toContain('两个预热时间');
  });

  it('agent control offers individual tools but no upfront both option', () => {
    const request = planReq({});
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage: { ...usage(20), provider: 'cc' } },
      codex: { provider: 'codex', state: AgentState.CONNECTED, usage: usage(30) },
    };
    const whole = JSON.stringify(buildAgentControlCard(request, statuses));
    expect(whole).toContain('Claude Code');
    expect(whole).toContain('Codex');
    expect(whole).not.toContain('两个都用');
    expect(whole).not.toContain('\"agent_strategy\":\"both\"');
  });

  it('monthly-only Codex does not enter the dual-agent selector', () => {
    const request = planReq({});
    const statuses: Record<string, AgentStatus> = {
      cc: { provider: 'cc', state: AgentState.CONNECTED, usage: { ...usage(20), provider: 'cc' } },
      codex: {
        provider: 'codex',
        state: AgentState.CONNECTED,
        usage: {
          provider: 'codex',
          fiveHour: null,
          sevenDay: null,
          monthly: { utilization: 30, resetsAt: new Date('2026-07-31T00:00:00Z'), windowSeconds: 2592000 },
        },
      },
    };
    const whole = JSON.stringify(buildAgentControlCard(request, statuses));
    expect(whole).toContain('当前仅检测到 Claude Code');
    expect(whole).not.toContain('两个都用');
    expect(whole).not.toContain('\"agent_strategy\":\"both\"');
  });

  it('weekly-only Codex reset inside the plan creates one non-editable activation', () => {
    const weekly: Usage = {
      provider: 'codex',
      fiveHour: null,
      sevenDay: { utilization: 100, resetsAt: new Date('2026-06-20T13:00:00'), windowSeconds: 604800 },
    };
    const plan = buildPlan(
      planReq({ timeMode: 'range', workEnd: '18:00', agentStrategy: 'codex' }),
      { codex: weekly },
    );
    const card = buildScheduleCard(plan);
    const text = md(card);
    const whole = JSON.stringify(card);
    expect(text).toContain('13:01');
    expect(text).toContain('开启新一周额度');
    expect(whole).not.toContain('"name":"first_warmup"');
    expect(whole).toContain('"kind":"weekly-activation"');
  });
});
