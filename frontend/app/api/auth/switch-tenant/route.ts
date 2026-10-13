import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { switchTenantViaAdmin } from "@/lib/admin-api";
import { SESSION_COOKIE } from "@/lib/auth";

/** 企业切换：转发浏览器 cookie 与 Origin（admin 侧做 CSRF/context_version 校验）。 */
export async function POST(req: NextRequest) {
  let body: { tenant_id?: unknown; expected_ctx_version?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "请求格式错误" }, { status: 400 });
  }
  if (typeof body.tenant_id !== "string" || typeof body.expected_ctx_version !== "number") {
    return NextResponse.json({ error: "缺少 tenant_id 或 expected_ctx_version" }, { status: 400 });
  }
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ error: "未登录" }, { status: 401 });
  const result = await switchTenantViaAdmin(
    `${SESSION_COOKIE}=${token}`,
    req.headers.get("origin"),
    body.tenant_id,
    body.expected_ctx_version
  );
  if (result.status === 200) return NextResponse.json(result.json);
  const detail = (result.json as { detail?: string } | null)?.detail;
  return NextResponse.json(
    { error: detail ?? "企业切换失败", status: result.status },
    { status: result.status }
  );
}
