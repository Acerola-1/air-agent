/** 已撤回功能留下的本地键清理；幂等、失败不阻塞页面. */

const LEGACY_KEYS = ["air-agent-chat-mode"];
const LEGACY_PREFIXES = ["air-agent-artifacts:"];

export function purgeLegacyLocalStorage(): void {
  if (typeof window === "undefined" || typeof localStorage === "undefined") return;
  try {
    for (const key of LEGACY_KEYS) localStorage.removeItem(key);
    const stale: string[] = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key && LEGACY_PREFIXES.some((prefix) => key.startsWith(prefix))) {
        stale.push(key);
      }
    }
    for (const key of stale) localStorage.removeItem(key);
  } catch {
    // 隐私模式或存储异常时清理失败；保留当前页面可用性。
  }
}
