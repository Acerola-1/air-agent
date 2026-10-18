import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch } from "@/lib/admin-api";

import { feedbackScopeMatches } from "@/lib/feedback/server-scope";

export const dynamic = "force-dynamic";

/** 本人评价提交（同源 BFF → admin PUT /agent/feedback/answers/{answer_id}）。
 *
 * 幂等 operation_id 与 CAS expected_revision 由 admin 服务统一裁决；
 * 409 语义（revision_conflict/event_conflict）原样透传给客户端区分提示。
 */
export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ answerId: string }> }
) {
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
  const { answerId } = await params;
  if (!/^[0-9a-fA-F-]{36}$/.test(answerId)) {
    return NextResponse.json({ error: "answer_id 非法" }, { status: 400 });
  }
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
  }
  const store = req.headers.get("cookie") ?? "";
  const result = await adminFetch(
    `/agent/feedback/answers/${encodeURIComponent(answerId)}`,
    {
      method: "PUT",
      cookie: store,
      tenantContext: {
        tenantId: ctx.current_tenant?.id ?? "",
        contextVersion: ctx.context_version,
      },
      origin: req.headers.get("origin"),
      body,
    }
  );
  return NextResponse.json(result.json ?? {}, { status: result.status });
}
