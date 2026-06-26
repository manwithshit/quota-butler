// 计划记录（plan_id 摘要）+ 校验。移植自 Python plan_tasks.py 的 plan_record / validate。

import { createHash } from 'node:crypto';
import { SUPPORTED_AGENTS, type SchedulePlan } from './planner.js';
import { requestToPayloadShape } from './schedule_flow.js';

export interface PlanEventRecord {
  agent: string;
  kind: string;
  at: string;
  purpose: string;
}

export interface PlanRecord {
  plan_id: string;
  status: string;
  plan_version: number;
  agents: string[];
  work_start: string;
  work_end: string;
  reason: string;
  events: PlanEventRecord[];
  request: Record<string, unknown>;
  adopted_at?: string;
  tasks?: Array<Record<string, unknown>>;
  manual?: boolean; // 手动预热（用户直接设预热点，非自动计划）
}

/** 手动预热记录：用户直接给每个模型各指定一个预热时间（只为明天），不走自动窗口数学。
 *  每个 [agent, HH:mm] 一条 warmup 事件（两个模型可不同时间）；
 *  work_start = 最早预热点，work_end = 最晚预热点 + 10 分钟（开完即结束，不等 5h）。 */
export function manualWarmupRecord(agentTimes: Array<[string, string]>, targetDate: string): PlanRecord {
  const events = agentTimes.map(([agent, hhmm]) => ({
    agent, kind: 'warmup', at: localIso(combineLocal(targetDate, hhmm)), purpose: '手动预热',
  }));
  const ms = agentTimes.map(([, hhmm]) => combineLocal(targetDate, hhmm).getTime());
  const start = Math.min(...ms);
  const end = Math.max(...ms) + 10 * 60000;
  const agents = agentTimes.map(([a]) => a);
  const core = {
    plan_version: 3,
    agents,
    work_start: localIso(new Date(start)),
    work_end: localIso(new Date(end)),
    reason: '手动预热',
    events,
    request: { manual: true, target_date: targetDate, agents: agents.join(',') },
  };
  const digest = createHash('sha256').update(stableStringify(core)).digest('hex').slice(0, 16);
  return { plan_id: digest, status: 'proposed', manual: true, ...core };
}

function combineLocal(targetDate: string, hhmm: string): Date {
  const [y, mo, d] = targetDate.split('-').map(Number) as [number, number, number];
  const [h, m] = hhmm.split(':').map(Number) as [number, number];
  return new Date(y, mo - 1, d, h, m, 0, 0);
}

export function planRecord(plan: SchedulePlan): PlanRecord {
  const core = {
    plan_version: plan.planVersion,
    agents: [...plan.agents],
    work_start: localIso(plan.workStart),
    work_end: localIso(plan.workEnd),
    reason: plan.reason,
    events: plan.events.map((e) => ({
      agent: e.agent,
      kind: e.kind,
      at: localIso(e.at),
      purpose: e.purpose,
    })),
    request: requestToPayloadShape(plan.request),
  };
  const digest = createHash('sha256').update(stableStringify(core)).digest('hex').slice(0, 16);
  return { plan_id: digest, status: 'proposed', ...core };
}

export function validatePlanRecord(value: unknown): PlanRecord {
  if (!value || typeof value !== 'object') throw new Error('计划 payload 缺失');
  const record = value as Record<string, unknown>;
  if (record['plan_version'] !== 3) throw new Error('该计划已失效，请重新规划');
  const planId = String(record['plan_id'] ?? '').trim();
  if (!planId || !/^[a-z0-9-]+$/i.test(planId)) throw new Error('plan_id 非法');
  const events = record['events'];
  if (!Array.isArray(events)) throw new Error('计划 events 缺失');
  const ws = parseDate(record['work_start']);
  const we = parseDate(record['work_end']);
  if (we.getTime() <= ws.getTime()) throw new Error('计划工作结束时间必须晚于开始时间');
  if (events.length > 50) throw new Error('计划事件过多');
  for (const e of events) {
    if (!e || typeof e !== 'object') throw new Error('计划事件格式非法');
    const ev = e as Record<string, unknown>;
    if (!(SUPPORTED_AGENTS as readonly string[]).includes(String(ev['agent']))) {
      throw new Error(`计划包含不支持的 Agent: ${String(ev['agent'])}`);
    }
    if (ev['kind'] !== 'warmup') throw new Error(`计划事件类型非法: ${String(ev['kind'])}`);
    parseDate(ev['at']);
  }
  return record as unknown as PlanRecord;
}

/** 本地 naive ISO（不带时区），让卡片按本地时间显示、且 new Date() 按本地解析回来。 */
function localIso(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function parseDate(v: unknown): Date {
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) throw new Error(`非法计划时间: ${String(v)}`);
  return d;
}

function stableStringify(obj: unknown): string {
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return `[${obj.map(stableStringify).join(',')}]`;
  const keys = Object.keys(obj as Record<string, unknown>).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify((obj as Record<string, unknown>)[k])}`).join(',')}}`;
}
