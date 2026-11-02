import { type NextRequest, NextResponse } from "next/server";
import { getSessionContext } from "@/lib/auth";
import { adminFetch, runtimeTicketViaAdmin } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

const UUID_RE = /^[0-9a-fA-F-]{36}$/;

type PlatformRun = {
  run_id?: string;
  status?: string;
  metadata?: Record<string, unknown>;
};

/** 审批等待中的运行校准（BFF → 平台只读 → 台账 waiting_input）。
 *
 * interrupt 之前不能写状态；平台把运行置为 `interrupted` 后，前台调用本入口：
 *  1. 用本人运行票据读取平台 run 的真实状态与 metadata；
 *  2. 只接受 `interrupted`；
 *  3. 用 run metadata 的台账关联（或 admin 已登记映射）定位 attempt；
 *  4. 以内部令牌把 attempt 幂等收敛到 `waiting_input`，供 resume 受理复用。
 *
 * 客户端不能指定 attempt/state；run_id 缺省时只在该线程最近运行里找 interrupted。
 */
export async function POST(req: NextRequest) {
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch (error) {
    const status = typeof (error as { status?: unknown })?.status === "number"
      ? (error as { status: number }).status
      : 503;
    return NextResponse.json({ error: "身份服务暂不可用，请稍后重试" }, { status });
  }
  if (!ctx) return NextResponse.json({ error: "请先登录" }, { status: 401 });
  if (ctx.membership?.status !== "active" || ctx.current_tenant?.status !== "active") {
    return NextResponse.json({ error: "当前企业成员身份无效" }, { status: 403 });
  }

  let body: { thread_id?: string; run_id?: string | null };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
  }
  const threadId = String(body.thread_id ?? "");
  const requestedRunId = body.run_id == null ? "" : String(body.run_id);
  if (!/^[a-zA-Z0-9_-]+$/.test(threadId) || (requestedRunId && !/^[a-zA-Z0-9_-]+$/.test(requestedRunId))) {
    return NextResponse.json({ error: "关联参数不完整" }, { status: 400 });
  }
  const base = process.env.LANGGRAPH_API_URL;
  if (!base) return NextResponse.json({ error: "对话服务地址未配置" }, { status: 503 });
  const store = req.headers.get("cookie") ?? "";
  let ticket = "";
  try {
    const issued = await runtimeTicketViaAdmin(store, ctx.current_tenant?.id ?? "", ctx.context_version);
    ticket = issued.ticket ?? "";
  } catch {
    ticket = "";
  }
  if (!ticket) return NextResponse.json({ error: "对话凭据不可用" }, { status: 503 });

  async function readRun(runId: string): Promise<PlatformRun | null> {
    try {
      const res = await fetch(
        `${base}/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}`,
        { headers: { authorization: `Bearer ${ticket}` }, cache: "no-store" },
      );
      if (!res.ok) return null;
      return (await res.json()) as PlatformRun;
    } catch {
      return null;
    }
  }

  /** 审批中断的权威证据在**线程状态**：`next` 指向 gate 且 tasks 带审批 payload。
   *
   * 平台对"运行停在 interrupt"可能返回 `success`（运行正常结束在中断点），
   * 不能只按 run.status 判断，否则会把可恢复等待误判为已完成。
   */
  async function hasPendingApproval(): Promise<boolean> {
    try {
      const res = await fetch(
        `${base}/threads/${encodeURIComponent(threadId)}/state`,
        { headers: { authorization: `Bearer ${ticket}` }, cache: "no-store" },
      );
      if (!res.ok) return false;
      const state = (await res.json()) as {
        next?: unknown;
        tasks?: { interrupts?: { value?: unknown }[] }[];
        interrupts?: { value?: unknown }[];
      };
      const next = Array.isArray(state.next) ? state.next.map(String) : [];
      if (!next.includes("websearch_gate")) return false;
      const candidates: unknown[] = [];
      for (const task of state.tasks ?? []) {
        for (const item of task.interrupts ?? []) candidates.push(item?.value);
      }
      for (const item of state.interrupts ?? []) candidates.push(item?.value);
      return candidates.some(
        (value) =>
          typeof value === "object" &&
          value !== null &&
          (value as { type?: unknown }).type === "websearch_approval",
      );
    } catch {
      return false;
    }
  }

  const pendingApproval = await hasPendingApproval();
  if (!pendingApproval) {
    return NextResponse.json({ confirmed: false, reason: "no_pending_approval" });
  }

  let run: PlatformRun | null = requestedRunId ? await readRun(requestedRunId) : null;
  if (!run) {
    try {
      const res = await fetch(
        `${base}/threads/${encodeURIComponent(threadId)}/runs?limit=5`,
        { headers: { authorization: `Bearer ${ticket}` }, cache: "no-store" },
      );
      if (res.ok) {
        const rows = (await res.json()) as PlatformRun[];
        // 审批中断的运行可能已结束（success）；取最近一条运行即可。
        run = (Array.isArray(rows) ? rows : [])[0] ?? null;
      }
    } catch {
      run = null;
    }
  }
  if (!run) {
    return NextResponse.json({ confirmed: false, reason: "run_missing" });
  }
  const runId = String(run.run_id ?? requestedRunId);
  if (!runId) return NextResponse.json({ confirmed: false, reason: "run_missing" });

  const resolved = await adminFetch(
    `/agent/feedback/attempts/resolve?thread_id=${encodeURIComponent(threadId)}&platform_run_id=${encodeURIComponent(runId)}`,
    { cookie: store },
  );
  let attemptId = "";
  if (resolved.status === 200) {
    const payload = resolved.json as { attempt_id?: unknown };
    attemptId = typeof payload.attempt_id === "string" ? payload.attempt_id : "";
  }
  if (!attemptId) {
    const metadataAttempt = run.metadata?.ledger_attempt_id;
    if (typeof metadataAttempt === "string" && UUID_RE.test(metadataAttempt)) {
      attemptId = metadataAttempt;
    }
  }
  if (!attemptId || !UUID_RE.test(attemptId)) {
    return NextResponse.json({ confirmed: false, reason: "attempt_not_found" });
  }

  const internal = process.env.INTERNAL_API_TOKEN ?? "";
  if (!internal) return NextResponse.json({ error: "内部凭据未配置" }, { status: 503 });
  const observed = await adminFetch(`/agent/runs/${encodeURIComponent(attemptId)}/state`, {
    method: "POST",
    cookie: store,
    internalToken: internal,
    body: {
      state: "waiting_input",
      evidence_source: "platform_read",
      platform_status: "interrupted",
      state_revision: null,
    },
  });
  if (observed.status !== 200) {
    return NextResponse.json({ confirmed: false, reason: "state_update_failed" });
  }
  return NextResponse.json({ confirmed: true, attempt_id: attemptId });
}
