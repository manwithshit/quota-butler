// 离线自检：检测本机 Agent 并在终端预览当前额度卡，不连飞书。

import { detectAgents } from './agent_status.js';
import { buildStatusCard } from './notify.js';
import { StateStore } from './state.js';

export async function selftest(): Promise<void> {
  console.log('检测本机 Agent…\n');
  const statuses = await detectAgents();
  for (const [provider, s] of Object.entries(statuses)) {
    const detail = s.detail ? `  (${s.detail})` : '';
    console.log(`- ${provider}: ${s.state}${detail}`);
  }
  const state = new StateStore();
  for (const [provider, s] of Object.entries(statuses)) {
    if (s.usage) state.recordUsageSnapshot(provider, s.usage);
  }
  const cardJson = buildStatusCard(statuses, state.get().usageSnapshots);
  const body = cardJson.body as { elements: Array<{ content?: string }> };
  console.log('\n=== 当前额度卡（终端预览）===\n');
  console.log(body.elements[0]?.content ?? '');
}
