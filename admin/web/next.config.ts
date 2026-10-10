import type { NextConfig } from "next";

/**
 * admin 前端把 /api/* 反代到 admin API（:8100）。
 *
 * 走同源代理而不是浏览器直连 :8100 的原因：会话是 httpOnly cookie，同源下
 * 浏览器会自动带上，既不需要处理 CORS credentials，也不用把 cookie 暴露给
 * 跨站请求。
 */
const ADMIN_API_URL = process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${ADMIN_API_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
