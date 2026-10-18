import { NextRequest, NextResponse } from "next/server";

/** 管理前端拥有独立应用会话；不把同主机聊天 cookie 传给业务服务。 */
async function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname.slice(4);
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.set("x-agent-frontend", "admin");
  if (process.env.AGENT_IDENTITY_BACKEND === "remote") {
    headers.set("cookie", (req.headers.get("cookie") ?? "").split(";").map((value) => value.trim()).filter((value) => value.startsWith("air_agent_iam_admin_handle=")).join("; "));
  }
  try {
    const upstream = await fetch(`${process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100"}${path}${req.nextUrl.search}`, {
      method: req.method, headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.arrayBuffer(),
      redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    return new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return NextResponse.json({ detail: "服务暂不可用，请稍后重试" }, { status: 503 });
  }
}
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
