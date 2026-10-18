import { NextRequest, NextResponse } from "next/server";

/** 路由保护：仅做导航层检查（cookie 是否存在且为新协议格式）。
 *
 * 真正的授权在每个业务请求：/api/* Node 代理向 admin API 实时确认身份，
 * LangGraph 侧执行票据+归属校验。旧 HMAC cookie（首段非 UUID）不放行导航，
 * 强制走登录页重新获取统一会话。
 */
export function middleware(req: NextRequest) {
  const value = req.cookies.get(process.env.AGENT_IDENTITY_BACKEND === "remote" ? "air_agent_iam_handle" : "air_agent_session")?.value ?? "";
  const uuidLike = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\..+/i;
  if (process.env.AGENT_IDENTITY_BACKEND === "remote" ? /^[0-9a-f]{32}\.[0-9a-f]{8}$/.test(value) : uuidLike.test(value)) return NextResponse.next();

  if (req.nextUrl.pathname.startsWith("/api")) {
    return NextResponse.json({ error: "未登录" }, { status: 401 });
  }
  const url = new URL("/login", req.url);
  if (value) url.searchParams.set("relogin", "1");
  url.searchParams.set("next", req.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|login|api/auth|api/health).*)",
  ],
};
