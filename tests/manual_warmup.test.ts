import { describe, it, expect } from 'vitest';
import { manualWarmupRecord, validatePlanRecord } from '../src/plan_record.js';

describe('manualWarmupRecord', () => {
  it('two models at different times → two windows; work_start=earliest, work_end=latest+10min', () => {
    const rec = manualWarmupRecord([['cc', '07:00'], ['codex', '08:30']], '2026-06-24');
    expect(rec.plan_version).toBe(3);
    expect(rec.manual).toBe(true);
    expect(rec.status).toBe('proposed');
    expect(rec.agents).toEqual(['cc', 'codex']);
    expect(rec.reason).toBe('手动预热');
    expect(rec.work_start).toBe('2026-06-24T07:00:00'); // 最早
    expect(rec.work_end).toBe('2026-06-24T08:40:00'); // 最晚 + 10min
    expect(rec.events.map((e) => [e.agent, e.at])).toEqual([
      ['cc', '2026-06-24T07:00:00'],
      ['codex', '2026-06-24T08:30:00'],
    ]);
    expect(rec.plan_id).toMatch(/^[a-f0-9]{16}$/);
  });

  it('single agent → single event, work_end = +10min', () => {
    const rec = manualWarmupRecord([['cc', '23:30']], '2026-06-24');
    expect(rec.events).toHaveLength(1);
    expect(rec.work_start).toBe('2026-06-24T23:30:00');
    expect(rec.work_end).toBe('2026-06-24T23:40:00');
  });

  it('produces a record that passes validatePlanRecord (adoptable)', () => {
    const rec = manualWarmupRecord([['cc', '07:00']], '2026-06-24');
    expect(() => validatePlanRecord(rec)).not.toThrow();
  });
});
