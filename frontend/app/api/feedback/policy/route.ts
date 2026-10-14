import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch } from "@/lib/admin-api";
import { feedbackScopeMatches } from "@/lib/feedback/server-scope";
export const dynamic = "force-dynamic";
export async function GET(req: NextRequest) {
  const ctx = await getSessionContext();
  if (!ctx) return NextResponse.json({ error: "请先登录" }, { status: 401 });
  if (!feedbackScopeMatches(req.headers, ctx)) return NextResponse.json({ error: "企业上下文已变化" }, { status: 409 });
  const result = await adminFetch("/agent/feedback/policy", { cookie: req.headers.get("cookie") ?? "", tenantContext: {tenantId: ctx.current_tenant?.id ?? "", contextVersion: ctx.context_version} });
  return NextResponse.json(result.json ?? {enabled: false}, {status: result.status});
}
