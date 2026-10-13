import "server-only";
import { createHmac } from "node:crypto";

/** 归档 scope v2 签发（同源服务 → LangGraph 归档路由与工具链）。
 *
 * `{v:2, tenantId, userId, threadId, aud, exp}`，独立密钥 ARCHIVE_SCOPE_KEYS；
 * 与 LangGraph `common/artifact_scope.py` 的验签格式字节兼容。
 * 客户端与模型都无法伪造：密钥只在服务端环境。
 */
export function signArtifactScope(tenantId: string, userId: string, threadId: string): string {
  const raw = (process.env.ARCHIVE_SCOPE_KEYS ?? "").trim().replace(/^'|'$/g, "");
  if (!raw) throw new Error("归档授权签名未配置（ARCHIVE_SCOPE_KEYS）");
  const keys = JSON.parse(raw) as string[];
  if (!Array.isArray(keys) || !keys.length) throw new Error("归档授权签名配置无效");
  const body = Buffer.from(
    JSON.stringify({
      v: 2,
      kid: "k1",
      tenantId,
      userId,
      threadId,
      aud: "air-agent-archive",
      exp: Math.floor(Date.now() / 1000) + 3600,
    })
  ).toString("base64url");
  const signature = createHmac("sha256", keys[0]).update(body).digest("hex");
  return `${body}.${signature}`;
}
