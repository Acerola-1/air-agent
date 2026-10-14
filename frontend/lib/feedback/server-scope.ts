import "server-only";
import type { AdminSessionContext } from "@/lib/admin-api-contract";

/** 声明只用于拒绝旧标签页，身份仍全部取可信会话。 */
export function feedbackScopeMatches(headers: Headers, ctx: AdminSessionContext): boolean {
  return headers.get("x-feedback-scope") === `${ctx.current_tenant?.id ?? ""}:${ctx.user.id}`
    && headers.get("x-feedback-context-version") === String(ctx.context_version);
}
