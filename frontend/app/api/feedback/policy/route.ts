import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch } from "@/lib/admin-api";
import { feedbackScopeMatches } from "@/lib/feedback/server-scope";
export const dynamic = "force-dynamic";
export async function GET(req: NextRequest) {
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch (error) {
    const status = typeof (error as { status?: unknown })?.status === "number" ? (error as { status: number }).status : 503;
    return NextResponse.json({ error: "身份服务暂不可用，请稍后重试" }, { status });
  }
  if (!ctx) return NextResponse.json({ error: "请先登录" }, { status: 401 });
  if (!feedbackScopeMatches(req.headers, ctx)) return NextResponse.json({ error: "企业上下文已变化" }, { status: 409 });
  const result = await adminFetch("/agent/feedback/policy", { cookie: req.headers.get("cookie") ?? "", tenantContext: {tenantId: ctx.current_tenant?.id ?? "", contextVersion: ctx.context_version} });
  return NextResponse.json(result.json ?? {enabled: false}, {status: result.status});
}
