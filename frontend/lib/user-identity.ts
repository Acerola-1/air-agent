/** 浏览器用户身份标识：localStorage 持久 UUID.
 *
 *  设计动机:
 *   - LangGraph 线程按 graph_id 过滤时，同一浏览器不同"会话/重启"之间
 *     不区分用户，历史线程会互相串。这里为每个浏览器生成一个持久 UUID
 *     （存 localStorage），线程创建/列表都带上它，实现"按浏览器隔离历史"。
 *   - 为什么不用纯 UA 指纹：UA 随浏览器升级会变化，会导致用户身份漂移、
 *     历史"丢失"；localStorage UUID 是稳定的持久标识（隐私模式/手动清
 *     缓存会重置，属于该类方案的通用权衡）。
 *
 *  用法: getUserIdentity() 在浏览器端返回稳定 UUID，SSR 阶段返回 undefined
 *  （避免服务端生成随机值导致 hydration mismatch）。
 */
const STORAGE_KEY = "air_agent_user_id";

function generateUuidV7(): string {
  const nowMs = Date.now();
  const rand = new Uint8Array(10);
  globalThis.crypto.getRandomValues(rand);

  const ts = BigInt(nowMs) & ((1n << 48n) - 1n);
  const bytes = new Uint8Array(16);
  bytes[0] = Number((ts >> 40n) & 0xffn);
  bytes[1] = Number((ts >> 32n) & 0xffn);
  bytes[2] = Number((ts >> 24n) & 0xffn);
  bytes[3] = Number((ts >> 16n) & 0xffn);
  bytes[4] = Number((ts >> 8n) & 0xffn);
  bytes[5] = Number(ts & 0xffn);
  bytes[6] = (rand[0] & 0x0f) | 0x70;
  bytes[7] = rand[1];
  bytes[8] = (rand[2] & 0x3f) | 0x80;
  bytes[9] = rand[3];
  bytes[10] = rand[4];
  bytes[11] = rand[5];
  bytes[12] = rand[6];
  bytes[13] = rand[7];
  bytes[14] = rand[8];
  bytes[15] = rand[9];

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return (
    hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) +
    "-" + hex.slice(16, 20) + "-" + hex.slice(20, 32)
  );
}

/** 返回当前浏览器用户的稳定标识；仅客户端可用，SSR 返回 undefined. */
export function getUserIdentity(): string | undefined {
  if (typeof window === "undefined" || typeof localStorage === "undefined") {
    return undefined;
  }
  try {
    const existing = localStorage.getItem(STORAGE_KEY);
    if (existing && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(existing)) {
      return existing;
    }
    const id = generateUuidV7();
    localStorage.setItem(STORAGE_KEY, id);
    return id;
  } catch {
    // localStorage 不可用（隐私模式等）时退回无用户隔离
    return undefined;
  }
}
