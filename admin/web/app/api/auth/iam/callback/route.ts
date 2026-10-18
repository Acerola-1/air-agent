import { type NextRequest, NextResponse } from "next/server";


/** SSO 回调：透传 ticket/state 到 admin API remote 门面（服务端兑换+会话登记）.
 * URL 中的 ticket 不落任何日志/存储；响应是 303 到干净路径（ticket 已剥离）。
 */
export async function GET(req: NextRequest) {
  if (process.env.AGENT_IDENTITY_BACKEND !== "remote") {
    return NextResponse.json({ error: "当前部署未启用 SSO" }, { status: 404 });
  }
  const base = process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";
  const params = new URLSearchParams({ ticket: req.nextUrl.searchParams.get("ticket") ?? "", state: req.nextUrl.searchParams.get("state") ?? "", frontend: "admin" });
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
    return NextResponse.json({ error: "身份服务暂不可用" }, { status: 503 });
  }
  if (res.status >= 400) {
    await res.text();
    const out = NextResponse.json({ error: "统一登录失败，请重新发起登录" }, { status: res.status });
    for (const value of res.headers.getSetCookie()) out.headers.append("set-cookie", value);
    return out;
  }
  // 303 到干净路径；透传应用会话 handle cookie（相对 Location 解析为同源绝对地址）
  const rawLocation = res.headers.get("location") ?? "/";
  const out = NextResponse.redirect(new URL(rawLocation, `${req.nextUrl.protocol}//${req.headers.get("host")}`), 303);
  for (const c of res.headers.getSetCookie?.() ?? []) out.headers.append("set-cookie", c);
  return out;
}
