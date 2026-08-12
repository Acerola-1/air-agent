/** 生成 UUID v7 — 基于时间戳的 UUID，同时是合法的 LangGraph thread_id
 *
 *  设计动机:
 *   - assistant-ui (RemoteThreadListRuntime) 创建新会话时, 默认在客户端生成
 *     临时的 __LOCALID_xxx 占位 ID, 等 create 返回真实 UUID 再替换.
 *     这个"替换时序"和 react-langchain glue 层抢跑的 /thread/:id/state
 *     叠加时会出现 422 "Invalid thread ID: must be a UUID".
 *   - 解法: 我们不依赖它的 local ID, 直接在"调用 create 同步阶段"就生成一个
 *     **后端能接受的合法 UUID v7** → 返回给 runtime 的 {remoteId: uuid},
 *     从流程第一刻就没有 __LOCALID_, 也就没有替换时序问题, 422 自然消失.
 *
 *  为什么选 UUID v7:
 *   - 前缀基于 Unix 毫秒时间戳, 后续 /threads/search 按 created_at 排序和
 *     按 thread_id 排序顺序相同, 对列表排序/分页友好 (v4 是完全随机).
 *   - LangGraph API 本身的 thread_id 校验就是标准 UUID 正则, v4/v7 都过.
 *
 *  实现: 无第三方依赖, 用 WebCrypto crypto.getRandomValues + 时间戳拼接.
 *  Node (Next SSR) 和浏览器端都自带 globalThis.crypto, 可直接运行.
 */
export function generateUuidV7(): string {
  const nowMs = Date.now();

  // 16 字节随机数据 —— UUID 是 128bit = 16 octets
  const rand = new Uint8Array(10);
  globalThis.crypto.getRandomValues(rand);

  // 48 bit = 6 bytes 毫秒时间戳 (高位在前 big-endian)
  // timestamp_ms = nowMs & ((1 << 48) - 1)
  const ts = BigInt(nowMs) & ((1n << 48n) - 1n);

  const bytes = new Uint8Array(16);
  bytes[0] = Number((ts >> 40n) & 0xffn);
  bytes[1] = Number((ts >> 32n) & 0xffn);
  bytes[2] = Number((ts >> 24n) & 0xffn);
  bytes[3] = Number((ts >> 16n) & 0xffn);
  bytes[4] = Number((ts >> 8n) & 0xffn);
  bytes[5] = Number(ts & 0xffn);

  // bytes[6] 的高 4 bit = version = 0111 (v7)
  bytes[6] = (rand[0] & 0x0f) | 0x70;
  bytes[7] = rand[1];

  // bytes[8] 的高 2 bit = variant = 10 (RFC 4122)
  bytes[8] = (rand[2] & 0x3f) | 0x80;
  bytes[9] = rand[3];
  bytes[10] = rand[4];
  bytes[11] = rand[5];
  bytes[12] = rand[6];
  bytes[13] = rand[7];
  bytes[14] = rand[8];
  bytes[15] = rand[9];

  // 格式化为 8-4-4-4-12 十六进制
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return (
    hex.slice(0, 8) +
    "-" +
    hex.slice(8, 12) +
    "-" +
    hex.slice(12, 16) +
    "-" +
    hex.slice(16, 20) +
    "-" +
    hex.slice(20, 32)
  );
}

/** 快速校验一个字符串是否为合法 UUID (v4 / v7 任意 version 都过) */
export function isValidUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
