"use client";

import { useEffect, useRef } from "react";
import { useLangChainStream } from "@assistant-ui/react-langchain";
import { useArtifactStore } from "./ArtifactProvider";
import type { ArtifactEvent } from "@/store/artifact-store";

/** 从流中提取 create_artifact 工具返回的 artifact_ref 并推送到画布.
 *
 * 使用 useEffect 监听 stream.messages 变化, 扫描新增的 ToolMessage,
 * 解析 JSON content 中的 artifact_ref 并调用 store.add 推送到画布.
 *
 * 选择此方案而非 useChannelEffect 的原因:
 * - v2 协议 messages channel 仅推送 token 级增量, 不含 ToolMessage
 * - v2 协议 values channel 的事件格式不明确, onEvent 回调难以正确解析
 * - stream.messages 是已组装的完整消息列表, 由 LangGraph SDK 自动合并
 *   messages channel 增量和 values channel 快照, 可靠性最高
 */
export function ArtifactToolInterceptor() {
  const stream = useLangChainStream();
  const store = useArtifactStore();
  const processedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!stream) return;
    const messages = stream.messages;
    if (!messages || !Array.isArray(messages)) return;

    for (const msg of messages) {
      if (!msg || !msg.id || processedRef.current.has(msg.id)) continue;

      // 兼容 LangChain BaseMessage 的 _getType() 和序列化后的 type 字段
      const msgType =
        (msg as { _getType?: () => string })._getType?.() ??
        (msg as { type?: string }).type;
      if (msgType !== "tool") continue;

      const content = (msg as { content?: unknown }).content;
      const text = typeof content === "string" ? content : "";
      if (!text) continue;

      try {
        const obj = JSON.parse(text) as {
          artifact_ref?: unknown;
          artifact?: unknown;
        };
        const ref = obj.artifact_ref ?? obj.artifact;
        if (
          ref &&
          typeof ref === "object" &&
          "title" in (ref as Record<string, unknown>)
        ) {
          processedRef.current.add(msg.id);
          console.log("[ArtifactToolInterceptor] 提取到 artifact:", {
            title: (ref as ArtifactEvent).title,
            content_type: (ref as ArtifactEvent).content_type,
            content_len: ((ref as ArtifactEvent).content ?? "").length,
            open_in: (ref as ArtifactEvent).open_in,
          });
          store.getState().add(ref as ArtifactEvent);
        }
      } catch {
        // content 不是合法 JSON, 忽略 (其他工具的 ToolMessage)
      }
    }
  }, [stream, stream?.messages, store]);

  return null;
}
