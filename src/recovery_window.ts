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
  const resetAt = lastKnownFiveHourReset(state, provider);
  if (!resetAt) return null;
  const age = now.getTime() - resetAt.getTime();
  if (age < 0 || age > WARMED_WINDOW_FRESHNESS_MS) return null;
  const windowKey = recoveryWindowKey(provider, 'fiveHour', resetAt);
  state.lastWarmedWindows = { ...(state.lastWarmedWindows ?? {}), [provider]: windowKey };
  removePendingRecoveryForWarmedWindow(state, provider, resetAt);
  return windowKey;
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
