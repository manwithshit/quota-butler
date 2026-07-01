// 飞书 CardKit 2.0 卡片构建。移植自 Python notify.py。

import { AgentState, isSchedulable, usableForPlanning, type AgentStatus } from './agent_status.js';
import { AGENT_LABELS, type SchedulePlan } from './planner.js';
import { planRecord } from './plan_record.js';
import {
  flowPayload,
  requestToPayloadShape,
  type PlanRequest,
} from './schedule_flow.js';
import type { DailyEvent, DayQuotaSnap, LastPlanRequest, QuotaWindowName, UsageSnapshot } from './state.js';

export const PROVIDER_LABEL: Record<string, string> = AGENT_LABELS;

// ---- 进度条 / 状态 -------------------------------------------------------

export function usageBar(percent: number, width = 10): string {
  if (width <= 0) return '';
  const value = Math.max(0, Math.min(percent, 100));
  let filled = Math.min(width, Math.floor((value * width) / 100 + 0.5));
  if (value > 0 && value < 100) filled = Math.max(1, Math.min(width - 1, filled));
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

export function remainingStatus(remaining: number): string {
  const v = Math.max(0, Math.min(remaining, 100));
  if (v > 70) return '🟢 余量充足';
  if (v > 30) return '🟡 正常使用';
  if (v > 10) return '🟠 注意消耗';
  return '🔴 接近耗尽';
}

function remainingLevel(remaining: number): string {
  const v = Math.max(0, Math.min(remaining, 100));
  if (v <= 0) return '耗尽';
  if (v <= 10) return '告急';
  if (v <= 30) return '偏低';
  if (v <= 70) return '正常';
  return '充足';
}

// ---- 当前额度卡 ----------------------------------------------------------

export function buildStatusCard(
  statuses: Record<string, AgentStatus>,
  snapshots: Record<string, UsageSnapshot> = {},
): Card {
  const lines: string[] = ['**当前额度**', ''];
  for (const provider of ['cc', 'codex']) {
    const status = statuses[provider];
    if (!status) continue;
    const label = PROVIDER_LABEL[provider]!;
    if (status.state === AgentState.CONNECTED && status.usage) {
      const five = status.usage.fiveHour;
      if (five) {
        const rem5 = 100 - five.utilization;
        const reset = formatReset(five);
        const cap = status.usage.sevenDay ?? status.usage.monthly ?? null;
        const capRem = cap ? 100 - cap.utilization : null;
        lines.push(
          `**${label}**`,
          `状态：${statusIcon(rem5, capRem)} ${cap ? `${windowLabel(cap)}${remainingLevel(capRem!)} · ` : ''}5 小时窗口${remainingLevel(rem5)}`,
          '',
          '**5 小时窗口**',
          `${usageBar(rem5)} 还剩 **${rem5.toFixed(0)}%**`,
          `刷新：**${reset}**`,
        );
        let rem7: number | null = null;
        if (status.usage.sevenDay) {
          rem7 = 100 - status.usage.sevenDay.utilization;
          lines.push('', '**7 天额度**', `${usageBar(rem7)} 还剩 **${rem7.toFixed(0)}%**`, `刷新：**${formatReset(status.usage.sevenDay)}**`);
        }
        if (rem7 != null && rem7 < 20 && rem7 < rem5) {
          lines.push(`⚠️ 7 天额度仅剩 **${rem7.toFixed(0)}%**，是真正的上限——5 小时窗口再充足也用不了多少。`);
        }
      } else if (status.usage.monthly) {
        // 免费档 Codex：只有月度窗口，不参与"预热/接力"计划。
        const m = status.usage.monthly;
        const remM = 100 - m.utilization;
        const reset = formatReset(m);
        lines.push(
          `**${label} · 月度额度**（免费档）`,
          `${usageBar(remM)} 还剩 **${remM.toFixed(0)}%**`,
          `${remainingStatus(remM)} · 重置：**${reset}**`,
          'ℹ️ 免费档只有月度额度，无 5 小时窗口，不参与预热计划。',
        );
      } else {
        lines.push(`**${label}**`, '🟠 **未读到额度窗口**');
      }
    } else if (status.state === AgentState.TOKEN_STALE) {
      lines.push(
        `**${label}**`,
        '🟡 **额度令牌已过期**',
        '登录仍有效，用一次 Claude 即可自动刷新（无需重新登录）。',
        ...snapshotLines(snapshots[provider]),
      );
    } else if (status.state === AgentState.NEEDS_LOGIN) {
      const instr = provider === 'cc' ? '`claude auth login`' : '`codex login`';
      lines.push(`**${label}**`, '🟡 **需要重新登录**', `请在本机运行 ${instr}`);
    } else if (status.state === AgentState.UNAVAILABLE) {
      lines.push(
        `**${label}**`,
        '🟠 **暂时无法读取**',
        '已检测到安装，稍后可重新查询。',
        ...snapshotLines(snapshots[provider]),
      );
    } else {
      lines.push(`**${label}**`, '⚪ **未检测到安装**');
    }
    lines.push('');
  }
  return card('额度管家：当前额度', lines);
}

// ---- 恢复 / 睡前 / 菜单 --------------------------------------------------

export function buildRecoveryCard(provider: string, windowKey: string, window: QuotaWindowName = 'fiveHour'): Card {
  const label = PROVIDER_LABEL[provider] ?? provider;
  const copy = recoveryCopy(label, window);
  const buttons = [];
  buttons.push(button('立即预热', 'primary', cb('warmup_now', { provider, window_key: windowKey })));
  buttons.push(
    button('30 分钟后提醒', 'default', cb('recovery_snooze', { provider, window_key: windowKey, window, minutes: 30 })),
    button('暂时不用', 'default', cb('recovery_skip', { provider, window_key: windowKey, window })),
  );
  return card(copy.title, [`⚡ **${copy.body}**`], buttons);
}

function recoveryCopy(label: string, window: QuotaWindowName): { title: string; body: string } {
  if (window === 'sevenDay') {
    return {
      title: `${label} 周额度已刷新`,
      body: `${label} 周额度已刷新，可以重新安排重度任务。`,
    };
  }
  return {
    title: `${label} 5 小时额度已恢复`,
    body: `${label} 5 小时额度已恢复，可以开始一段重点使用。`,
  };
}

/** 日报上下文：晚卡上半段用。 */
export interface DailyReportContext {
  eventLog?: DailyEvent[];
  dayStart?: Record<string, DayQuotaSnap>;
  activePlan?: unknown;
  now?: Date;
}

export function buildBedtimeCard(
  statuses?: Record<string, AgentStatus>,
  _lastPlan?: LastPlanRequest | null,
  report?: DailyReportContext,
): Card {
  const lines: string[] = [];
  if (statuses && report) lines.push(...dailyReportLines(statuses, report));
  if (statuses) {
    const connected = Object.values(statuses)
      .filter((s) => isSchedulable(s) && s.usage && usableForPlanning(s.usage))
      .map((s) => PROVIDER_LABEL[s.provider]!);
    if (connected.length) lines.push(`明天可规划：**${connected.join(' + ')}**`, '');
  }
  lines.push('🌙 **明天有重度使用 AI 的计划吗？**');
  const target = tomorrowIso();
  const buttons = [
    button('设置明天计划', 'primary', cb('schedule_intent', { intent: 'tomorrow', target_date: target })),
    button('明天不用', 'default', cb('tomorrow_skip')),
  ];
  return card('额度管家：明日计划', lines, buttons, buttons.length);
}

// ---- 日报（拼进晚卡上半段）----------------------------------------------

function dailyReportLines(
  statuses: Record<string, AgentStatus>,
  ctx: DailyReportContext,
): string[] {
  const now = ctx.now ?? new Date();
  const today = localDate(now);
  const lines: string[] = [`📋 **今日小结 · ${pad(now.getMonth() + 1)}-${pad(now.getDate())}**`, ''];

  // A1/A2 额度盘点 + 健康度
  const quota: string[] = [];
  for (const provider of ['cc', 'codex']) {
    const s = statuses[provider];
    if (!s) continue;
    const label = PROVIDER_LABEL[provider]!;
    if (s.state === AgentState.CONNECTED && s.usage) {
      const parts: string[] = [];
      if (s.usage.fiveHour) parts.push(`5h 剩 ${(100 - s.usage.fiveHour.utilization).toFixed(0)}%`);
      if (s.usage.sevenDay) parts.push(`周剩 ${(100 - s.usage.sevenDay.utilization).toFixed(0)}%`);
      if (!s.usage.fiveHour && s.usage.monthly) parts.push(`月度剩 ${(100 - s.usage.monthly.utilization).toFixed(0)}%`);
      quota.push(`· ${label}：${parts.join(' · ') || '—'}`);
    } else if (s.state === AgentState.TOKEN_STALE) {
      quota.push(`· ${label}：🟡 令牌过期，用一次 CLI 即可自愈`);
    } else if (s.state === AgentState.NEEDS_LOGIN) {
      quota.push(`· ${label}：🟡 需重新登录`);
    } else if (s.state === AgentState.UNAVAILABLE) {
      const why = s.detail ? `（${s.detail.slice(0, 24)}）` : '';
      quota.push(`· ${label}：🟠 暂时读不到额度${why}`);
    }
  }
  if (quota.length) lines.push('**额度**', ...quota, '');

  // C 当日消耗 + 见底预测
  const consume = consumptionLines(statuses, ctx.dayStart);
  if (consume.length) lines.push('**消耗**', ...consume, '');

  // B1 预热执行 / B2 恢复
  const todays = (ctx.eventLog ?? []).filter((e) => localDate(new Date(e.ts)) === today);
  const warmups = todays.filter((e) => e.type === 'warmup');
  if (warmups.length) {
    const ok = warmups.filter((e) => e.result === 'ok').length;
    const fail = warmups.filter((e) => e.result === 'fail').length;
    const skip = warmups.filter((e) => e.result === 'skip').length;
    lines.push(`**今日预热**：${warmups.length} 次（✅ ${ok} · ❌ ${fail} · ⏭️ ${skip}）`);
  } else {
    lines.push('**今日预热**：无定时预热任务');
  }
  const recos = todays.filter((e) => e.type === 'recovery');
  if (recos.length) {
    const counts = countRecoveriesByWindow(recos);
    lines.push(`**今日恢复**：${recoverySummary(counts)}`);
  }

  // A3 计划状态
  const planLine = activePlanLine(ctx.activePlan);
  if (planLine) lines.push(planLine);

  lines.push('');
  return lines;
}

function countRecoveriesByWindow(events: DailyEvent[]): Record<QuotaWindowName, number> {
  return events.reduce<Record<QuotaWindowName, number>>((acc, event) => {
    const window = event.window ?? 'fiveHour';
    acc[window] += 1;
    return acc;
  }, { fiveHour: 0, sevenDay: 0, monthly: 0 });
}

function recoverySummary(counts: Record<QuotaWindowName, number>): string {
  const parts: string[] = [];
  if (counts.fiveHour) parts.push(`5h 恢复 ${counts.fiveHour} 次`);
  if (counts.sevenDay) parts.push(`周额度恢复 ${counts.sevenDay} 次`);
  return parts.join(' · ');
}

function consumptionLines(
  statuses: Record<string, AgentStatus>,
  dayStart: Record<string, DayQuotaSnap> | undefined,
): string[] {
  if (!dayStart) return [];
  const out: string[] = [];
  for (const provider of ['cc', 'codex']) {
    const s = statuses[provider];
    if (!s || s.state !== AgentState.CONNECTED || !s.usage) continue;
    const start = dayStart[provider];
    if (!start) continue;
    // 消耗看长窗（真正的上限）：周优先，否则月度。
    const longNow = s.usage.sevenDay ?? s.usage.monthly ?? null;
    if (!longNow) continue;
    const startUtil = s.usage.sevenDay ? start.sevenDayUtil : start.monthlyUtil;
    if (startUtil == null) continue;
    const winName = s.usage.sevenDay ? '周额度' : '月度额度';
    const label = PROVIDER_LABEL[provider]!;
    const remaining = 100 - longNow.utilization;
    const consumed = longNow.utilization - startUtil;
    if (consumed < 0) {
      out.push(`· ${label} ${winName}今天重置过，现剩 ${remaining.toFixed(0)}%`);
      continue;
    }
    out.push(`· ${label}：今天用掉 ${winName} ≈${consumed.toFixed(0)}%，还剩 ${remaining.toFixed(0)}%`);
  }
  return out;
}

function activePlanLine(activePlan: unknown): string | null {
  const a = activePlan as Record<string, unknown> | null;
  if (!a || a['status'] !== 'active') return null;
  const startIso = String(a['work_start'] ?? '');
  const dateLabel = startIso.length >= 10 ? startIso.slice(5, 10) : '';
  const agents = (a['agents'] as string[] | undefined) ?? [];
  const labels = agents.map((x) => PROVIDER_LABEL[x] ?? x).join(' + ');
  const next = nextWarmupText(a);
  return `📅 **已采用计划** ${dateLabel}（${labels}${next ? ` · 下一次 ${next}` : ''}）`;
}

function localDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function buildCommandMenuCard(plans: Record<string, Record<string, unknown>> = {}, now = new Date()): Card {
  const lines = ['**当前安排**', ...menuPlanLines(plans, now), '', '**想做什么？**'];
  return card(
    '额度管家',
    lines,
    [
      button('查询额度', 'primary', cb('query_status')),
      button('设置明天计划', 'default', cb('schedule_intent', { intent: 'tomorrow' })),
      button('立即预热', 'default', cb('manual_warmup')),
    ],
    1,
  );
}

// ---- 立即预热 -----------------------------------------------------------

export function buildManualWarmupCard(statuses: Record<string, AgentStatus>): Card {
  const lines: string[] = [];
  const buttons: Array<Record<string, unknown>> = [];
  for (const provider of ['cc', 'codex']) {
    const status = statuses[provider];
    const reason = status ? manualWarmupBlockReason(status) : `${PROVIDER_LABEL[provider]!}：暂不可用`;
    if (!reason) {
      buttons.push(button(PROVIDER_LABEL[provider]!, buttons.length === 0 ? 'primary' : 'default', cb('warmup_now', { provider })));
    } else {
      lines.push(reason);
    }
  }
  if (buttons.length) lines.unshift('**选择要立即预热的 AI 工具**', '点击后会立刻发起一次真实请求。');
  return card('额度管家：立即预热', lines, buttons);
}

// ---- 时间选择 / 换工具 ---------------------------------------------------

export function buildTimeModeCard(targetDate: string, _lastPlan?: LastPlanRequest | null): Card {
  const base: PlanRequest = {
    targetDate, timeMode: 'point', workStart: '09:00', workEnd: '16:31', agentStrategy: 'auto', firstWarmup: '06:30', secondWarmup: '11:31',
  };
  return buildTimeCard(base);
}

export function buildTimeCard(request: PlanRequest, error = ''): Card {
  const fields: Array<Record<string, unknown>> = [
    {
      tag: 'picker_time', name: 'work_start',
      placeholder: { tag: 'plain_text', content: '选择开始时间' },
      initial_time: request.workStart, required: true,
    },
  ];
  fields.push({
    tag: 'button', name: 'submit_plan_time',
    text: { tag: 'plain_text', content: '生成计划' },
    type: 'primary', width: 'fill', form_action_type: 'submit',
    behaviors: [{ type: 'callback', value: flowPayload('generate_plan', request) }],
  });
  const lines = [
    '**选择重度使用时间**',
    '只需要选择开始时间；系统会优先选择一个可用工具，并生成两次预热节点。',
    '确认计划前，你还可以调整两次预热时间。',
  ];
  if (error) lines.push('', `❌ ${error}`);
  return {
    schema: '2.0',
    config: { summary: { content: '额度管家：设置使用时间' } },
    body: {
      elements: [
        { tag: 'markdown', content: lines.join('\n') },
        { tag: 'form', name: 'v3_plan_time', elements: fields },
      ],
    },
  };
}

export function buildAgentControlCard(request: PlanRequest, statuses: Record<string, AgentStatus>): Card {
  const available = ['cc', 'codex'].filter((p) => statuses[p] && isSchedulable(statuses[p]!));
  if (available.length <= 1) {
    const label = available[0] ? PROVIDER_LABEL[available[0]] : '可用 Agent';
    return card('更换 AI 工具', [`当前仅检测到 ${label}。`, '', '重新检测后会按最新状态生成计划。'], [
      button('重新检测', 'primary', cb('redetect_agents', { request: requestToPayloadShape(request) })),
    ]);
  }
  const buttons = ([['Claude Code', 'cc'], ['Codex', 'codex'], ['两个都用', 'both']] as const).map(
    ([label, strategy]) => {
      const candidate: PlanRequest = { ...request, agentStrategy: strategy };
      return button(label, strategy === request.agentStrategy ? 'primary' : 'default', {
        ...flowPayload('generate_plan', candidate),
        agent_strategy: strategy,
      });
    },
  );
  return card('更换 AI 工具', ['**明天想使用哪个 AI 工具？**'], buttons);
}

// ---- 当前计划卡 ----------------------------------------------------------

export function buildActivePlanCard(record: Record<string, unknown>): Card {
  if (record['manual']) {
    const evts = (record['events'] as Array<Record<string, unknown>> | undefined) ?? [];
    const lines = ['**手动预热（已采用）**'];
    for (const ev of evts) {
      lines.push(`🔥 明天 **${hhmmOf(ev['at'])}** 预热 ${PROVIDER_LABEL[String(ev['agent'])] ?? String(ev['agent'])}`);
    }
    lines.push('到点自动开窗，开完即结束。');
    return card('额度管家：当前计划', lines, [button('取消整日计划', 'danger', cb('cancel_schedule'))]);
  }
  const start = hhmmOf(record['work_start']);
  const startIso = String(record['work_start'] ?? '');
  const planDateLabel = startIso.length >= 10 ? startIso.slice(5, 10) : ''; // MM-DD（今天/明天都对）
  const agents = (record['agents'] as string[] | undefined) ?? [];
  const labels = agents.map((a) => PROVIDER_LABEL[a] ?? a).join(' + ');
  const lines = [
    `**已采用计划${planDateLabel ? ` · ${planDateLabel}` : ''}**`,
    `使用开始：**${start}**`,
    `负责的 AI：**${labels || '未记录'}**`,
    '',
    '额度管家会到点自动帮你预热：',
  ];
  const events = (record['events'] as Array<Record<string, unknown>> | undefined) ?? [];
  for (const ev of events) {
    const label = PROVIDER_LABEL[String(ev['agent'])] ?? String(ev['agent']);
    const purpose = ev['purpose'] ? ` · ${String(ev['purpose'])}` : '';
    lines.push(`🔥 **${hhmmOf(ev['at'])}** 预热 ${label}${purpose}`);
  }
  if (agents.length > 1) lines.push('', '<font color="grey">取消会移除当天全部预热任务。</font>');
  return card('额度管家：当前计划', lines, [button('取消整日计划', 'danger', cb('cancel_schedule', { target_date: startIso.slice(0, 10) }))]);
}

export function buildCurrentPlansCard(plans: Record<string, Record<string, unknown>>, now = new Date()): Card {
  const today = localDate(now);
  const tomorrow = localDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
  const lines = ['**当前计划**', ''];
  const buttons: Array<Record<string, unknown>> = [];
  for (const [label, day] of [['今日计划', today], ['明日计划', tomorrow]] as const) {
    const record = plans[day];
    lines.push(`**${label}**`);
    if (!record) {
      lines.push('暂无计划', '');
      continue;
    }
    const agents = ((record['agents'] as string[] | undefined) ?? []).map((a) => PROVIDER_LABEL[a] ?? a).join(' + ');
    lines.push(`已安排：${agents || '未记录'}`, ...warmupTable(record, true));
    const supplement = label === '明日计划' ? supplementAgent(record) : null;
    if (supplement && hasPendingWarmup(record)) {
      buttons.push(button(`也安排 ${PROVIDER_LABEL[supplement]} 预热`, 'primary', cb('append_schedule_agent', {
        target_date: day,
        plan_id: String(record['plan_id'] ?? ''),
        agent: supplement,
      })));
    }
    if (hasPendingWarmup(record)) buttons.push(button('取消整日计划', 'danger', cb('cancel_schedule', { target_date: day })));
    lines.push('');
  }
  return card('额度管家：当前计划', lines, buttons, 1);
}

export function buildSupplementPlanCard(
  record: Record<string, unknown>,
  agent: string,
  firstWarmup: string,
  secondWarmup: string,
): Card {
  const label = PROVIDER_LABEL[agent] ?? agent;
  const start = hhmmOf(record['work_start']);
  const target = String(record['work_start'] ?? '').slice(0, 10);
  const existing = ((record['agents'] as string[] | undefined) ?? []).map((a) => PROVIDER_LABEL[a] ?? a);
  const mergedLabels = [...existing, label].join(' + ');
  return {
    schema: '2.0',
    config: { summary: { content: `额度管家：也安排 ${label}` } },
    body: {
      elements: [
        {
          tag: 'markdown',
          content: [
            `**为 ${label} 安排明天预热**`,
            `沿用已有计划的使用开始时间：**${start}**。`,
            `采用后，明天计划会变成 **${mergedLabels || label}**。`,
            '取消整日计划会取消明天全部预热。',
            '',
            '| 时间 | 模型 | 说明 |',
            '|---|---|---|',
            `| ${firstWarmup} | ${label} | 第一次预热 |`,
            `| ${secondWarmup} | ${label} | 第二次预热 |`,
            '',
            '你可以直接采用，也可以调整下面两个预热时间。',
          ].join('\n'),
        },
        {
          tag: 'form',
          name: 'append_schedule_form',
          elements: [
            {
              tag: 'picker_time', name: 'first_warmup',
              placeholder: { tag: 'plain_text', content: '第一次预热' },
              initial_time: firstWarmup, required: true,
            },
            {
              tag: 'picker_time', name: 'second_warmup',
              placeholder: { tag: 'plain_text', content: '第二次预热' },
              initial_time: secondWarmup, required: true,
            },
            {
              tag: 'button', name: 'submit_append_schedule',
              text: { tag: 'plain_text', content: '采用补充计划' },
              type: 'primary', width: 'fill', form_action_type: 'submit',
              behaviors: [{ type: 'callback', value: cb('adopt_schedule_append', {
                target_date: target,
                previous_plan_id: String(record['plan_id'] ?? ''),
                agent,
              }) }],
            },
          ],
        },
      ],
    },
  };
}

// ---- 时间轴计划卡（核心）------------------------------------------------

export function buildScheduleCard(plan: SchedulePlan): Card {
  const record = planRecord(plan);
  const elements = scheduleTimelineElements(plan);
  const events = ([...record['events']] as unknown as Array<Record<string, unknown>>)
    .sort((a, b) => String(a['at']).localeCompare(String(b['at'])));
  elements.push({
    tag: 'form',
    name: 'adopt_schedule_form',
    elements: [
      {
        tag: 'picker_time', name: 'first_warmup',
        placeholder: { tag: 'plain_text', content: '第一次预热' },
        initial_time: hhmmOf(events[0]?.['at']), required: true,
      },
      {
        tag: 'picker_time', name: 'second_warmup',
        placeholder: { tag: 'plain_text', content: '第二次预热' },
        initial_time: hhmmOf(events[1]?.['at']), required: true,
      },
      {
        tag: 'button', name: 'submit_adopt_schedule',
        text: { tag: 'plain_text', content: '采用计划' },
        type: 'primary', width: 'fill', form_action_type: 'submit',
        behaviors: [{ type: 'callback', value: cb('adopt_schedule', { plan: record }) }],
      },
    ],
  });
  return { schema: '2.0', config: { summary: { content: '额度管家：明日计划预览' } }, body: { elements } };
}

function menuPlanLines(plans: Record<string, Record<string, unknown>>, now: Date): string[] {
  const today = localDate(now);
  const tomorrow = localDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
  const lines: string[] = [];
  const todayPlan = plans[today];
  if (todayPlan) lines.push(...compactPlanLines('今天计划', todayPlan));
  const tomorrowPlan = plans[tomorrow];
  if (tomorrowPlan) lines.push(...compactPlanLines('明天计划', tomorrowPlan));
  if (!tomorrowPlan) lines.push('明天暂无计划');
  return lines;
}

function compactPlanLines(label: string, record: Record<string, unknown>): string[] {
  const rows = warmupTable(record, false);
  return [`**${label}**`, ...rows];
}

function warmupEvents(record: Record<string, unknown>): Array<Record<string, unknown>> {
  return ([...((record['events'] as Array<Record<string, unknown>> | undefined) ?? [])] as Array<Record<string, unknown>>)
    .filter((ev) => String(ev['kind'] ?? ev['type'] ?? 'warmup') === 'warmup')
    .sort((a, b) => String(a['at']).localeCompare(String(b['at'])) || String(a['agent']).localeCompare(String(b['agent'])));
}

function warmupTable(record: Record<string, unknown>, includeStatus: boolean): string[] {
  const events = warmupEvents(record);
  if (!events.length) return ['暂无预热节点'];
  const lines = includeStatus
    ? ['| 时间 | 模型 | 状态 |', '|---|---|---|']
    : ['| 时间 | 模型 |', '|---|---|'];
  for (const ev of events) {
    const label = PROVIDER_LABEL[String(ev['agent'])] ?? String(ev['agent']);
    if (includeStatus) lines.push(`| ${hhmmOf(ev['at'])} | ${label} | ${eventStatus(record, ev)} |`);
    else lines.push(`| ${hhmmOf(ev['at'])} | ${label} |`);
  }
  return lines;
}

function nextWarmupText(record: Record<string, unknown>): string | null {
  const now = Date.now();
  const next = ([...((record['events'] as Array<Record<string, unknown>> | undefined) ?? [])] as Array<Record<string, unknown>>)
    .filter((ev) => new Date(String(ev['at'] ?? '')).getTime() > now)
    .sort((a, b) => String(a['at']).localeCompare(String(b['at'])))[0];
  if (!next) return null;
  return `${hhmmOf(next['at'])} ${PROVIDER_LABEL[String(next['agent'])] ?? String(next['agent'])}`;
}

function supplementAgent(record: Record<string, unknown>): string | null {
  if (record['manual']) return null;
  const agents = (record['agents'] as string[] | undefined) ?? [];
  if (agents.length !== 1) return null;
  const missing = ['cc', 'codex'].find((a) => !agents.includes(a));
  return missing ?? null;
}

function scheduleTimelineElements(plan: SchedulePlan): Array<Record<string, unknown>> {
  const md = (content: string) => ({ tag: 'markdown', content, text_align: 'left' });
  const ws = plan.workStart;
  const we = plan.workEnd;
  const workHours = (we.getTime() - ws.getTime()) / 3600000;
  const first = plan.agents[0]!;
  const firstLabel = PROVIDER_LABEL[first]!;
  const fw = plan.events.filter((e) => e.agent === first).map((e) => e.at).sort((a, b) => a.getTime() - b.getTime());
  const prepStart = fw[0] ?? ws;
  const secondWarm = fw[1] ?? we;
  const windowCount = Math.max(1, fw.length);
  const dual = false;

  const bar: Array<Record<string, unknown>> = [segColumn(1, 'grey-200', '预备')];
  const axis: Array<Record<string, unknown>> = [segColumn(1, null, `${hm(prepStart)}\n开始计时`)];

  let headline: Record<string, unknown>;
  let sub: Record<string, unknown>;
  let metric: Record<string, unknown>;
  let baseline: Record<string, unknown>;
  const phases: Array<Record<string, unknown>> = [];

  if (dual) {
    const relay = plan.agents[1]!;
    const relayLabel = PROVIDER_LABEL[relay]!;
    const rw = plan.events.filter((e) => e.agent === relay).map((e) => e.at).sort((a, b) => a.getTime() - b.getTime());
    const relayAt = rw[rw.length - 1]!;
    const prePin = rw.length > 1 ? rw[0]! : null;
    const w1End = new Date(Math.min(Math.max(secondWarm.getTime(), ws.getTime()), relayAt.getTime()));
    const w1h = (w1End.getTime() - ws.getTime()) / 3600000;
    const w2h = (relayAt.getTime() - w1End.getTime()) / 3600000;
    const relayH = (we.getTime() - relayAt.getTime()) / 3600000;
    bar.push(segColumn(segWeight(w1h), 'blue-200', `${firstLabel} 窗口 1\n**100%**`));
    bar.push(segColumn(segWeight(w2h), 'blue-200', `${firstLabel} 窗口 2\n**100%**`));
    bar.push(segColumn(segWeight(relayH), 'wathet-200', `${relayLabel}\n接力`));
    axis.push(segColumn(segWeight(w1h), null, `${hm(ws)}\n你开工`));
    axis.push(segColumn(segWeight(w2h), null, `${hm(secondWarm)}\n续上额度`));
    axis.push(segColumn(segWeight(relayH), null, `${hm(relayAt)}\n${relayLabel} 接力`));
    headline = md(`**明天 ${hm(ws)}–${endLabel(ws, we)} 连续可用 · ${firstLabel} 为主，${relayLabel} 接力**`);
    sub = md(`先用 ${firstLabel}；等它的额度用到交接点，${relayLabel} 自动接上，让你一整天连续用、不会中途被卡。`);
    metric = md(`📊 **前 5 小时 ≈ 200% 额度**（${firstLabel} 两窗）　·　**全程 ${fmtHours(workHours)} 小时连续可用**`);
    baseline = md(`<font color='grey'>不安排的话：同样时间最多撑住 1～2 个窗口，中途大概率被卡。</font>`);
    phases.push(md(`✅ **开工前** · ${hm(prepStart)} 启动 ${firstLabel}，${hm(ws)} 打开直接用。`));
    phases.push(md(`🔄 **工作中** · ${hm(secondWarm)} 自动续上第二档 ${firstLabel}。`));
    if (prePin) {
      phases.push(md(`➕ **接力延长** · ${relayLabel} 提前在 ${hm(prePin)} 备好窗口，${hm(relayAt)} 准点接上，一直用到 ${hm(we)}。`));
    } else {
      phases.push(md(`➕ **接力延长** · ${hm(relayAt)} 起 ${relayLabel} 接上，一直用到 ${hm(we)}。`));
    }
  } else {
    const w1End = new Date(Math.min(Math.max(secondWarm.getTime(), ws.getTime()), we.getTime()));
    const w1h = (w1End.getTime() - ws.getTime()) / 3600000;
    const w2h = (we.getTime() - w1End.getTime()) / 3600000;
    bar.push(segColumn(segWeight(w1h), 'blue-200', '窗口 1\n**100%**'));
    bar.push(segColumn(segWeight(w2h), 'blue-200', '窗口 2\n**100%**'));
    axis.push(segColumn(segWeight(w1h), null, `${hm(ws)}\n你开工`));
    axis.push(segColumn(segWeight(w2h), null, `${hm(secondWarm)}\n续上额度`));
    headline = md(`**明天 ${hm(ws)}–${endLabel(ws, we)}：${firstLabel}**`);
    sub = md('默认约 **7.5 小时**，尽量吃满单个工具两段 5 小时窗口，约等于 **200%** 可用窗口。');
    metric = md(`重点使用区间：**${hm(ws)}–${endLabel(ws, we)}**。将创建 **${windowCount}** 个预热任务；每次预热都会发起一次真实请求。`);
    baseline = md('确认前可以调整两个预热时间；两个预热时间至少相隔 5 小时。');
    phases.push(md(`✅ **开工前** · ${hm(prepStart)} 替你启动一档额度，${hm(ws)} 打开直接用。`));
    phases.push(md(`🔄 **工作中** · ${hm(secondWarm)} 自动续上第二档，你不用管。`));
  }

  return [
    headline, sub,
    row(bar, '8px 0px 2px 0px'),
    row(axis, '0px 0px 6px 0px'),
    metric, baseline, ...phases,
  ];
}

// ---- 卡片/按钮辅助 -------------------------------------------------------

export interface Card {
  schema: string;
  config: { summary: { content: string } };
  body: { elements: Array<Record<string, unknown>> };
}

function card(
  summary: string,
  lines: string[],
  buttons?: Array<Record<string, unknown>>,
  perRow = 2,
): Card {
  const elements: Array<Record<string, unknown>> = [{ tag: 'markdown', content: lines.join('\n') }];
  if (buttons) {
    for (let i = 0; i < buttons.length; i += perRow) {
      elements.push({
        tag: 'column_set',
        columns: buttons.slice(i, i + perRow).map((b) => ({ tag: 'column', elements: [b] })),
      });
    }
  }
  return { schema: '2.0', config: { summary: { content: summary } }, body: { elements } };
}

function button(text: string, type: string, value: Record<string, unknown>): Record<string, unknown> {
  return {
    tag: 'button',
    text: { tag: 'plain_text', content: text },
    type,
    width: 'fill',
    behaviors: [{ type: 'callback', value }],
  };
}

function cb(action: string, fields: Record<string, unknown> = {}): Record<string, unknown> {
  return { cmd: 'quota', action, ...fields };
}


function segColumn(weight: number, bg: string | null, content: string): Record<string, unknown> {
  const col: Record<string, unknown> = {
    tag: 'column', width: 'weighted', weight, vertical_align: 'center', padding: '7px 2px',
    elements: [{ tag: 'markdown', content, text_align: 'center' }],
  };
  if (bg) col['background_style'] = bg;
  return col;
}

function row(columns: Array<Record<string, unknown>>, margin: string): Record<string, unknown> {
  return { tag: 'column_set', horizontal_spacing: '4px', margin, columns };
}

function segWeight(hours: number): number {
  return Math.max(2, Math.min(5, Math.round(hours)));
}

function fmtHours(hours: number): string {
  const v = Math.round(hours * 10) / 10;
  return Math.abs(v - Math.round(v)) < 0.05 ? String(Math.round(v)) : v.toFixed(1);
}

function snapshotLines(snap: UsageSnapshot | undefined): string[] {
  if (!snap) return [];
  const ageH = Math.max(0, (Date.now() - new Date(snap.capturedAt).getTime()) / 3600000);
  const ageText = ageH < 1 ? '不到 1' : ageH.toFixed(0);
  if (snap.fiveHourUtil != null) {
    const rem5 = (100 - snap.fiveHourUtil).toFixed(0);
    return [`<font color='grey'>上次成功：约 ${ageText} 小时前 · 5 小时还剩 ${rem5}%</font>`];
  }
  if (snap.monthlyUtil != null) {
    const remM = (100 - snap.monthlyUtil).toFixed(0);
    return [`<font color='grey'>上次成功：约 ${ageText} 小时前 · 月度还剩 ${remM}%</font>`];
  }
  return [`<font color='grey'>上次成功：约 ${ageText} 小时前</font>`];
}

function fmtReset(d: Date): string {
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatReset(window: { utilization: number; resetsAt?: Date | null; windowSeconds?: number | null }): string {
  if (window.resetsAt) return fmtReset(window.resetsAt);
  if (window.windowSeconds === 5 * 3600 && window.utilization <= 0) return '发送任意消息时触发';
  return '暂无';
}

function windowLabel(window: { windowSeconds?: number | null }): string {
  if (window.windowSeconds === 5 * 3600) return '5 小时窗口';
  if (window.windowSeconds === 7 * 86400) return '7 天额度';
  if (window.windowSeconds && window.windowSeconds >= 28 * 86400 && window.windowSeconds <= 31 * 86400) return '月度额度';
  return '额度';
}

function statusIcon(fiveRemaining: number, capRemaining: number | null): string {
  if (capRemaining != null) {
    if (capRemaining <= 10) return '🔴';
    if (capRemaining <= 30 || fiveRemaining <= 10) return '🟠';
    if (capRemaining <= 70 || fiveRemaining <= 30) return '🟡';
    return '🟢';
  }
  if (fiveRemaining <= 10) return '🔴';
  if (fiveRemaining <= 30) return '🟠';
  if (fiveRemaining <= 70) return '🟡';
  return '🟢';
}

function manualWarmupBlockReason(status: AgentStatus): string {
  const label = PROVIDER_LABEL[status.provider] ?? status.provider;
  if (status.state === AgentState.NEEDS_LOGIN) return `${label}：需要重新登录`;
  if (status.state !== AgentState.CONNECTED || !status.usage) return `${label}：暂不可用`;
  const weekly = status.usage.sevenDay;
  if (weekly && weekly.utilization >= 100) return `${label}：7 天额度已耗尽，暂不可预热`;
  const five = status.usage.fiveHour;
  if (!five) return `${label}：没有 5 小时窗口，暂不可预热`;
  const reset = five.resetsAt;
  if (reset) {
    const now = Date.now();
    if (reset.getTime() > now && five.utilization < 100) return `${label}：当前 5 小时窗口已在进行中，无需立即预热`;
    if (reset.getTime() > now && five.utilization >= 100) return `${label}：5 小时窗口已用尽，等待刷新后再预热`;
  }
  if (five.utilization >= 100) return `${label}：5 小时窗口已用尽，等待刷新后再预热`;
  return '';
}

function hasPendingWarmup(record: Record<string, unknown>): boolean {
  const now = Date.now();
  const planId = String(record['plan_id'] ?? '');
  const executed = new Set((record['executed_warmups'] as string[] | undefined) ?? []);
  for (const ev of (record['events'] as Array<Record<string, unknown>> | undefined) ?? []) {
    const at = String(ev['at'] ?? '');
    const key = `${planId}:${String(ev['agent'])}:${at}`;
    if (!executed.has(key) && new Date(at).getTime() > now) return true;
  }
  return false;
}

function eventStatus(record: Record<string, unknown>, ev: Record<string, unknown>): string {
  const planId = String(record['plan_id'] ?? '');
  const at = String(ev['at'] ?? '');
  const executed = new Set((record['executed_warmups'] as string[] | undefined) ?? []);
  if (executed.has(`${planId}:${String(ev['agent'])}:${at}`)) return '已执行';
  return new Date(at).getTime() <= Date.now() ? '已过期' : '未执行';
}

function hm(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 结束时间标签：跨午夜（结束日期晚于开始日期）时加"次日"前缀。 */
function endLabel(ws: Date, we: Date): string {
  const crossDay =
    we.getFullYear() !== ws.getFullYear() ||
    we.getMonth() !== ws.getMonth() ||
    we.getDate() !== ws.getDate();
  return crossDay ? `次日 ${hm(we)}` : hm(we);
}

function hhmmOf(value: unknown): string {
  const text = String(value ?? '');
  if (text.includes('T')) return text.slice(11, 16);
  return text.slice(0, 5);
}

function fmtDateCn(targetDate: string): string {
  const [, mo, d] = targetDate.split('-');
  return `${mo} 月 ${d} 日`;
}

function tomorrowIso(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}
