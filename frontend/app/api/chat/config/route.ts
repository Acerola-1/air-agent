import { type NextRequest, NextResponse } from "next/server";
import { AdminApiError, runtimeTicketViaAdmin } from "@/lib/admin-api";
import { getSessionContext } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** BFF 聊天配置代理：仅暴露当前图生效的输入长度与计数方式。
 * 身份、企业上下文与票据由服务端核对，浏览器不能传入自己的上限。 */
export async function GET(req: NextRequest) {
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch (error) {
    const status = typeof (error as { status?: unknown })?.status === "number" ? (error as { status: number }).status : 503;
    return NextResponse.json({ error: "身份服务暂不可用，请稍后重试" }, { status });
  }
  if (!ctx) {
    return NextResponse.json({ error: "请先登录" }, { status: 401 });
  }
  if (ctx.needs_tenant_selection) {
    return NextResponse.json({ error: "请先选择当前企业后再使用对话功能" }, { status: 403 });
  }
  if (ctx.membership?.status !== "active" || ctx.current_tenant?.status !== "active") {
    return NextResponse.json({ error: "当前企业成员身份无效或企业已停用" }, { status: 403 });
  }

  const base = process.env.LANGGRAPH_API_URL;
  if (!base) return NextResponse.json({ error: "对话服务地址未配置" }, { status: 503 });

  const claimedTenant = req.headers.get("x-tenant-id");
  const claimedVersion = req.headers.get("x-tenant-context-version");
  if (!claimedTenant || !claimedVersion) {
    return NextResponse.json(
      { error: "页面企业上下文缺失，请刷新后重试", code: "context_missing" },
      { status: 403 }
    );
  }
  if (claimedTenant !== (ctx.current_tenant?.id ?? "") || Number(claimedVersion) !== ctx.context_version) {
    return NextResponse.json(
      { error: "企业上下文已变更（可能在其他标签页切换），请刷新后重试", code: "context_changed" },
      { status: 409 }
    );
  }

  const store = req.headers.get("cookie") ?? "";
  let ticket: string;
  try {
    const issued = await runtimeTicketViaAdmin(store, claimedTenant, Number(claimedVersion));
    if (issued.status === 409) {
      return NextResponse.json(
        { error: "企业上下文已变更（签发时已切换），请刷新后重试", code: "context_changed" },
        { status: 409 }
      );
    }
    if (issued.status === 403 || !issued.ticket) {
      return NextResponse.json(
        { error: issued.status === 403 ? "当前企业成员身份无效或企业已停用" : "对话凭据不可用" },
        { status: issued.status === 403 ? 403 : 503 }
      );
    }
    ticket = issued.ticket;
  } catch (err) {
    const status = err instanceof AdminApiError ? err.status : 503;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "对话凭据获取失败" },
      { status: status === 401 ? 503 : status === 409 ? 409 : 503 }
    );
  }

  const searchParams = req.nextUrl.searchParams.toString();
  const upstreamUrl = `${base.replace(/\/+$/, "")}/chat/config${searchParams ? `?${searchParams}` : ""}`;

  try {
    const res = await fetch(upstreamUrl, {
      method: "GET",
      headers: { authorization: `Bearer ${ticket}`, "content-type": "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(6000),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, {
      status: res.status,
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { error: "聊天配置获取超时或服务不可用" },
      { status: 503 }
    );
  }
}
