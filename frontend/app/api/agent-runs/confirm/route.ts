import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch, runtimeTicketViaAdmin } from "@/lib/admin-api";
import { confirmAdmission } from "@/lib/agent-ledger";

export const dynamic = "force-dynamic";

/** 台账受理结果确认（流式运行：客户端在首个 metadata 事件后上报 run_id）。
 *
 * 不轻信客户端：
 *  1. 服务端向 admin 查询该线程上**本人尚未确认映射**的尝试候选；
 *  2. 逐个与平台 run 的 metadata 比对（`ledger_request_id`/`ledger_attempt_id`）；
 *  3. 只有匹配的那一个才确认。
 * 客户端伪造 run_id 或错配一律返回 confirmed=false（保持"受理中"，交校准处理）。
 */
export async function POST(req: NextRequest) {
  const ctx = await getSessionContext();
  if (!ctx) return NextResponse.json({ error: "请先登录" }, { status: 401 });
  if (ctx.membership?.status !== "active" || ctx.current_tenant?.status !== "active") {
    return NextResponse.json({ error: "当前企业成员身份无效" }, { status: 403 });
  }
  let body: { thread_id?: string; run_id?: string };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
  }
  const threadId = String(body.thread_id ?? "");
  const runId = String(body.run_id ?? "");
  if (!/^[a-zA-Z0-9_-]+$/.test(threadId) || !/^[a-zA-Z0-9_-]+$/.test(runId)) {
    return NextResponse.json({ error: "关联参数不完整" }, { status: 400 });
  }
  const base = process.env.LANGGRAPH_API_URL;
  if (!base) return NextResponse.json({ error: "对话服务地址未配置" }, { status: 503 });

  const store = req.headers.get("cookie") ?? "";
  const candidates = await adminFetch(
    `/agent/runs/pending-attempts?thread_id=${encodeURIComponent(threadId)}`,
    { cookie: store }
  );
  if (candidates.status !== 200) {
    return NextResponse.json({ confirmed: false, reason: "candidates_unavailable" });
  }
  const items = ((candidates.json as { items?: unknown[] })?.items ?? []) as {
    request_id: string;
    attempt_id: string;
  }[];
  if (items.length === 0) {
    return NextResponse.json({ confirmed: false, reason: "no_pending_attempt" });
  }

  let ticket = "";
  try {
    const issued = await runtimeTicketViaAdmin(
      store,
      ctx.current_tenant?.id ?? "",
      ctx.context_version
    );
    ticket = issued.ticket ?? "";
  } catch {
    ticket = "";
  }
  if (!ticket) return NextResponse.json({ error: "对话凭据不可用" }, { status: 503 });

  let runMetadata: Record<string, unknown> = {};
  try {
    const res = await fetch(
      `${base}/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}`,
      { headers: { authorization: `Bearer ${ticket}` }, cache: "no-store" }
    );
    if (!res.ok) {
      return NextResponse.json({ confirmed: false, reason: "run_unreadable" });
    }
    runMetadata =
      ((await res.json()) as { metadata?: Record<string, unknown> }).metadata ?? {};
  } catch {
    return NextResponse.json({ confirmed: false, reason: "unavailable" });
  }

  const matched = items.find(
    (item) =>
      runMetadata.ledger_request_id === item.request_id &&
      runMetadata.ledger_attempt_id === item.attempt_id
  );
  if (!matched) {
    return NextResponse.json({ confirmed: false, reason: "marker_mismatch" });
  }
  try {
    await confirmAdmission({
      cookie: store,
      requestId: matched.request_id,
      attemptId: matched.attempt_id,
      outcome: "confirmed",
      platformRunId: runId,
    });
  } catch {
    return NextResponse.json({ confirmed: false, reason: "confirm_failed" });
  }
  return NextResponse.json({ confirmed: true });
}
