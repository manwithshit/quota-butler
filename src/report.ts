// 预览晚间日报卡：检测本机 Agent + 读现有 state，在终端打印"日报 + 明日计划"卡。
// 只读、不连飞书、不写盘——方便白天随时看一眼日报长什么样。

import { detectAgents } from './agent_status.js';
import { buildBedtimeCard } from './notify.js';
import { StateStore } from './state.js';

export async function report(): Promise<void> {
  console.log('检测本机 Agent…（只读，不发飞书、不写盘）\n');
  const statuses = await detectAgents();
  for (const [provider, s] of Object.entries(statuses)) {
    const detail = s.detail ? `  (${s.detail})` : '';
    console.log(`- ${provider}: ${s.state}${detail}`);
  }
  const st = new StateStore().get();
  const now = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  const today = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
  const card = buildBedtimeCard(statuses, st.lastPlanRequest, {
    eventLog: st.eventLog,
    dayStart: st.dayStartUsage[today],
    activePlan: st.activePlan,
    now,
  });
  const body = card.body as { elements: Array<{ content?: string }> };
  console.log('\n=== 晚间日报卡（终端预览）===\n');
  console.log(
    body.elements
      .map((e) => e.content ?? '')
      .filter(Boolean)
      .join('\n'),
  );
  if (!st.dayStartUsage[today]) {
    console.log('\n（提示：今天还没有"日初快照"，"消耗"一节要 daemon 跑过当天才有数据。）');
  }
}
