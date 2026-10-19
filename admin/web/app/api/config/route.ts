import { NextResponse } from "next/server";

/** 前端可见的部署配置。
 *
 * `remote` 模式下用户/企业/角色/权限/审计由 IAM 管理台负责，后台首页与
 * 侧边栏应直接指向该入口，而不是继续暴露已退役的本地页面。
 */
export const dynamic = "force-dynamic";

export function GET() {
  const base = (process.env.IAM_BASE_URL ?? "").trim().replace(/\/+$/, "");
  const remote = (process.env.AGENT_IDENTITY_BACKEND ?? "").trim().toLowerCase() === "remote";
  return NextResponse.json({
    identityBackend: remote ? "remote" : "local",
    iamAdminUrl: remote && base ? `${base}/admin` : null,
  });
}
