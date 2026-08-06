import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";

/** Next.js 配置
 *  - 已通过 /app/api/[..._path]/route.ts 提供对 LangGraph API (:2024) 的同源代理, 无需额外 rewrites
 *  - 注释掉脚手架默认附带的 localhost:8000 路由, 避免误导
 */
const nextConfig: NextConfig = {
  // async rewrites() {
  //   return [
  //     {
  //       source: "/assistant/:path*",
  //       destination: "http://localhost:2024/assistant/:path*",
  //     },
  //   ];
  // },
};

export default withAui(nextConfig);
