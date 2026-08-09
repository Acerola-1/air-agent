"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  type PropsWithChildren,
} from "react";
import { createArtifactStore } from "@/store/artifact-store";
import type {
  Artifact,
  ArtifactContentType,
  ArtifactOpenIn,
} from "@/types/artifact";

// 从 createArtifactStore 返回的 useStore 里反推 State 类型
type _ArtifactStore = ReturnType<typeof createArtifactStore>;
type ArtifactUseBoundStore = _ArtifactStore;
type ArtifactState = ReturnType<_ArtifactStore["getState"]>;

/** React Context: 每个 Thread 独立的 Artifact Store 实例. */
const ArtifactStoreContext = createContext<ArtifactUseBoundStore | null>(null);

/** 前端暴露给子组件的工具方法集合. */
export interface ArtifactApi {
  /** 手动添加一个 Artifact（供前端组件内部调用，例如点击"在画布打开"按钮）. */
  addArtifact: (input: {
    title: string;
    content_type: ArtifactContentType;
    content: string;
    open_in?: ArtifactOpenIn;
    auto_detected?: boolean;
  }) => Artifact;
}

const ArtifactApiContext = createContext<ArtifactApi | null>(null);

/** ArtifactProvider: 创建并注入 Store 实例 + 暴露 API.
 *
 *  - 本 Provider 在 MyRuntimeProvider 内部按 Thread 生命周期挂载（每次切 graph 重建一次），
 *    天然隔离不同 graph / thread 的 Artifact 数据.
 *  - Provider 自身不包含视觉组件，仅持有 store；
 *    CanvasWindow / InlineContainer / Modal 等在 page.tsx / thread.tsx 里使用 useArtifactStore() 消费.
 */
export function ArtifactProvider({
  children,
  threadId,
}: PropsWithChildren<{ threadId?: string }>) {
  const storeRef = useRef<ArtifactUseBoundStore | null>(null);
  if (!storeRef.current) {
    storeRef.current = createArtifactStore(threadId ?? "__default__");
  }
  const store = storeRef.current;

  const addArtifact = useCallback<ArtifactApi["addArtifact"]>(
    (input) => {
      return store.getState().add({
        title: input.title,
        content_type: input.content_type,
        content: input.content,
        open_in: input.open_in ?? "canvas_window",
        auto_detected: input.auto_detected,
      }) as Artifact;
    },
    [store],
  );

  useEffect(() => {
    const w = window as unknown as {
      __AIR_AGENT_ARTIFACT_ADD?: ArtifactApi["addArtifact"];
    };
    w.__AIR_AGENT_ARTIFACT_ADD = addArtifact;
    return () => {
      if (w.__AIR_AGENT_ARTIFACT_ADD === addArtifact) delete w.__AIR_AGENT_ARTIFACT_ADD;
    };
  }, [addArtifact]);

  return (
    <ArtifactStoreContext.Provider value={store}>
      <ArtifactApiContext.Provider value={{ addArtifact }}>
        {children}
      </ArtifactApiContext.Provider>
    </ArtifactStoreContext.Provider>
  );
}

/** React hook: 获取当前 Thread 对应的 Artifact Store.
 *
 *  - 不传 selector: 返回 zustand store 实例（含 .getState()/.subscribe() 等方法），
 *    适合在事件回调、useEffect 中做一次性 action 调用。
 *  - 传 selector: 返回 selector(state) 的值，并在其变化时 re-render
 *  - 传 equalityFn（如 zustand useShallow）: store 层做引用缓存，
 *    否则 useSyncExternalStore 会报 `getServerSnapshot should be cached` infinite loop.
 */
export function useArtifactStore<T>(
  selector: (s: ArtifactState) => T,
  equalityFn?: (a: T, b: T) => boolean,
): T;
export function useArtifactStore(): ArtifactUseBoundStore;
export function useArtifactStore<T>(
  selector?: (s: ArtifactState) => T,
  equalityFn?: (a: T, b: T) => boolean,
): T | ArtifactUseBoundStore {
  const ctx = useContext(ArtifactStoreContext);
  if (!ctx) {
    throw new Error(
      "[useArtifactStore] 必须在 <ArtifactProvider /> 内部使用。",
    );
  }
  // 不传 selector: 直接返回 store 实例（zustand create() 返回的 hook 函数本身，
  // 带 .getState()/.setState()/.subscribe() 方法），不作为 hook 调用.
  if (!selector) return ctx;
  // 传 selector: 作为 React hook 调用，订阅 state 变化
  return (ctx as (sel: (s: ArtifactState) => T, eq?: (a: T, b: T) => boolean) => T)(
    selector,
    equalityFn,
  );
}

/** React hook: 获取 Artifact API（addArtifact）. */
export function useArtifactApi(): ArtifactApi {
  const ctx = useContext(ArtifactApiContext);
  if (!ctx) {
    throw new Error(
      "[useArtifactApi] 必须在 <ArtifactProvider /> 内部使用。",
    );
  }
  return ctx;
}
