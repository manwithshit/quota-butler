import type { Provider } from './base.js';
import { ClaudeProvider } from './claude.js';
import { CodexProvider } from './codex.js';

const PROVIDERS: Record<string, Provider> = {
  cc: new ClaudeProvider(),
  codex: new CodexProvider(),
};

export function getProvider(name: string): Provider {
  const p = PROVIDERS[name];
  if (!p) throw new Error(`unknown provider: ${name}`);
  return p;
}

export type { Provider, ProviderTier, ReadUsageOptions, Usage, WindowUsage } from './base.js';
export { ProviderError, usageTier } from './base.js';
