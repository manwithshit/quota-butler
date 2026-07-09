import type { QuotaWindowName, State } from './state.js';

export const WINDOW_MATCH_TOLERANCE_MS = 90_000;
const WARMED_WINDOW_FRESHNESS_MS = 4 * 3600000;

export function recoveryWindowKey(provider: string, window: QuotaWindowName, resetAt: Date): string {
  return `${provider}:${window}:${resetAt.toISOString()}`;
}

export function sameRecoveryWindowKey(
  windowKey: string | undefined,
  provider: string,
  window: QuotaWindowName,
  resetAt: Date,
): boolean {
  if (!windowKey) return false;
  if (windowKey === recoveryWindowKey(provider, window, resetAt)) return true;
  const prefix = `${provider}:${window}:`;
  const iso = windowKey.startsWith(prefix) ? windowKey.slice(prefix.length) : '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return Math.abs(t - resetAt.getTime()) <= WINDOW_MATCH_TOLERANCE_MS;
}

export function sameLegacyRecoveryWindowKey(
  windowKey: string | undefined,
  provider: string,
  resetAt: Date,
): boolean {
  if (!windowKey) return false;
  if (windowKey === `${provider}:${resetAt.toISOString()}`) return true;
  const iso = windowKey.startsWith(`${provider}:`) ? windowKey.slice(provider.length + 1) : '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return Math.abs(t - resetAt.getTime()) <= WINDOW_MATCH_TOLERANCE_MS;
}

export function markFiveHourWindowWarmed(state: State, provider: string, now = new Date()): string | null {
  const pendingWindowKey = firstPendingFiveHourWindowKey(state, provider, now);
  if (pendingWindowKey) {
    markKnownRecoveryWindowWarmed(state, provider, pendingWindowKey);
    return pendingWindowKey;
  }

  const resetAt = lastKnownFiveHourReset(state, provider);
  if (!resetAt) return null;
  const age = now.getTime() - resetAt.getTime();
  if (age < 0 || age > WARMED_WINDOW_FRESHNESS_MS) return null;
  const windowKey = recoveryWindowKey(provider, 'fiveHour', resetAt);
  markKnownRecoveryWindowWarmed(state, provider, windowKey);
  return windowKey;
}

export function markKnownRecoveryWindowWarmed(state: State, provider: string, windowKey: string): void {
  state.lastWarmedWindows = { ...(state.lastWarmedWindows ?? {}), [provider]: windowKey };
  const parsed = parseWindowKey(provider, windowKey);
  if (parsed?.window === 'fiveHour') removePendingRecoveryForWarmedWindow(state, provider, parsed.resetAt);
}

export function warmedFiveHourWindowMatches(state: State, provider: string, resetAt: Date): boolean {
  const warmedKey = state.lastWarmedWindows?.[provider];
  return (
    sameRecoveryWindowKey(warmedKey, provider, 'fiveHour', resetAt) ||
    sameLegacyRecoveryWindowKey(warmedKey, provider, resetAt)
  );
}

function removePendingRecoveryForWarmedWindow(state: State, provider: string, resetAt: Date): void {
  state.pendingNotifications = (state.pendingNotifications ?? []).filter((item) => {
    if (item.provider !== provider) return true;
    const window = item.window ?? windowFromKey(item.windowKey);
    if (window !== 'fiveHour') return true;
    return !(
      sameRecoveryWindowKey(item.windowKey, provider, 'fiveHour', resetAt) ||
      sameLegacyRecoveryWindowKey(item.windowKey, provider, resetAt)
    );
  });
  const pending = state.pendingRecovery as
    | { provider?: string; windowKey?: string; window?: QuotaWindowName }
    | null;
  if (!pending || pending.provider !== provider || !pending.windowKey) return;
  const window = pending.window ?? windowFromKey(pending.windowKey);
  if (
    window === 'fiveHour' &&
    (
      sameRecoveryWindowKey(pending.windowKey, provider, 'fiveHour', resetAt) ||
      sameLegacyRecoveryWindowKey(pending.windowKey, provider, resetAt)
    )
  ) {
    state.pendingRecovery = null;
  }
}

function firstPendingFiveHourWindowKey(state: State, provider: string, now: Date): string | null {
  const candidates = state.pendingNotifications ?? [];
  for (const item of candidates) {
    if (item.provider !== provider) continue;
    const parsed = parseWindowKey(provider, item.windowKey);
    if (!parsed || parsed.window !== 'fiveHour') continue;
    const age = now.getTime() - parsed.resetAt.getTime();
    if (age >= 0 && age <= WARMED_WINDOW_FRESHNESS_MS) return item.windowKey;
  }
  const pending = state.pendingRecovery as
    | { provider?: string; windowKey?: string; window?: QuotaWindowName }
    | null;
  if (pending?.provider !== provider || !pending.windowKey) return null;
  const parsed = parseWindowKey(provider, pending.windowKey);
  if (!parsed || parsed.window !== 'fiveHour') return null;
  const age = now.getTime() - parsed.resetAt.getTime();
  return age >= 0 && age <= WARMED_WINDOW_FRESHNESS_MS ? pending.windowKey : null;
}

function parseWindowKey(provider: string, windowKey: string): { window: QuotaWindowName; resetAt: Date } | null {
  const modernPrefix = `${provider}:`;
  if (!windowKey.startsWith(modernPrefix)) return null;
  const rest = windowKey.slice(modernPrefix.length);
  const firstColon = rest.indexOf(':');
  let window: QuotaWindowName = 'fiveHour';
  let iso = rest;
  if (firstColon > 0) {
    const rawWindow = rest.slice(0, firstColon);
    if (rawWindow === 'fiveHour' || rawWindow === 'sevenDay') {
      window = rawWindow;
      iso = rest.slice(firstColon + 1);
    }
  }
  const resetAt = new Date(iso);
  if (Number.isNaN(resetAt.getTime())) return null;
  return { window, resetAt };
}

function lastKnownFiveHourReset(state: State, provider: string): Date | null {
  const resetText =
    state.providerWindowSnapshots?.[provider]?.fiveHour?.resetAt ??
    state.providerSnapshots?.[provider]?.resetAt ??
    state.usageSnapshots?.[provider]?.fiveHourResetAt ??
    null;
  if (!resetText) return null;
  const resetAt = new Date(resetText);
  return Number.isNaN(resetAt.getTime()) ? null : resetAt;
}

function windowFromKey(windowKey: string): QuotaWindowName {
  const parts = windowKey.split(':');
  const maybe = parts[1];
  return maybe === 'sevenDay' || maybe === 'monthly' ? maybe : 'fiveHour';
}
