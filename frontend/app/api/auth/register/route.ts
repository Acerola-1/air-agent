import { NextResponse } from "next/server";

/** 公开注册已下线（统一身份域）：账号由平台管理员开通。 */
export async function POST() {
  return NextResponse.json(
    { error: "公开注册已停止，请联系管理员开通账号后登录" },
    { status: 410 }
  );
}
