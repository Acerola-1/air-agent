import { NextResponse } from "next/server";
import { getSessionUser } from "@/lib/auth";

/** 当前会话用户：已登录返回 { id, username }，否则 401 */
export async function GET() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: "未登录" }, { status: 401 });
  return NextResponse.json({ id: user.id, username: user.username });
}
