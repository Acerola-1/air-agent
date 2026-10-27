import { type NextRequest, NextResponse } from "next/server";


/** SSO 回调：透传 ticket/state 到 admin API remote 门面（服务端兑换+会话登记）.
 * URL 中的 ticket 不落任何日志/存储；响应是 303 到干净路径（ticket 已剥离）。
 */
export async function GET(req: NextRequest) {
  if (process.env.AGENT_IDENTITY_BACKEND !== "remote") {
    return NextResponse.json({ error: "当前部署未启用 SSO" }, { status: 404 });
  }
  const base = process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";
  const params = new URLSearchParams({ ticket: req.nextUrl.searchParams.get("ticket") ?? "", state: req.nextUrl.searchParams.get("state") ?? "", frontend: "chat" });
  const error = req.nextUrl.searchParams.get("error");
  if (error) params.set("error", error);
  // Next may normalize 127.0.0.1 to localhost internally; preserve the incoming
  // browser host so host-only session cookies stay on the callback origin.
  const origin = new URL(`${req.nextUrl.protocol}//${req.headers.get("host") ?? req.nextUrl.host}`).origin;
  const login = (reason: string) => {
    const out = NextResponse.redirect(new URL(`/login?reason=${reason}`, origin), 303);
    out.headers.set("cache-control", "no-store");
    return out;
  };
  const query = `?${params}`; // ticket & state 原样转发（仅此一跳）
  let res: Response;
  try {
    res = await fetch(`${base}/auth/iam/callback${query}`, {
      headers: { cookie: req.headers.get("cookie") ?? "" },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    return login("identity-unavailable");
  }
  if (res.status !== 303) {
    await res.text();
    const out = login(res.status >= 500 ? "identity-unavailable" : "sso-failed");
    for (const value of res.headers.getSetCookie()) out.headers.append("set-cookie", value);
    return out;
  }
  // 303 到干净路径；透传应用会话 handle cookie（相对 Location 解析为同源绝对地址）
  const rawLocation = res.headers.get("location") ?? "/";
  const destination = new URL(rawLocation, origin);
  if (destination.origin !== origin) return login("sso-failed");
  const out = NextResponse.redirect(destination, 303);
  out.headers.set("cache-control", "no-store");
  for (const c of res.headers.getSetCookie?.() ?? []) out.headers.append("set-cookie", c);
  return out;
}
