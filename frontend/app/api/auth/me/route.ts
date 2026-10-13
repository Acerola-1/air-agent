import { NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";

/** 当前统一会话上下文（实时来自 admin API）；未登录 401。 */
export async function GET() {
  const ctx = await getSessionContext();
  if (!ctx) return NextResponse.json({ error: "未登录" }, { status: 401 });
  return NextResponse.json(ctx);
}
