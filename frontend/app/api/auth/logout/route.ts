import { NextRequest, NextResponse } from "next/server";
import { logoutViaUnified, SESSION_COOKIE } from "@/lib/auth";

/** 登出：转 admin 撤销服务端会话 + 清本地 cookie。幂等。 */
export async function POST(req: NextRequest) {
  const cookie = req.headers.get("cookie") ?? "";
  await logoutViaUnified(cookie, req.headers.get("origin"));
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return res;
}
