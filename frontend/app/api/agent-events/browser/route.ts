import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch } from "@/lib/admin-api";

import { feedbackScopeMatches } from "@/lib/feedback/server-scope";

export const dynamic = "force-dynamic";

/** 浏览器事件批次投递（同源 BFF → admin /agent/events/browser）。
 *
 * 服务端覆盖身份/来源；逐项返回结果。401/403 表示授权失效（客户端清队列）。
 */
export async function POST(req: NextRequest) {
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch (error) {
    const status = typeof (error as { status?: unknown })?.status === "number" ? (error as { status: number }).status : 503;
    return NextResponse.json({ error: "身份服务暂不可用，请稍后重试" }, { status });
  }
  if (!ctx) return NextResponse.json({ error: "请先登录" }, { status: 401 });
  if (!feedbackScopeMatches(req.headers, ctx)) return NextResponse.json({error: "企业上下文已变化，请刷新", code: "context_changed"}, {status: 409});
  if (ctx.membership?.status !== "active" || ctx.current_tenant?.status !== "active") {
    return NextResponse.json({ error: "当前企业成员身份无效" }, { status: 403 });
  }
  let body: { events?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
  }
  if (!Array.isArray(body.events) || body.events.length === 0 || body.events.length > 50) {
    return NextResponse.json({ error: "events 必须为 1..50 条" }, { status: 400 });
  }
  const store = req.headers.get("cookie") ?? "";
  const result = await adminFetch("/agent/events/browser", {
    method: "POST",
    cookie: store,
    tenantContext: {
      tenantId: ctx.current_tenant?.id ?? "",
      contextVersion: ctx.context_version,
    },
    origin: req.headers.get("origin"),
    body,
  });
  return NextResponse.json(result.json ?? {}, { status: result.status });
}
