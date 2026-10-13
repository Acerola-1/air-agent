import { NextRequest, NextResponse } from "next/server";
import { loginViaUnified } from "@/lib/auth";

/** 登录：转统一身份域；透传 Set-Cookie；多企业返回需选择标记。 */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { account?: unknown; username?: unknown; password?: unknown };
    const account = typeof body.account === "string"
      ? body.account.trim()
      : typeof body.username === "string"
        ? body.username.trim()
        : "";
    const password = typeof body.password === "string" ? body.password : "";
    const result = await loginViaUnified(account, password, req.headers.get("origin"));
    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: result.status });
    }
    const res = NextResponse.json(result.ctx);
    for (const setCookie of result.setCookies) res.headers.append("set-cookie", setCookie);
    return res;
  } catch {
    return NextResponse.json({ error: "请求格式错误" }, { status: 400 });
  }
}
