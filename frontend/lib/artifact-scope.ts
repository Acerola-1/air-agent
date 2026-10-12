import "server-only";
import { createHmac } from "node:crypto";

/** 同源服务签发当前用户的单线程授权，模型和浏览器不能自行改写归属。 */
export function signArtifactScope(userId: string, threadId: string): string {
  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new Error("会话服务配置缺失");
  const body = Buffer.from(JSON.stringify({ userId, threadId, exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
  return `${body}.${createHmac("sha256", secret).update(body).digest("hex")}`;
}
