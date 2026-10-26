import { getTenantRequestContext } from "@/lib/langgraph-api";

export type ChatConfig = {
  graph_id: string;
  max_input_chars: number;
  character_count_mode: string;
};

/** 读取服务端权威单次输入限制；浏览器不维护另一份上限常量。 */
export async function fetchChatConfig(graphId: string, signal?: AbortSignal): Promise<ChatConfig> {
  const tenant = getTenantRequestContext();
  const headers: Record<string, string> = {};
  if (tenant) {
    headers["x-tenant-id"] = tenant.tenantId;
    headers["x-tenant-context-version"] = String(tenant.contextVersion);
  }
  const res = await fetch(
    `/api/chat/config?graph_id=${encodeURIComponent(graphId)}`,
    {
      cache: "no-store", headers,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(8000)]) : AbortSignal.timeout(8000),
    }
  );
  const data = (await res.json().catch(() => ({}))) as Partial<ChatConfig> & {
    error?: string;
  };
  if (!res.ok) {
    const error = new Error(data.error || `聊天配置加载失败 (${res.status})`) as Error & { status?: number };
    error.status = res.status;
    throw error;
  }
  if (
    !Number.isInteger(data.max_input_chars) ||
    (data.max_input_chars as number) <= 0 ||
    data.character_count_mode !== "unicode_code_point" ||
    data.graph_id !== graphId
  ) {
    throw new Error("聊天配置响应无效");
  }
  return data as ChatConfig;
}

/** Unicode 码点计数：与后端 Python len 对齐，不按 UTF-16 代码单元. */
export function countUnicodeCodePoints(text: string): number {
  return Array.from(text).length;
}
