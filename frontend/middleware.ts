import { NextRequest, NextResponse } from "next/server";
import { verifySession } from "@/lib/session-token";

/** 路由保护：未登录访问页面 → 重定向 /login；访问 /api/*（认证接口除外）→ 401。
 *  中间件只做 HMAC 签名 + 过期校验（edge 无 fs，不查 users.json）。 */
export async function middleware(req: NextRequest) {
  const token = req.cookies.get("air_agent_session")?.value;
  const secret = process.env.AUTH_SECRET ?? "";
  const payload = token ? await verifySession(token, secret) : null;
  if (payload) return NextResponse.next();

  if (req.nextUrl.pathname.startsWith("/api")) {
    return NextResponse.json({ error: "未登录" }, { status: 401 });
  }
  const url = new URL("/login", req.url);
  url.searchParams.set("next", req.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: [
    // 保护除认证接口 /login 和静态资源外的所有路由
    "/((?!_next/static|_next/image|favicon.ico|login|api/auth|api/health).*)",
  ],
};
