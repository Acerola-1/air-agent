import { type NextRequest, NextResponse } from "next/server";
import { AdminApiError, runtimeTicketViaAdmin } from "@/lib/admin-api";
import { getSessionContext } from "@/lib/auth";

export const dynamic = "force-dynamic";

function jsonNoStore(body: unknown, init: ResponseInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("Cache-Control", "no-store");
  headers.set("X-Content-Type-Options", "nosniff");
  return NextResponse.json(body, { ...init, headers });
}


/** 知识引用原文同源代理：只接受 citation_id + text/pdf，不转发浏览器提供的 URL/record id。
 *
 * 服务端用当前 cookie 会话换取 60s 运行票据，再调用 Agent 自定义受保护入口；
 * 原文由 Agent 在服务端用新的单操作委托从知识服务读取，浏览器看不到 token。
 */
export async function GET(
  req: NextRequest,
  context: { params: Promise<{ citationId: string; kind: string }> }
) {
  const { citationId, kind } = await context.params;
  if (!/^[0-9a-fA-F-]{36}$/.test(citationId) || !["text", "pdf"].includes(kind)) {
    return jsonNoStore({ error: "引用不存在" }, { status: 404 });
  }
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch {
    return jsonNoStore({ error: "身份服务暂不可用" }, { status: 503 });
  }
  if (!ctx) return jsonNoStore({ error: "请先登录" }, { status: 401 });
  if (ctx.needs_tenant_selection) {
    return jsonNoStore({ error: "请先选择当前企业" }, { status: 403 });
  }
  const tenantId = ctx.current_tenant?.id ?? "";
  if (!tenantId || ctx.membership?.status !== "active") {
    return jsonNoStore({ error: "当前企业成员身份无效" }, { status: 403 });
  }
  const base = process.env.LANGGRAPH_API_URL;
  if (!base) return jsonNoStore({ error: "对话服务地址未配置" }, { status: 503 });

  const cookie = req.headers.get("cookie") ?? "";
  let ticket: string;
  try {
    const issued = await runtimeTicketViaAdmin(
      cookie,
      tenantId,
      ctx.context_version
    );
    if (!issued.ticket || issued.status !== 200) {
      return jsonNoStore({ error: "对话凭据不可用" }, { status: 503 });
    }
    ticket = issued.ticket;
  } catch (error) {
    const status = error instanceof AdminApiError ? error.status : 503;
    return jsonNoStore(
      { error: error instanceof Error ? error.message : "对话凭据获取失败" },
      { status: status === 409 ? 409 : 503 }
    );
  }

  const params = req.nextUrl.searchParams;
  const upstream = new URL(
    `/knowledge/citations/${citationId}/${kind}`,
    base
  );
  upstream.searchParams.set("offset", params.get("offset") ?? "0");
  upstream.searchParams.set("limit", params.get("limit") ?? "8000");

  const headers: Record<string, string> = {
    authorization: `Bearer ${ticket}`,
  };
  const range = req.headers.get("range");
  if (range) headers.range = range;
  let response: globalThis.Response;
  try {
    response = await fetch(upstream, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(22_000)]),
    });
  } catch {
    return jsonNoStore({ error: "原文暂不可用" }, { status: 503 });
  }
  const pass = new Headers();
  for (const name of [
    "content-type",
    "content-disposition",
    "content-security-policy",
    "content-range",
    "accept-ranges",
    "x-content-offset",
    "x-content-next-offset",
    "x-content-total-chars",
  ]) {
    const value = response.headers.get(name);
    if (value) pass.set(name, value);
  }
  pass.set("Cache-Control", "no-store");
  pass.set("X-Content-Type-Options", "nosniff");
  if (response.status !== 200 && response.status !== 206) {
    void response.body?.cancel().catch(() => {});
    const messages: Record<number, string> = {
      401: "登录状态已失效，请重新登录。",
      403: "当前会话无权读取该来源。",
      404: "找不到该引用或当前会话不可访问，请重新检索。",
      409: "企业或会话上下文已变化，请刷新后重新检索。",
      410: "来源已失效或权限已变化，请重新检索。",
      416: "文件读取范围无效，请重新打开。",
      503: "原文服务暂不可用，请稍后重试。",
      504: "原文读取超时，请重试。",
    };
    return jsonNoStore(
      { error: messages[response.status] ?? "原文读取异常，请重试。" },
      { status: response.status, headers: { "Cache-Control": "no-store" } }
    );
  }
  return new NextResponse(response.body, { status: response.status, headers: pass });
}
