import type { NextConfig } from "next";

/** API 统一由 app/api/[...path] 代理，避免 rewrite 抢先绕过会话隔离。 */
const nextConfig: NextConfig = {};
export default nextConfig;
