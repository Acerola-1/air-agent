import { create } from "zustand";

function getTenantCtx(): { tenantId: string; contextVersion: number } | null {
  if (
    typeof window !== "undefined" &&
    (window as unknown as { __getTenantRequestContext?: () => { tenantId: string; contextVersion: number } | null }).__getTenantRequestContext
  ) {
    return (window as unknown as { __getTenantRequestContext: () => { tenantId: string; contextVersion: number } | null }).__getTenantRequestContext!();
  }
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = typeof require !== "undefined" ? require("./langgraph-api") : null;
    return mod?.getTenantRequestContext?.() ?? null;
  } catch {
    return null;
  }
}

export type SkillCatalogItem = {
  id: string;
  name?: string;
  title?: string;
  description?: string;
  graph_id?: string;
  material_version?: string;
  required_tools?: string[];
  required_tool_ids?: string[];
  allowed_tools?: string[];
  allowed_tool_ids?: string[];
};

export type McpToolCatalogItem = {
  id?: string;
  name?: string;
  server_key?: string;
  tool_name?: string;
  display_name?: string;
  description?: string;
  status?: "available" | "unavailable";
};

export type McpServiceCatalogItem = {
  server_key: string;
  display_name: string;
  protocol_version?: string;
  status: "available" | "unavailable" | "unverified" | "not_configured" | "temporarily_unavailable";
};

export type CapabilityCatalogData = {
  catalog_version: string;
  graph_id: string;
  skills: SkillCatalogItem[];
  tools: McpToolCatalogItem[];
  mcp_services?: McpServiceCatalogItem[];
  services?: McpServiceCatalogItem[];
  unavailable_services: string[];
  next_cursor?: string | null;
};

export type CapabilitySelectionDraft = {
  skillMode: "auto" | "explicit";
  selectedSkillId: string | null;
  mcpMode: "auto" | "allowlist";
  selectedToolIds: string[];
};

export type CapabilityAcceptedSnapshot = {
  id: string;
  skill_mode: "auto" | "explicit";
  skill_id: string | null;
  mcp_mode: "auto" | "allowlist";
  tool_ids: string[];
  selection_digest: string;
  selection_source: string;
};

/** 仅转换服务端回读的已受理计划；缺引用或摘要时不显示成功。 */
export function acceptedSnapshot(raw: Record<string, unknown> | null | undefined): CapabilityAcceptedSnapshot | null {
  if (!raw || !(raw.id || raw.selection_id) || typeof raw.selection_digest !== "string") return null;
  const skill = raw.skill as { mode?: "auto" | "explicit"; skill_id?: string } | undefined;
  const mcp = raw.mcp as { mode?: "auto" | "allowlist"; tool_ids?: string[] } | undefined;
  return {
    id: String(raw.id || raw.selection_id),
    skill_mode: (raw.skill_mode as "auto" | "explicit") ?? skill?.mode ?? "auto",
    skill_id: (raw.skill_id as string | null) ?? skill?.skill_id ?? null,
    mcp_mode: (raw.mcp_mode as "auto" | "allowlist") ?? mcp?.mode ?? "auto",
    tool_ids: [...((raw.tool_ids as string[] | undefined) ?? mcp?.tool_ids ?? [])],
    selection_digest: raw.selection_digest,
    selection_source: String(raw.selection_source ?? "admitted"),
  };
}

type CapabilityState = {
  catalog: CapabilityCatalogData | null;
  catalogLoading: boolean;
  catalogError: string | null;
  epoch: number;
  activeGraphId: string | null;
  activeTenantId: string | null;
  selection: CapabilitySelectionDraft;
  acceptedSelection: CapabilityAcceptedSnapshot | null;

  // Actions
  fetchCatalog: (graphId: string, tenantId?: string | null) => Promise<void>;
  setSkillMode: (mode: "auto" | "explicit", skillId?: string | null) => void;
  setMcpMode: (mode: "auto" | "allowlist", toolIds?: string[]) => void;
  toggleTool: (toolId: string) => void;
  resetToDefault: () => void;
  setAcceptedSelection: (snapshot: CapabilityAcceptedSnapshot | null) => void;
  clearForScopeSwitch: (newGraphId: string, newTenantId?: string | null) => void;
  toPayload: () => Record<string, unknown>;
};

const DEFAULT_SELECTION: CapabilitySelectionDraft = {
  skillMode: "auto",
  selectedSkillId: null,
  mcpMode: "auto",
  selectedToolIds: [],
};

let globalEpoch = 0;

export const useCapabilityStore = create<CapabilityState>((set, get) => ({
  catalog: null,
  catalogLoading: false,
  catalogError: null,
  epoch: 0,
  activeGraphId: null,
  activeTenantId: null,
  selection: { ...DEFAULT_SELECTION },
  acceptedSelection: null,

  fetchCatalog: async (graphId: string, tenantId?: string | null) => {
    const currentEpoch = ++globalEpoch;
    const tenantCtx = getTenantCtx();
    const effectiveTenant = tenantId ?? tenantCtx?.tenantId ?? null;
    const prevGraph = get().activeGraphId;
    const prevTenant = get().activeTenantId;
    const scopeChanged = prevGraph !== graphId || prevTenant !== effectiveTenant;

    set({
      catalogLoading: true,
      catalogError: null,
      epoch: currentEpoch,
      activeGraphId: graphId,
      activeTenantId: effectiveTenant,
      ...(scopeChanged ? { selection: { ...DEFAULT_SELECTION }, acceptedSelection: null } : {}),
    });

    try {
      const headers: Record<string, string> = {};
      if (tenantCtx) {
        headers["x-tenant-id"] = tenantCtx.tenantId;
        headers["x-tenant-context-version"] = String(tenantCtx.contextVersion);
      } else if (effectiveTenant) {
        headers["x-tenant-id"] = effectiveTenant;
      }
      const tenantParam = effectiveTenant ? `&tenant_id=${encodeURIComponent(effectiveTenant)}` : "";
      const res = await fetch(`/api/capabilities?graph_id=${encodeURIComponent(graphId)}${tenantParam}`, {
        cache: "no-store",
        headers,
      });
      if (get().epoch !== currentEpoch) {
        // Epoch 已推进（A -> B -> A 或新图切换），迟到响应静默丢弃
        return;
      }
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        if (get().epoch !== currentEpoch) return;
        set({
          catalogLoading: false,
          catalogError: (errJson as { error?: string }).error || `目录加载失败 (${res.status})`,
        });
        return;
      }
      const data = (await res.json()) as CapabilityCatalogData;
      if (get().epoch !== currentEpoch) return;
      // 分页属于同一个目录版本，禁止拼接刷新后的另一份目录。
      let cursor = data.next_cursor;
      const seen = new Set<string>();
      while (cursor) {
        if (seen.has(cursor) || seen.size >= 50) throw new Error("能力目录过大，请联系管理员调整范围");
        seen.add(cursor);
        const page = await fetch(`/api/capabilities?graph_id=${encodeURIComponent(graphId)}&cursor=${encodeURIComponent(cursor)}`, { cache: "no-store", headers });
        if (get().epoch !== currentEpoch) return;
        if (!page.ok) throw new Error("能力目录已变化或加载失败，请刷新重试");
        const next = await page.json() as CapabilityCatalogData;
        if (get().epoch !== currentEpoch) return;
        if (next.catalog_version !== data.catalog_version || next.graph_id !== graphId) throw new Error("能力目录已变化，请刷新重试");
        data.skills.push(...next.skills);
        data.tools.push(...next.tools);
        cursor = next.next_cursor;
      }

      // 如果显式选择的 skillId 不在当前 catalog 中，重置为 auto
      const currentSel = get().selection;
      let nextSkillMode = currentSel.skillMode;
      let nextSkillId = currentSel.selectedSkillId;
      if (nextSkillMode === "explicit" && nextSkillId) {
        const found = data.skills.some((s) => s.id === nextSkillId);
        if (!found) {
          nextSkillMode = "auto";
          nextSkillId = null;
        }
      }

      set({
        catalog: data,
        catalogLoading: false,
        catalogError: null,
        selection: {
          ...currentSel,
          skillMode: nextSkillMode,
          selectedSkillId: nextSkillId,
        },
      });
    } catch (err) {
      if (get().epoch !== currentEpoch) return;
      set({
        catalogLoading: false,
        catalogError: err instanceof Error ? err.message : "获取能力目录网络异常",
      });
    }
  },

  setSkillMode: (mode, skillId = null) => {
    set((state) => ({
      selection: {
        ...state.selection,
        skillMode: mode,
        selectedSkillId: mode === "explicit" ? skillId : null,
      },
    }));
  },

  setMcpMode: (mode, toolIds = []) => {
    set((state) => ({
      selection: {
        ...state.selection,
        mcpMode: mode,
        selectedToolIds: mode === "allowlist" ? [...toolIds] : [],
      },
    }));
  },

  toggleTool: (toolId: string) => {
    set((state) => {
      const current = state.selection;
      const inList = current.selectedToolIds.includes(toolId);
      const nextTools = inList
        ? current.selectedToolIds.filter((id) => id !== toolId)
        : [...current.selectedToolIds, toolId];
      return {
        selection: {
          ...current,
          mcpMode: "allowlist",
          selectedToolIds: nextTools,
        },
      };
    });
  },

  resetToDefault: () => {
    set({
      selection: { ...DEFAULT_SELECTION },
    });
  },

  setAcceptedSelection: (snapshot) => {
    set({ acceptedSelection: snapshot });
  },

  clearForScopeSwitch: (newGraphId, newTenantId = null) => {
    const nextEpoch = ++globalEpoch;
    set({
      epoch: nextEpoch,
      activeGraphId: newGraphId,
      activeTenantId: newTenantId,
      catalog: null,
      catalogLoading: false,
      catalogError: null,
      selection: { ...DEFAULT_SELECTION },
      acceptedSelection: null,
    });
  },

  toPayload: () => {
    const sel = get().selection;
    const cat = get().catalog;
    const isExplicit = sel.skillMode === "explicit" || sel.mcpMode === "allowlist";
    const toolIds =
      sel.mcpMode === "allowlist"
        ? Array.from(new Set(sel.selectedToolIds)).sort()
        : [];
    const payload: Record<string, unknown> = {
      schema_version: "agent.capability-selection.v1",
      skill: {
        mode: sel.skillMode,
        skill_id: sel.skillMode === "explicit" ? sel.selectedSkillId : null,
      },
      mcp: {
        mode: sel.mcpMode,
        tool_ids: toolIds,
      },
      catalog_version: isExplicit ? (cat?.catalog_version ?? null) : null,
    };
    Object.defineProperty(payload, "selection_source", {
      value: isExplicit ? ("user_explicit" as const) : ("automatic" as const),
      enumerable: false,
      configurable: true,
    });
    return payload;
  },
}));
