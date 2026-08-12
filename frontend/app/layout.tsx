import "./globals.css";

import { cn } from "@/lib/utils";
import { Montserrat } from "next/font/google";
import { Suspense } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";

const montserrat = Montserrat({ subsets: ["latin"] });

/** 根布局（原生 LangGraph 实现，无 assistant-ui Runtime）
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
      <body className={cn(montserrat.className, "h-dvh")}>
        <TooltipProvider>
          <Suspense>{children}</Suspense>
        </TooltipProvider>
      </body>
    </html>
  );
}
