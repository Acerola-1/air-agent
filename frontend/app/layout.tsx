import "./globals.css";

import { cn } from "@/lib/utils";
import { Suspense } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";

/** 根布局（原生 LangGraph 实现，无 assistant-ui Runtime）
 *  - 使用系统字体栈，避免生产构建依赖外网 Google Fonts
 *  - TooltipProvider: 画布/通用组件依赖
 *  - 线程/对话状态由 zustand chat-store 管理，数据直连后端 :2024（经 /api 代理）
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={cn("h-dvh")}>
        <TooltipProvider>
          <Suspense>{children}</Suspense>
        </TooltipProvider>
      </body>
    </html>
  );
}
