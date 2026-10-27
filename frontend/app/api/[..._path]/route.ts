import { type NextRequest, NextResponse } from "next/server";
import { AdminApiError, runtimeTicketViaAdmin } from "@/lib/admin-api";
import { getSessionContext } from "@/lib/auth";
import { signArtifactScope } from "@/lib/artifact-scope";
import {
  admitRequest,
  getRequestDetail,
  ledgerMarker,
  requestThreadDeletion,
} from "@/lib/agent-ledger";
import { confirmRunWithMarker, markAdmissionUnknown } from "@/lib/run-admission";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

/** 聊天代理：每次请求实时确认统一身份 → 内部端点换 60s 运行票据 → 携带票据转发。
 *
 * 安全边界（本 change 核心）：
 *  - 身份不来自浏览器自报：cookie → admin /auth/me（服务端验会话/企业/成员）；
 *  - 上游不再使用平台 API key：Authorization: Bearer <运行票据>，
 *    LangGraph custom auth 再实时核对会话/企业/成员并做资源归属过滤；
 *  - 线程创建强制注入可信 user_id/tenant_id（与票据身份一致的字段）；
 *  - 归档 scope 升级为 v2（租户维度），客户端无法伪造。
 */
async function handleRequest(req: NextRequest, method: string) {
  let ctx;
  try {
    ctx = await getSessionContext();
  } catch (error) {
    const status = typeof (error as { status?: unknown })?.status === "number" ? (error as { status: number }).status : 503;
    return NextResponse.json({ error: "身份服务暂不可用，请稍后重试" }, { status });
  }
  if (!ctx) {
    return NextResponse.json({ error: "请先登录" }, { status: 401 });
  }
  if (ctx.needs_tenant_selection) {
    return NextResponse.json({ error: "请先选择当前企业后再使用对话功能" }, { status: 403 });
  }
  if (ctx.membership?.status !== "active" || ctx.current_tenant?.status !== "active") {
    return NextResponse.json({ error: "当前企业成员身份无效或企业已停用" }, { status: 403 });
  }
  const path = req.nextUrl.pathname.replace(/^\/?api\//, "");
  const base = process.env.LANGGRAPH_API_URL;
  if (!base) return NextResponse.json({ error: "对话服务地址未配置" }, { status: 503 });

  const userId = ctx.user.id;
  const tenantId = ctx.current_tenant?.id ?? "";

  // R02：业务请求必须携带"页面感知的目标企业 + 上下文版本"（非授权性提示），
  // 与当前会话实时比对：缺失拒绝（客户端上下文丢失），不一致 409 context_changed
  // （旧标签页不得把写入悄悄落进新企业）。票据本身只授权会话当前企业。
  const claimedTenant = req.headers.get("x-tenant-id");
  const claimedVersion = req.headers.get("x-tenant-context-version");
  if (!claimedTenant || !claimedVersion) {
    return NextResponse.json(
      { error: "页面企业上下文缺失，请刷新后重试", code: "context_missing" },
      { status: 403 }
    );
  }
  if (claimedTenant !== tenantId || Number(claimedVersion) !== ctx.context_version) {
    return NextResponse.json(
      { error: "企业上下文已变更（可能在其他标签页切换），请刷新后重试", code: "context_changed" },
      { status: 409 }
    );
  }

  // R02 票据 TOCTOU：签发请求携带页面声称的目标企业；服务端（admin API）若
  // 已发生上下文变化会直接 409，代理不得伪造可用票据。
  const store = req.headers.get("cookie") ?? "";
  let ticket: string;
  try {
    const issued = await runtimeTicketViaAdmin(store, claimedTenant, Number(claimedVersion));
    if (issued.status === 409) {
      return NextResponse.json(
        { error: "企业上下文已变更（签发时已切换），请刷新后重试", code: "context_changed" },
        { status: 409 }
      );
    }
    if (issued.status === 403 || !issued.ticket) {
      return NextResponse.json(
        { error: issued.status === 403 ? "当前企业成员身份无效或企业已停用" : "对话凭据不可用" },
        { status: issued.status === 403 ? 403 : 503 }
      );
    }
    ticket = issued.ticket;
  } catch (err) {
    const status = err instanceof AdminApiError ? err.status : 503;
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "对话凭据获取失败" },
      { status: status === 401 ? 503 : status === 409 ? 409 : 503 }
    );
  }

  const segments = path.split("/");
  if (path === "threads" && method !== "POST") {
    return NextResponse.json({ error: "请使用授权会话搜索" }, { status: 403 });
  }
  if (!["threads", "archives", "assistants", "capabilities"].includes(segments[0])) {
    return NextResponse.json({ error: "此接口不可访问" }, { status: 403 });
  }
  // 助手与能力目录为只读发现接口（写入由 LangGraph auth 默认拒绝）。
  if (segments[0] === "assistants" && method !== "GET" && !(method === "POST" && segments[1] === "search")) {
    return NextResponse.json({ error: "此操作不可访问" }, { status: 403 });
  }
  if (segments[0] === "capabilities" && method !== "GET") {
    return NextResponse.json({ error: "此操作不可访问" }, { status: 403 });
  }

  const upstreamHeaders: Record<string, string> = {
    authorization: `Bearer ${ticket}`,
    "content-type": "application/json",
  };
  const contentType = req.headers.get("content-type");
  if (contentType) upstreamHeaders["content-type"] = contentType;

  const threadId =
    ["threads", "archives"].includes(segments[0]) && segments[1] && !["search", "count"].includes(segments[1])
      ? segments[1]
      : null;
  if (threadId && !/^[a-zA-Z0-9_-]+$/.test(threadId)) {
    return NextResponse.json({ error: "会话标识无效" }, { status: 400 });
  }
  if (threadId) {
    // 归属校验交给 LangGraph 资源级 auth（thread_ownership 为权威）；
    // 归档接口独立携带 v2 scope（自定义 app 不经平台认证中间件）。
    upstreamHeaders["x-artifact-scope"] = signArtifactScope(tenantId, userId, threadId);
  }

  let body: Record<string, unknown> | undefined;
  let mintedThreadId = "";
  let ledgerAdmission: {
    requestId: string;
    attemptId: string;
    submissionId: string;
  } | null = null;
  let admittedSelection: Record<string, unknown> | null = null;
  if (["POST", "PUT", "PATCH"].includes(method)) {
    const raw = await req.text();
    try {
      body = (raw ? JSON.parse(raw) : {}) as Record<string, unknown>;
    } catch {
      return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
    }
    if (segments[0] === "threads" && (!threadId || (segments.length === 2 && method === "PATCH"))) {
      const meta = (body.metadata as Record<string, unknown> | undefined) ?? {};
      // 客户端提交的同名字段一律覆盖为可信值（LangGraph auth 还会二次核对）
      const nextMeta: Record<string, unknown> = {
        ...meta,
        user_id: userId,
        tenant_id: tenantId,
      };
      // Langfuse 语义字段只在**写入路径**追加：search / count 的 metadata 是
      // "过滤条件"，混入新增键会使无这些键的历史线程全部失配而从列表消失。
      const isWritePath = segments[1] !== "search" && segments[1] !== "count";
      if (isWritePath) {
        // → trace 归属：Users 页按人聚合，Sessions 页按线程聚合。
        nextMeta.langfuse_user_id = userId;
        nextMeta.user_account = ctx.user.account;
        if (threadId) nextMeta.langfuse_session_id = threadId;
      }
      body.metadata = nextMeta;
    }
    // F01：线程创建标识由代理铸造（uuidv4），客户端不能指定/复用既有 ID——
    // "未知历史线程被严格创建吸收"的攻击面在入口即不存在；同时删除 if_exists，
    // 只允许原生默认严格语义。响应丢失重试只会再得到一个全新的空线程，不会与
    // 任何既有对象冲突。
    if (path === "threads" && method === "POST") {
      delete body.if_exists;
      mintedThreadId = crypto.randomUUID();
      body.thread_id = mintedThreadId;
      // Langfuse session = 会话线程：创建时 thread_id 刚确定，此处补写。
      const createdMeta = (body.metadata as Record<string, unknown> | undefined) ?? {};
      body.metadata = { ...createdMeta, langfuse_session_id: mintedThreadId };
    }
    if (threadId && method === "POST" && segments[2] === "runs" && segments.length <= 4 && !["cancel", "join"].includes(segments[3] ?? "")) {
      const cfg = body.config as { configurable?: Record<string, unknown>; tags?: string[]; metadata?: Record<string, unknown> } | undefined;
      // 台账可信受理必须先于平台运行创建：受理失败 → 503，不启动新运行。
      const submissionKey = req.headers.get("x-submission-key") ?? `auto-${crypto.randomUUID()}`;
      let operationKind = (req.headers.get("x-operation-kind") ?? "initial") as
        | "initial" | "retry" | "regenerate" | "resume";
      const userMessageId = req.headers.get("x-user-message-id");
      const firstUserMessage = extractFirstUserContent(body.input);
      const graphId = String(body.assistant_id ?? "");
      // 基础问答不提供技能/MCP 选择入口；旧页面或手工请求携带的选择一律
      // 丢弃，由后台强制归为系统自动路由。
      const rawCapabilitySelection =
        graphId === "basic-qa"
          ? undefined
          : ((body.capability_selection as Record<string, unknown> | undefined) ??
            (cfg?.configurable?.capability_selection as Record<string, unknown> | undefined) ??
            (req.headers.get("x-capability-selection")
              ? (() => {
                  try {
                    return JSON.parse(req.headers.get("x-capability-selection")!);
                  } catch {
                    return undefined;
                  }
                })()
              : undefined));
      try {
        // F06：正常提交必须留下**按 request 可精确回读**的原问题来源——否则刷新/
        // 后续提问/重新生成之后，热门问题分析只剩可变 thread 指针与摘要。
        // 只保存本次用户原问题文本（受控快照，带保留期），正文不进入普通事件与指标；
        // 空内容（如 resume）不落快照。保留期由部署配置，缺省 90 天。
        const retentionDays = Number(process.env.LEDGER_CONTENT_RETENTION_DAYS ?? "90");
        const menuName =
          (req.headers.get("x-menu-name") as string | null) ||
          ((body.menu_name as string | undefined) ?? null) ||
          ((cfg?.configurable?.menu_name as string | undefined) ?? null);
        const admitted = await admitRequest({
          cookie: store,
          threadId: threadId as string,
          submissionKey,
          graphId,
          operationKind,
          sourceAttemptId: req.headers.get("x-source-attempt-id"),
          content: firstUserMessage,
          userMessageId,
          storeContent: firstUserMessage.trim().length > 0,
          retentionDays:
            Number.isFinite(retentionDays) && retentionDays >= 1 ? retentionDays : 90,
          tenantId,
          contextVersion: Number(claimedVersion),
          menuName,
          capabilitySelection: rawCapabilitySelection,
        });
        operationKind = (admitted.operation_kind ?? operationKind) as typeof operationKind;
        admittedSelection = (admitted.capability_selection as Record<string, unknown> | null) ?? null;
        ledgerAdmission = {
          requestId: admitted.request_id,
          attemptId: admitted.attempt_id,
          submissionId: admitted.submission_id,
        };
      } catch (err) {
        const status = err instanceof AdminApiError ? err.status : 503;
        const errJson = err instanceof AdminApiError ? (err.json as { detail?: string; code?: string }) : undefined;
        const detail = errJson?.detail || (err instanceof Error ? err.message : String(err));
        const code = errJson?.code || (status === 409 ? "submission_conflict" : "ledger_admission_failed");
        return NextResponse.json(
          {
            error:
              detail ||
              (status === 409
                ? "该提交与已有内容不一致，请刷新后重试"
                : "台账受理失败，本次未启动运行，请稍后重试"),
            code: code,
          },
          { status }
        );
      }
      // F01：启动权由**原生入口**（平台 auth hook）单点原子裁决——那是唯一不可绕过的
      // 判定位。BFF 不再预先 claim：此前 BFF claim 成功后原生再 claim，导致合法首笔被
      // 409 拒绝、同键重试 202，正常业务不可用。
      // BFF 的职责：受理拿到 request/attempt/submission → 注入可信关联 → 转发。
      // 上游因台账裁决拒绝时，按**结构化 code** 区分处理（见下方 forward 分支）。

      const capMeta = admittedSelection
        ? {
            capability_selection_id:
              (admittedSelection.id as string | undefined) ??
              (admittedSelection.selection_id as string | undefined) ??
              "",
            capability_skill_mode:
              (admittedSelection.skill_mode as string | undefined) ??
              ((admittedSelection.skill as Record<string, unknown> | undefined)?.mode as string | undefined) ??
              "auto",
            capability_skill_id:
              (admittedSelection.skill_id as string | undefined) ??
              ((admittedSelection.skill as Record<string, unknown> | undefined)?.skill_id as string | undefined) ??
              null,
            capability_mcp_mode:
              (admittedSelection.mcp_mode as string | undefined) ??
              ((admittedSelection.mcp as Record<string, unknown> | undefined)?.mode as string | undefined) ??
              "auto",
            capability_tool_ids:
              (admittedSelection.tool_ids as string[] | undefined) ??
              ((admittedSelection.mcp as Record<string, unknown> | undefined)?.tool_ids as string[] | undefined) ??
              [],
            capability_selection_digest:
              (admittedSelection.selection_digest as string | undefined) ?? "",
          }
        : {};

      const marker = ledgerMarker(ledgerAdmission.requestId, ledgerAdmission.attemptId);
      const meta = (body.metadata as Record<string, unknown> | undefined) ?? {};
      body.metadata = {
        ...meta,
        user_account: ctx.user.account,
        ledger_request_id: ledgerAdmission.requestId,
        ledger_attempt_id: ledgerAdmission.attemptId,
        ledger_submission_id: ledgerAdmission.submissionId,
        ledger_submission_key: submissionKey,
        ledger_operation_kind: operationKind,
        ledger_graph_id: graphId,
        ledger_marker: marker,
        ...capMeta,
      };
      body.config = {
        ...cfg,
        // 可读账号只作 Langfuse metadata 维度；user_id 仍用稳定 UUID。
        // 覆盖客户端同名字段，避免浏览器伪造归属。
        metadata: {
          ...(cfg?.metadata ?? {}),
          user_account: ctx.user.account,
        },
        configurable: {
          ...cfg?.configurable,
          user_id: userId,
          tenant_id: tenantId,
          thread_id: threadId,
          __artifact_scope: upstreamHeaders["x-artifact-scope"],
          processing_run_id: crypto.randomUUID(),
          ledger_request_id: ledgerAdmission.requestId,
          ledger_attempt_id: ledgerAdmission.attemptId,
          ledger_submission_id: ledgerAdmission.submissionId,
          ledger_operation_kind: operationKind,
          ledger_graph_id: String(body.assistant_id ?? ""),
          ...capMeta,
        },
      };
    }
  }

  if (method === "DELETE" && segments[0] === "threads" && threadId && segments.length === 2) {
    // R05：真实删除流程必须先落**持久删除标记**（立即禁读/禁新写），
    // 再清理归档并删除原生线程；标记失败则不继续删除（避免"删了但没登记"）。
    try {
      await requestThreadDeletion({
        cookie: store,
        threadId,
        tenantContext: { tenantId, contextVersion: Number(claimedVersion) },
        requestOrigin: req.headers.get("origin"),
      });
    } catch {
      return NextResponse.json(
        { error: "会话删除登记失败，暂未删除，请重试", code: "deletion_marking_failed" },
        { status: 503 }
      );
    }
    const cleanup = await fetch(`${base}/archives/${threadId}`, {
      method: "DELETE",
      headers: upstreamHeaders,
      signal: req.signal,
    });
    if (!cleanup.ok) {
      return NextResponse.json({ error: "资料清理失败，会话暂未删除，请重试" }, { status: 503 });
    }
  }

  const params = new URLSearchParams(req.nextUrl.search);
  params.delete("_path");
  params.delete("nxtP_path");
  let res: Response;
  try {
    res = await fetch(`${base}/${path}${params.size ? `?${params}` : ""}`, {
      method,
      headers: upstreamHeaders,
      body: body ? JSON.stringify(body) : undefined,
      signal: req.signal,
      cache: "no-store",
    });
  } catch (err) {
    // F01：**上游结果不确定**（连接中断/超时）与"准入失败"必须分开处理：
    // 启动权可能在原生入口已被消费并真的创建了运行，因此不能谎报失败、也不能
    // 重发；如实登记"待确认"，由有界补偿按平台可信 metadata 定位既有运行后收敛；
    // 定位不到且超过宽限期才释放启动权（见 ledger reconcile）。
    if (ledgerAdmission) {
      await markAdmissionUnknown({
        cookie: store,
        requestId: ledgerAdmission.requestId,
        attemptId: ledgerAdmission.attemptId,
        submissionId: ledgerAdmission.submissionId,
        reason: "upstream_unreachable",
      }).catch(() => undefined);
      return NextResponse.json(
        {
          status: "admission_unknown",
          code: "run_admission_unknown",
          ledger_request_id: ledgerAdmission.requestId,
          ledger_attempt_id: ledgerAdmission.attemptId,
          error: "本次提交已受理但上游结果未知；未重发，等待确认后再试。",
        },
        { status: 202 }
      );
    }
    void err;
    return NextResponse.json(
      { error: "上游服务暂不可用", code: "upstream_unavailable" },
      { status: 503 }
    );
  }
  // 墓碑和资料清理已经成功后，平台线程不存在就是删除完成，重复删除保持成功。
  if (
    method === "DELETE" &&
    segments[0] === "threads" &&
    threadId &&
    segments.length === 2 &&
    res.status === 404
  ) {
    return new NextResponse(null, { status: 204 });
  }
  if (res.status === 401 || res.status === 403) {
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "cache-control": "no-store", "content-type": "application/json" },
    });
  }
  if (ledgerAdmission && res.status === 409) {
    // 原生启动权裁决的结构化拒绝：只有**明确的 code** 才进入复用/待确认分支；
    // 文本型 409 一律原样透传（绝不把任意 409 当作"上次成功"）。
    const cloned = res.clone();
    let code = "";
    let reusedRunId = "";
    try {
      const payload = (await cloned.json()) as {
        detail?: { code?: string; platform_run_id?: string };
      };
      code = String(payload?.detail?.code ?? "");
      reusedRunId = String(payload?.detail?.platform_run_id ?? "");
    } catch {
      code = "";
    }
    if (code === "run_already_confirmed") {
      // 既有运行：以**台账**为准取回 run_id 复用，不重复创建业务运行
      let runId = reusedRunId;
      if (!runId) {
        const detail = await getRequestDetail({
          cookie: store,
          requestId: ledgerAdmission.requestId,
          tenantId,
          contextVersion: Number(claimedVersion),
        });
        runId =
          detail?.attempts?.find((item) => item.platform_run_id)?.platform_run_id ?? "";
      }
      if (runId) {
        return NextResponse.json(
          {
            status: "already_confirmed",
            run_id: runId,
            thread_id: threadId,
            ledger_request_id: ledgerAdmission.requestId,
            ledger_attempt_id: ledgerAdmission.attemptId,
          },
          { status: 200, headers: { "cache-control": "no-store" } }
        );
      }
      return NextResponse.json(
        {
          status: "admission_unknown",
          code: "run_admission_unknown",
          ledger_request_id: ledgerAdmission.requestId,
          ledger_attempt_id: ledgerAdmission.attemptId,
          error: "该提交已有运行但映射待确认；未重发。",
        },
        { status: 202 }
      );
    }
    if (code === "run_already_started") {
      // 启动权已消费但尚无运行映射：不盲发，如实待确认
      return NextResponse.json(
        {
          status: "admission_unknown",
          code: "run_admission_unknown",
          ledger_request_id: ledgerAdmission.requestId,
          ledger_attempt_id: ledgerAdmission.attemptId,
          error: "本次提交已在受理中，等待确认后再重试；未重复执行。",
        },
        { status: 202 }
      );
    }
    if (code === "run_request_mismatch" || code === "run_attempt_terminal") {
      return NextResponse.json(
        { error: "本次运行请求与台账预留不一致，已拒绝", code },
        { status: 409 }
      );
    }
  }

  // F01：确认只发生在**平台返回 2xx 的本次铸造线程**上（创建事实=代理亲眼
  // 所见的成功响应 + 同票据会话的 pending 认领被服务端校验升级）。409/403/
  // 5xx/超时一律不确认——"任意 409 当作上次成功"正是被复验证伪的补偿分支，
  // 已删除；铸造 ID 使该分支不可能对既有对象成立。确认失败重试后仍失败则
  // 503（线程保持 pending：不可读、不可运行，用户重试得到全新会话）。
  if (mintedThreadId && res.status >= 200 && res.status < 300) {
    let confirmed = false;
    for (let attempt = 0; attempt < 3 && !confirmed; attempt++) {
      confirmed = await confirmThreadClaim(base, ticket, mintedThreadId, req.signal);
      if (!confirmed) await new Promise((resolve) => setTimeout(resolve, 300 * (attempt + 1)));
    }
    if (!confirmed) {
      return NextResponse.json(
        { error: "会话归属确认未完成，请重试", code: "claim_confirmation_failed" },
        { status: 503 }
      );
    }
  }

  const headers = new Headers(res.headers);
  for (const key of ["content-encoding", "content-length", "transfer-encoding", "set-cookie"]) {
    headers.delete(key);
  }
  headers.set("Cache-Control", "no-store");
  if (ledgerAdmission) {
    // 非秘密关联标记回传客户端：流式首个 metadata 事件后由客户端上报 run_id 确认。
    headers.set(
      "x-ledger-marker",
      ledgerMarker(ledgerAdmission.requestId, ledgerAdmission.attemptId)
    );
    if (admittedSelection) {
      headers.set("x-capability-selection", JSON.stringify(admittedSelection));
    }
  }

  // 台账受理结果回填。平台不提供调用方指定 run_id（任务 1.2 实测），因此：
  //  - 非流式 JSON 响应：直接取 run_id，校验平台 run metadata 携带本次台账标记后确认；
  //  - 流式（SSE）：代理不消费响应体，交由 `/api/agent-runs/confirm`（客户端拿到
  //    首个 metadata 事件后上报 run_id）完成确认；未确认前保持"受理中"，
  //    **绝不**自动重发创建。
  if (ledgerAdmission && res.status >= 200 && res.status < 300) {
    const contentType = res.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      const text = await res.text();
      let runId = "";
      try {
        runId = String((JSON.parse(text) as { run_id?: string }).run_id ?? "");
      } catch {
        runId = "";
      }
      const confirmed = runId
        ? await confirmRunWithMarker({
            base,
            ticket,
            cookie: store,
            threadId: threadId as string,
            runId,
            marker: ledgerMarker(ledgerAdmission.requestId, ledgerAdmission.attemptId),
            requestId: ledgerAdmission.requestId,
            attemptId: ledgerAdmission.attemptId,
            submissionId: ledgerAdmission.submissionId,
          })
        : false;
      if (!confirmed) {
        await markAdmissionUnknown({
          cookie: store,
          requestId: ledgerAdmission.requestId,
          attemptId: ledgerAdmission.attemptId,
          submissionId: ledgerAdmission.submissionId,
          reason: runId ? "marker_mismatch" : "missing_run_id",
        });
      }
      return new NextResponse(text, { status: res.status, headers });
    }
    // 流式：不消费响应体；由客户端上报 run_id 后确认
    return new NextResponse(res.body, { status: res.status, headers });
  }

  return new NextResponse(res.body, { status: res.status, headers });
}


/** 提取首条用户消息文本（仅用于规范摘要，不落台账事件载荷）。 */
function extractFirstUserContent(input: unknown): string {
  const messages = (input as { messages?: unknown } | undefined)?.messages;
  if (!Array.isArray(messages)) return "";
  for (const message of messages) {
    const item = message as { type?: string; role?: string; content?: unknown };
    const isUser = item?.type === "human" || item?.role === "user";
    if (!isUser) continue;
    if (typeof item.content === "string") return item.content;
    if (Array.isArray(item.content)) {
      const text = item.content
        .map((part) => (typeof part === "string" ? part : ((part as { text?: string })?.text ?? "")))
        .join("");
      if (text) return text;
    }
  }
  return "";
}

/** 调用原生服务上的受限内部端点确认认领；任何非 confirmed 结果都返回 false。
 * 作用域/会话由端点从已验票据推导（F01），请求体只带 thread_id。 */
async function confirmThreadClaim(
  base: string,
  ticket: string,
  threadId: string,
  signal: AbortSignal
): Promise<boolean> {
  const internal = process.env.INTERNAL_API_TOKEN ?? "";
  if (!internal) return false;
  try {
    const res = await fetch(`${base}/internal/threads/claim-confirmation`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${ticket}`,
        "x-internal-token": internal,
        "content-type": "application/json",
      },
      body: JSON.stringify({ thread_id: threadId }),
      cache: "no-store",
      signal,
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { confirmed?: boolean };
    return data.confirmed === true;
  } catch {
    return false;
  }
}

export const GET = (req: NextRequest) => handleRequest(req, "GET");
export const POST = (req: NextRequest) => handleRequest(req, "POST");
export const PUT = (req: NextRequest) => handleRequest(req, "PUT");
export const PATCH = (req: NextRequest) => handleRequest(req, "PATCH");
export const DELETE = (req: NextRequest) => handleRequest(req, "DELETE");
export const OPTIONS = () => new NextResponse(null, { status: 204 });
