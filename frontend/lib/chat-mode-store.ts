"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/** 回答模式: fast=快速模式(默认), expert=专家模式. */
export type ChatMode = "fast" | "expert";

const STORAGE_KEY = "air-agent-chat-mode";

type ChatModeState = {
  chatMode: ChatMode;
  setChatMode: (mode: ChatMode) => void;
};

/** 全局常驻的回答模式状态.
 *
 *  - zustand + localStorage 持久化: 刷新页面、切换 Graph 后依然保持用户选择.
 *  - 与后端 Configuration.chat_mode 对应, 通过 runConfig 注入每次 run.
 */
export const useChatModeStore = create<ChatModeState>()(
  persist(
    (set) => ({
      chatMode: "fast",
      setChatMode: (chatMode) => set({ chatMode }),
    }),
    { name: STORAGE_KEY },
  ),
);

export const useChatMode = () => useChatModeStore((s) => s.chatMode);
export const useSetChatMode = () => useChatModeStore((s) => s.setChatMode);
