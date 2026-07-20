import { describe, expect, it } from 'vitest';
import { weeklyActivationForRange, weeklyCycleState } from '../src/weekly_activation.js';

describe('Codex weekly activation planning', () => {
  const start = new Date('2026-07-21T09:00:00');
  const end = new Date('2026-07-21T18:00:00');

  it('does not append while the current weekly cycle covers the whole plan', () => {
    expect(weeklyActivationForRange(new Date('2026-07-26T20:56:00'), start, end)).toBeNull();
  });

  it('activates 90 seconds after a reset inside the work range', () => {
    const option = weeklyActivationForRange(new Date('2026-07-21T13:00:00'), start, end);
    expect(option?.at).toEqual(new Date('2026-07-21T13:01:30'));
  });

  it('activates ten minutes before work when reset already passed', () => {
    const option = weeklyActivationForRange(new Date('2026-07-21T02:00:00'), start, end);
    expect(option?.at).toEqual(new Date('2026-07-21T08:50:00'));
  });

  it('supports cross-midnight plans with absolute dates', () => {
    const option = weeklyActivationForRange(
      new Date('2026-07-22T00:30:00'),
      new Date('2026-07-21T22:00:00'),
      new Date('2026-07-22T02:00:00'),
    );
    expect(option?.at).toEqual(new Date('2026-07-22T00:31:30'));
  });

  it('does not append without a reliable reset or when reset equals plan end', () => {
    expect(weeklyActivationForRange(null, start, end)).toBeNull();
    expect(weeklyActivationForRange(end, start, end)).toBeNull();
  });

  it('distinguishes active, exhausted, and ready weekly states', () => {
    const now = new Date('2026-07-20T12:00:00');
    expect(weeklyCycleState({ utilization: 63, resetsAt: new Date('2026-07-26T20:56:00') }, now)).toBe('active');
    expect(weeklyCycleState({ utilization: 100, resetsAt: new Date('2026-07-26T20:56:00') }, now)).toBe('exhausted');
    expect(weeklyCycleState({ utilization: 100, resetsAt: new Date('2026-07-20T11:59:00') }, now)).toBe('ready');
  });
});
