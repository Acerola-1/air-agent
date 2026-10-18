import { NextRequest, NextResponse } from "next/server";
import { logoutViaUnified, sessionCookieName } from "@/lib/auth";

/** 登出：转 admin 撤销服务端会话 + 清本地 cookie。幂等。 */
export async function POST(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";
  try {
    await logoutViaUnified(cookie, req.headers.get("origin"));
  } catch {
    return NextResponse.json({ error: "退出未完成，请重试" }, { status: 503 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(sessionCookieName(), "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return res;
}
