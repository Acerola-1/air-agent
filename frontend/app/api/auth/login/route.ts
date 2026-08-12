import { NextRequest, NextResponse } from "next/server";
import { buildSessionResponse, login } from "@/lib/auth";

/** 登录：校验账号密码，成功签发 cookie，返回 { id, username } */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { username?: unknown; password?: unknown };
    const username = typeof body.username === "string" ? body.username.trim() : "";
    const password = typeof body.password === "string" ? body.password : "";
    const result = await login(username, password);
    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: 401 });
    }
    return await buildSessionResponse(result.user);
  } catch (e) {
    console.error("[auth/login] error:", e);
    return NextResponse.json({ error: "请求格式错误" }, { status: 400 });
  }
}
