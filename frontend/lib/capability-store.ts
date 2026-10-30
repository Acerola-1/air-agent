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
  mcpMode: "auto" | "allowlist";
  selectedToolIds: string[];
};

export type CapabilityAcceptedSnapshot = {
  selection_id: string;
  selection_digest: string;
  digest_version: 3;
  selection_status: "collected";
  schema_version: "agent.capability-selection.v2";
  mcp: { mode: "auto" | "allowlist"; tool_ids: string[] };
  catalog_version: string | null;
  selection_source: string;
  validation_time: string;
};

/** 仅转换服务端回读的已受理计划；缺引用或摘要时不显示成功。 */
export function acceptedSnapshot(raw: Record<string, unknown> | null | undefined): CapabilityAcceptedSnapshot | null {
  if (!raw) return null;
  const retiredFields = ["skill", "skill_mode", "skill_id", "material_version"];
  if (retiredFields.some((field) => Object.hasOwn(raw, field))) return null;
  const selectionId =
    typeof raw.selection_id === "string" && raw.selection_id.length > 0
      ? raw.selection_id
      : typeof raw.id === "string" && raw.id.length > 0
        ? raw.id
        : null;
  if (
    !selectionId ||
    typeof raw.selection_digest !== "string" ||
    raw.selection_digest.length === 0 ||
    raw.digest_version !== 3 ||
    raw.selection_status !== "collected" ||
    raw.schema_version !== "agent.capability-selection.v2" ||
    (raw.catalog_version !== null && typeof raw.catalog_version !== "string") ||
    typeof raw.selection_source !== "string" ||
    typeof raw.validation_time !== "string"
  ) return null;
  const mcp = raw.mcp as { mode?: unknown; tool_ids?: unknown } | undefined;
  if (
    !mcp ||
    Object.keys(mcp).some((key) => !["mode", "tool_ids"].includes(key)) ||
    (mcp.mode !== "auto" && mcp.mode !== "allowlist") ||
    !Array.isArray(mcp.tool_ids) ||
    !mcp.tool_ids.every((toolId) => typeof toolId === "string") ||
    (mcp.mode === "auto" && mcp.tool_ids.length > 0)
  ) return null;
  return {
    selection_id: selectionId,
    selection_digest: raw.selection_digest,
    digest_version: 3,
    selection_status: "collected",
    schema_version: "agent.capability-selection.v2",
    mcp: {
      mode: mcp.mode,
      tool_ids: mcp.mode === "auto" ? [] : [...new Set(mcp.tool_ids as string[])].sort(),
    },
    catalog_version: raw.catalog_version as string | null,
    selection_source: raw.selection_source,
    validation_time: raw.validation_time,
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
  setMcpMode: (mode: "auto" | "allowlist", toolIds?: string[]) => void;
  toggleTool: (toolId: string) => void;
  resetToDefault: () => void;
  setAcceptedSelection: (snapshot: CapabilityAcceptedSnapshot | null) => void;
  clearForScopeSwitch: (newGraphId: string, newTenantId?: string | null) => void;
  toPayload: () => Record<string, unknown>;
};

const DEFAULT_SELECTION: CapabilitySelectionDraft = {
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

      const currentSel = get().selection;
      set({
        catalog: data,
        catalogLoading: false,
        catalogError: null,
        selection: currentSel,
      });
    } catch (err) {
      if (get().epoch !== currentEpoch) return;
      set({
        catalogLoading: false,
        catalogError: err instanceof Error ? err.message : "获取能力目录网络异常",
      });
    }
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
    const toolIds =
      sel.mcpMode === "allowlist"
        ? Array.from(new Set(sel.selectedToolIds)).sort()
        : [];
    return {
      schema_version: "agent.capability-selection.v2",
      mcp: {
        mode: sel.mcpMode,
        tool_ids: toolIds,
      },
      catalog_version: sel.mcpMode === "allowlist" ? (cat?.catalog_version ?? null) : null,
    };
  },
}));
