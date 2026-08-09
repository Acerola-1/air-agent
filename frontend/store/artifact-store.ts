"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type {
  Artifact,
  ArtifactContentType,
  ArtifactOpenIn,
} from "@/types/artifact";
import { generateUuidV7 } from "@/lib/uuidv7";

/** 后端推送的原始 Artifact 事件结构（create_artifact 工具返回的 artifact_ref）. */
export interface ArtifactEvent {
  type?: "artifact";
  /** 后端生成的幂等 ID（推荐走它做去重）；若缺省，前端会基于 content 哈希生成. */
  artifact_id?: string;
  content_sha256?: string;
  title: string;
  content_type: ArtifactContentType;
  content: string;
  open_in: ArtifactOpenIn;
  auto_detected?: boolean;
  createdAt?: number;
  closed?: boolean;
}

/** 从历史消息 / ToolMessage JSON 中反序列化出来的 artifact_ref. */
export interface ArtifactRef extends Omit<ArtifactEvent, "type"> {}

/** Artifact Store 结构: 分桶维护 open_in 对应的 Artifact ID 索引，方便组件快速取数. */
interface ArtifactState {
  /** 主存储：id -> Artifact */
  artifacts: Record<string, Artifact>;
  /** 画布窗口 Tab 条：按创建时间顺序的 id 列表，last = 当前激活 Tab */
  canvasWindowOrder: string[];
  canvasWindowActiveId: string | null;
  /** 画布窗口是否打开（右侧抽屉式，首次生成时自动开启） */
  canvasWindowOpen: boolean;
  /** 是否已完成当前 thread 的 hydrate：避免重复扫描历史消息. */
  hydrated: boolean;

  // ─────────────── actions ───────────────
  /** 接收后端 stream 事件；artifact_id 存在时按 artifact_id 幂等，否则基于 content_hash. */
  add: (input: ArtifactEvent) => Artifact | null;

  // ──── canvas_window 相关 ────
  activateCanvasTab: (id: string) => void;
  closeCanvasTab: (id: string) => void;
  setCanvasWindowOpen: (open: boolean) => void;
  toggleCanvasWindow: () => void;

  // ──── inline_below / modal 一次性容器用 ────
  closeArtifact: (id: string) => void;
  remove: (id: string) => void;
  clearAll: () => void;

  // ──── history 恢复 ────
  /** 把 hydrated 标记重置为 false，切换 thread 时调用（触发下次 rehydrate）. */
  resetHydrated: () => void;
  /** 基于一组后端返回的原始消息（AIMessage/ToolMessage）恢复 Artifact.
   *
   *  - AIMessage content 字符串：扫私有前缀注释提取 artifact_ref
   *  - ToolMessage content 字符串：解析 JSON，读取 artifact_ref 字段
   *  - 所有提取出的 Artifact 按 artifact_id 去重 add
   *  - 内部设置 hydrated=true；重复调用直接 noop
   */
  hydrateFromHistory: (messages: unknown[]) => number;

  // ──── 按 open_in 取列表的 helper ────
  listByOpenIn: (open_in: ArtifactOpenIn) => Artifact[];
  getById: (id: string) => Artifact | null;
}

/** 生成 Zustand Store.
 *
 * persist key 通过 ArtifactProvider 动态注入（per-thread：`air-agent-artifacts:${threadId}`），
 * 避免多 thread 串数据。这里提供 `threadId` 参数，由 Provider 在外层做 createThreadArtifactStore。
 */
export const createArtifactStore = (threadId: string) =>
  create<ArtifactState>()(
    persist(
      (set, get) => {
        /** listByOpenIn 结果缓存 key: artifacts 引用 */
        let _listCacheKey: Record<string, Artifact> | null = null;
        /** listByOpenIn 结果缓存 value */
        const _listCache = new Map<ArtifactOpenIn, Artifact[]>();

        /** 幂等添加：
         * - 优先按 artifact_id 去重；
         * - 其次按 content_sha256 + title 做软去重（后端旧兼容）；
         * - 返回已存在或新加入的 Artifact；参数非法返回 null.
         */
        const upsert = (input: ArtifactEvent): Artifact | null => {
          if (!input || !input.title || !input.content_type || !input.content) {
            return null;
          }
          const {
            artifact_id,
            content_sha256,
            title,
            content_type,
            content,
            open_in,
            auto_detected = false,
            createdAt,
            closed,
          } = input;

          const artifacts = get().artifacts;
          // 1) 按 artifact_id 精准去重
          if (artifact_id) {
            const existed = Object.values(artifacts).find(
              (a) => (a as unknown as { artifact_id?: string }).artifact_id === artifact_id,
            );
            if (existed) return existed;
          }
          // 2) 按 content_sha256 粗去重
          if (content_sha256) {
            const existed = Object.values(artifacts).find(
              (a) =>
                (a as unknown as { content_sha256?: string }).content_sha256 ===
                  content_sha256 && a.title === title,
            );
            if (existed) return existed;
          }

          const id = artifact_id && artifact_id.startsWith("art_")
            ? artifact_id
            : generateUuidV7();
          const artifact: Artifact = {
            id,
            title,
            content_type,
            content,
            open_in,
            auto_detected,
            createdAt: createdAt ?? Date.now(),
            closed,
            // 额外字段（做 TS 兼容）
            ...(artifact_id
              ? { artifact_id }
              : null),
            ...(content_sha256 ? { content_sha256 } : null),
          } as Artifact & { artifact_id?: string; content_sha256?: string };

          set((s) => {
            const next: Partial<ArtifactState> = {
              artifacts: { ...s.artifacts, [id]: artifact },
            };
            if (open_in === "canvas_window" && !s.canvasWindowOrder.includes(id)) {
              next.canvasWindowOrder = [...s.canvasWindowOrder, id];
              next.canvasWindowActiveId = id;
              next.canvasWindowOpen = true;
            }
            return next;
          });
          return artifact;
        };

        return {
          artifacts: {},
          canvasWindowOrder: [],
          canvasWindowActiveId: null,
          canvasWindowOpen: false,
          hydrated: false,

          add: upsert,

          activateCanvasTab: (id) =>
            set((s) =>
              s.artifacts[id]
                ? { canvasWindowActiveId: id, canvasWindowOpen: true }
                : s,
            ),

          closeCanvasTab: (id) =>
            set((s) => {
              if (!s.artifacts[id]) return s;
              const order = s.canvasWindowOrder.filter((x) => x !== id);
              const nextArtifacts = { ...s.artifacts };
              delete nextArtifacts[id];
              const activeId = s.canvasWindowActiveId === id
                ? order[order.length - 1] ?? null
                : s.canvasWindowActiveId;
              return {
                artifacts: nextArtifacts,
                canvasWindowOrder: order,
                canvasWindowActiveId: activeId,
                canvasWindowOpen: order.length > 0 ? s.canvasWindowOpen : false,
              };
            }),

          setCanvasWindowOpen: (open) => set({ canvasWindowOpen: open }),
          toggleCanvasWindow: () =>
            set((s) => ({ canvasWindowOpen: !s.canvasWindowOpen })),

          closeArtifact: (id) =>
            set((s) => {
              if (!s.artifacts[id]) return s;
              return {
                artifacts: {
                  ...s.artifacts,
                  [id]: { ...s.artifacts[id], closed: true },
                },
              };
            }),

          remove: (id) =>
            set((s) => {
              const nextArtifacts = { ...s.artifacts };
              delete nextArtifacts[id];
              return {
                artifacts: nextArtifacts,
                canvasWindowOrder: s.canvasWindowOrder.filter((x) => x !== id),
                canvasWindowActiveId:
                  s.canvasWindowActiveId === id ? null : s.canvasWindowActiveId,
              };
            }),

          clearAll: () =>
            set({
              artifacts: {},
              canvasWindowOrder: [],
              canvasWindowActiveId: null,
              canvasWindowOpen: false,
              hydrated: false,
            }),

          resetHydrated: () => set({ hydrated: false }),

          hydrateFromHistory: (messages) => {
            if (get().hydrated) return 0;
            const marker = "__AIR_AGENT_ARTIFACT__";
            const re = new RegExp(
              `<!--\\s*${marker}\\s+(?<json>\\{.*?\\})\\s*-->`,
              "gs",
            );
            let added = 0;
            for (const rawMsg of messages) {
              if (!rawMsg || typeof rawMsg !== "object") continue;
              const msg = rawMsg as {
                type?: string;
                content?: unknown;
                artifact_ref?: unknown;
              };
              const content = msg.content;
              const text = typeof content === "string" ? content : "";

              // 1) ToolMessage JSON 中 artifact_ref 字段
              if (msg.type === "tool" && text) {
                try {
                  const obj = JSON.parse(text);
                  const ref = obj?.artifact_ref ?? obj?.artifact;
                  if (ref && typeof ref === "object" && "title" in ref) {
                    const art = upsert((ref as unknown) as ArtifactEvent);
                    if (art) added++;
                  }
                } catch {
                  /* ignore parse error */
                }
              }

              // 2) AIMessage / ToolMessage content 中嵌入的 HTML 注释 artifact_ref
              if (text) {
                for (const m of text.matchAll(re)) {
                  const groups = m.groups as { json: string } | undefined;
                  if (!groups?.json) continue;
                  try {
                    const ref = JSON.parse(groups.json);
                    if (ref && typeof ref === "object" && "title" in ref) {
                      const art = upsert((ref as unknown) as ArtifactEvent);
                      if (art) added++;
                    }
                  } catch {
                    /* ignore */
                  }
                }
              }
            }
            set({ hydrated: true });
            return added;
          },

          listByOpenIn: (open_in) => {
            const state = get();
            if (state.artifacts !== _listCacheKey) {
              _listCacheKey = state.artifacts;
              _listCache.clear();
            }
            const cached = _listCache.get(open_in);
            if (cached) return cached;
            const result = Object.values(state.artifacts)
              .filter((a) => a.open_in === open_in)
              .sort((a, b) => a.createdAt - b.createdAt);
            _listCache.set(open_in, result);
            return result;
          },

          getById: (id) => get().artifacts[id] ?? null,
        };
      },
      {
        name: `air-agent-artifacts:${threadId}`,
        partialize: (s) => ({
          // 仅持久化与画布恢复相关的数据；inline_below/modal 不需要跨刷新保留
          artifacts: s.artifacts,
          canvasWindowOrder: s.canvasWindowOrder,
          canvasWindowActiveId: s.canvasWindowActiveId,
          canvasWindowOpen: s.canvasWindowOpen,
        }),
      },
    ),
  );

export type ArtifactStore = ReturnType<typeof createArtifactStore>;
