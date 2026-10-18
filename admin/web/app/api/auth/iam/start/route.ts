import { NextRequest, NextResponse } from "next/server";

/** 同源发起登录；只向页面返回授权地址，凭据保留服务端。 */
export async function POST(req: NextRequest) {
  if (process.env.AGENT_IDENTITY_BACKEND !== "remote") return NextResponse.json({ error: "当前部署未启用统一登录" }, { status: 404 });
  const origin = req.headers.get("origin");
  if (!origin || ![`http://${req.headers.get("host")}`, `https://${req.headers.get("host")}`].includes(origin)) return NextResponse.json({ error: "请求来源不被允许" }, { status: 403 });
  try {
    const upstream = await fetch(`${process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100"}/auth/iam/start?frontend=admin`, {
      method: "POST", headers: { origin }, redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(5000),
    });
    const url = upstream.headers.get("location");
    if (upstream.status !== 303 || !url) return NextResponse.json({ error: "统一登录暂不可用，请稍后重试" }, { status: upstream.status >= 400 ? upstream.status : 503 });
    const response = NextResponse.json({ url });
    for (const value of upstream.headers.getSetCookie()) response.headers.append("set-cookie", value);
    return response;
  } catch {
    return NextResponse.json({ error: "统一登录暂不可用，请稍后重试" }, { status: 503 });
  }
}
