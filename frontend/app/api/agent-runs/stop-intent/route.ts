import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { recordStopIntent } from "@/lib/agent-ledger";

export const dynamic = "force-dynamic";

/** 停止意图记录（同源 BFF）：把用户停止/导航/断连/企业切换写成**独立事实**。
 *
 * 该事实不替代主状态：用户停止 ≠ 平台已取消。运行标识可缺失（停止先于 run_id），
 * 服务端按 attempt/thread 关联并在 run_id 确认后继续追踪。
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
  if (ctx.membership?.status !== "active" || ctx.current_tenant?.status !== "active") {
    return NextResponse.json({ error: "当前企业成员身份无效" }, { status: 403 });
  }
  let body: {
    thread_id?: string | null;
    attempt_id?: string | null;
    platform_run_id?: string | null;
    origin?: string;
    phase?: string;
  };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
  }
  const origin = String(body.origin ?? "unknown");
  const phase = String(body.phase ?? "unknown");
  const store = req.headers.get("cookie") ?? "";
  try {
    const result = await recordStopIntent({
      cookie: store,
      threadId: body.thread_id ?? null,
      attemptId: body.attempt_id ?? null,
      platformRunId: body.platform_run_id ?? null,
      origin: origin as Parameters<typeof recordStopIntent>[0]["origin"],
      phase: phase as Parameters<typeof recordStopIntent>[0]["phase"],
      tenantId: ctx.current_tenant?.id ?? "",
      contextVersion: ctx.context_version,
      requestOrigin: req.headers.get("origin"),
    });
    return NextResponse.json({ recorded: true, ...result });
  } catch {
    // 停止意图记录失败不阻断本地停止体验；不冒充已取消。
    return NextResponse.json({ recorded: false });
  }
}
