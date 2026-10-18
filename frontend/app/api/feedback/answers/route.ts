import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch } from "@/lib/admin-api";

import { feedbackScopeMatches } from "@/lib/feedback/server-scope";

export const dynamic = "force-dynamic";

/** 本人反馈回读（同源 BFF → admin /agent/feedback/answers）。
 *
 * 客户端只传 message_ids；服务端按可信会话返回权威 answer 映射与当前评价。
 * 跨企业/未确认对象不出现（不泄露存在性）。
 */
export async function GET(req: NextRequest) {
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
  const ids = req.nextUrl.searchParams.get("ids") ?? "";
  if (!ids || ids.split(",").length > 50) {
    return NextResponse.json({ error: "ids 必须为 1..50 个" }, { status: 400 });
  }
  const store = req.headers.get("cookie") ?? "";
  // 聊天页的映射键统一是 **message_id**（UI 消息 / 台账 answer.message_id 权威键）。
  // UUID 形状无法区分两种 ID（两者都是 UUID），因此按显式 kind 参数走，默认 message。
  const kind = req.nextUrl.searchParams.get("kind") === "answer" ? "answer" : "message";
  const query =
    kind === "answer"
      ? `answer_ids=${encodeURIComponent(ids)}`
      : `message_ids=${encodeURIComponent(ids)}`;
  const threadId = req.nextUrl.searchParams.get("thread_id");
  const scopedQuery = query + (threadId ? `&thread_id=${encodeURIComponent(threadId)}` : "");
  const result = await adminFetch(`/agent/feedback/answers?${scopedQuery}`, {
    cookie: store,
    tenantContext: {
      tenantId: ctx.current_tenant?.id ?? "",
      contextVersion: ctx.context_version,
    },
  });
  return NextResponse.json(result.json ?? { items: [] }, { status: result.status });
}
