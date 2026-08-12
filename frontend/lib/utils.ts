import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn 标准 className 合并工具
 *  - clsx 负责条件拼接
 *  - twMerge 负责 Tailwind 冲突类（如 px-2 vs px-4）去重合并
 *  所有 components/ui/* 和 components/assistant-ui/* 都依赖此函数
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
