import "./globals.css";

import { cn } from "@/lib/utils";
import { Montserrat } from "next/font/google";
import { MyRuntimeProvider } from "./MyRuntimeProvider";
import { Suspense } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";

const montserrat = Montserrat({ subsets: ["latin"] });

/** 根布局
 *  - TooltipProvider: assistant-ui thread 组件依赖 (add thread 后 CLI 提示必须包裹)
 *  - MyRuntimeProvider: 注入 LangGraph 运行时 (对接 :2024 后端)
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <MyRuntimeProvider>
      <html lang="zh-CN">
        <body className={cn(montserrat.className, "h-dvh")}>
          <TooltipProvider>
            <Suspense>{children}</Suspense>
          </TooltipProvider>
        </body>
      </html>
    </MyRuntimeProvider>
  );
}
