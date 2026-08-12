import { NextRequest, NextResponse } from "next/server";
import { buildSessionResponse, register } from "@/lib/auth";

/** 注册新账号：成功即签发登录 cookie，返回 { id, username } */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { username?: unknown; password?: unknown };
    const username = typeof body.username === "string" ? body.username.trim() : "";
    const password = typeof body.password === "string" ? body.password : "";
    const result = await register(username, password);
    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: 400 });
    }
    return await buildSessionResponse(result.user);
  } catch (e) {
    console.error("[auth/register] error:", e);
    return NextResponse.json({ error: "请求格式错误" }, { status: 400 });
  }
}
