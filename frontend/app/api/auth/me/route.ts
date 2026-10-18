import { NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";

/** 当前统一会话上下文（实时来自 admin API）；未登录 401。 */
export async function GET() {
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch (error) {
    const status = typeof (error as { status?: unknown })?.status === "number" ? (error as { status: number }).status : 503;
    return NextResponse.json({ error: "身份服务暂不可用，请稍后重试" }, { status });
  }
  if (!ctx) return NextResponse.json({ error: "未登录" }, { status: 401 });
  return NextResponse.json(ctx);
}
