import { describe, it, expect } from 'vitest';
import { parsePlanRequest, validateWorkTime } from '../src/schedule_flow.js';

describe('schedule flow', () => {
  it('point mode creates the default 7.5h single-agent window', () => {
    const r = parsePlanRequest({ target_date: '2026-06-23', time_mode: 'point', work_start: '09:00' }, 1);
    expect(r.firstWarmup).toBe('06:30');
    expect(r.secondWarmup).toBe('11:31');
    expect(r.workEnd).toBe('16:31');
  });

  it('default warmup generation rejects plans whose generated warmups cross day boundary', () => {
    expect(validateWorkTime('22:00', '02:00')).toBe(4 * 60);
    expect(() => parsePlanRequest(
      { target_date: '2026-06-23', time_mode: 'range', work_start: '22:00', work_end: '02:00' },
      2,
    )).toThrow('跨天');
  });

  it('still caps total duration at 16 hours (incl. equal start/end = 24h)', () => {
    expect(() => validateWorkTime('22:00', '22:00')).toThrow('16 小时');
    expect(() => validateWorkTime('09:00', '06:00')).toThrow('16 小时'); // 21h 跨天
  });

  it('validates user-adjusted warmup times after sorting by distance only', () => {
    const r = parsePlanRequest({
      target_date: '2026-06-23',
      time_mode: 'point',
      work_start: '09:00',
      first_warmup: '18:30',
      second_warmup: '11:31',
    }, 1);
    expect(r.firstWarmup).toBe('18:30');
    expect(r.secondWarmup).toBe('11:31');
  });

  it('rejects warmup times less than 5 hours apart', () => {
    expect(() => parsePlanRequest({
      target_date: '2026-06-23',
      time_mode: 'point',
      work_start: '09:00',
      first_warmup: '06:30',
      second_warmup: '07:30',
    }, 1)).toThrow('5 小时');
  });
});
