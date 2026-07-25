// 进程内预热互斥：同一 provider 同时只允许一次 warmup 在跑。
// 覆盖：手动预热卡双击、恢复卡与手动卡并发、定时预热与手动预热撞车。
// 单进程内存标志即可（run_lock 已保证单实例）；进程崩溃自动释放，无需持久化。

const inFlight = new Set<string>();

/** 尝试开始一次预热；false = 该 provider 已有预热在跑，调用方应放弃并告知用户。 */
export function tryBeginWarmup(provider: string): boolean {
  if (inFlight.has(provider)) return false;
  inFlight.add(provider);
  return true;
}

export function endWarmup(provider: string): void {
  inFlight.delete(provider);
}

/** 测试用：清空所有在跑标记。 */
export function resetWarmupLocks(): void {
  inFlight.clear();
}
