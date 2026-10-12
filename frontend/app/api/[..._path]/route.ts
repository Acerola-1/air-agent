import { randomUUID } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import { getSessionUser } from "@/lib/auth";
import { signArtifactScope } from "@/lib/artifact-scope";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

async function handleRequest(req: NextRequest, method: string) {
  try {
    const user = await getSessionUser();
    if (!user) return NextResponse.json({ error: "请先登录" }, { status: 401 });
    const path = req.nextUrl.pathname.replace(/^\/?api\//, "");
    const base = process.env.LANGGRAPH_API_URL;
    if (!base) return NextResponse.json({ error: "对话服务地址未配置" }, { status: 503 });
    const segments = path.split("/");
    if (path === "threads" && method !== "POST") return NextResponse.json({ error: "请使用授权会话搜索" }, { status: 403 });
    if (!['threads', 'archives', 'assistants'].includes(segments[0])) {
      return NextResponse.json({ error: "此接口不可访问" }, { status: 403 });
    }
    // 助手为全局图定义，仅开放只读发现接口。
    if (segments[0] === "assistants" && method !== "GET" && !(method === "POST" && segments[1] === "search")) {
      return NextResponse.json({ error: "此操作不可访问" }, { status: 403 });
    }
    const threadId = ['threads', 'archives'].includes(segments[0]) && segments[1] && !['search', 'count'].includes(segments[1]) ? segments[1] : null;
    const upstreamHeaders: Record<string, string> = { "x-api-key": process.env.LANGCHAIN_API_KEY || "" };
    const contentType = req.headers.get("content-type");
    if (contentType) upstreamHeaders["content-type"] = contentType;
    if (threadId) {
      if (!/^[a-zA-Z0-9_-]+$/.test(threadId)) return NextResponse.json({ error: "会话标识无效" }, { status: 400 });
      const check = await fetch(`${base}/threads/${threadId}`, { headers: upstreamHeaders, cache: "no-store", signal: req.signal });
      if (!check.ok) return NextResponse.json({ error: "会话不可读取" }, { status: check.status });
      const thread = await check.json();
      if (thread.metadata?.user_id !== user.id) return NextResponse.json({ error: "无权访问此会话" }, { status: 403 });
      upstreamHeaders["x-artifact-scope"] = signArtifactScope(user.id, threadId);
    }
    let body: Record<string, unknown> | undefined;
    if (["POST", "PUT", "PATCH"].includes(method)) {
      const raw = await req.text();
      body = (raw ? JSON.parse(raw) : {}) as Record<string, unknown>;
      if (!body || typeof body !== "object" || Array.isArray(body)) throw new SyntaxError("无效请求");
      if (segments[0] === "threads" && (!threadId || segments.length === 2 && method === "PATCH")) {
        body.metadata = { ...(body.metadata as Record<string, unknown> ?? {}), user_id: user.id };
      }
      if (path === "threads" && typeof body.thread_id === "string") {
        const existing = await fetch(`${base}/threads/${encodeURIComponent(body.thread_id)}`, { headers: upstreamHeaders, cache: "no-store", signal: req.signal });
        if (existing.ok && (await existing.json()).metadata?.user_id !== user.id) {
          return NextResponse.json({ error: "会话标识不可使用" }, { status: 403 });
        }
        if (!existing.ok && existing.status !== 404) return NextResponse.json({ error: "无法确认会话状态" }, { status: existing.status });
      }
      if (threadId && method === "POST" && segments[2] === "runs" && segments.length <= 4 && !['cancel','join'].includes(segments[3] ?? "")) {
        const cfg = body.config as { configurable?: Record<string, unknown>; tags?: string[] } | undefined;
        body.config = { ...cfg, configurable: { ...cfg?.configurable, user_id: user.id, thread_id: threadId,
          __artifact_scope: upstreamHeaders["x-artifact-scope"], processing_run_id: randomUUID() } };
      }
    }
    if (method === "DELETE" && segments[0] === "threads" && threadId && segments.length === 2) {
      const cleanup = await fetch(`${base}/archives/${threadId}`, { method: "DELETE", headers: upstreamHeaders, signal: req.signal });
      if (!cleanup.ok) return NextResponse.json({ error: "资料清理失败，会话暂未删除，请重试" }, { status: 503 });
    }
    const params = new URLSearchParams(req.nextUrl.search);
    params.delete("_path"); params.delete("nxtP_path");
    const res = await fetch(`${base}/${path}${params.size ? `?${params}` : ""}`, {
      method, headers: upstreamHeaders, body: body ? JSON.stringify(body) : undefined, signal: req.signal, cache: "no-store",
    });
    const headers = new Headers(res.headers);
    for (const key of ["content-encoding", "content-length", "transfer-encoding", "set-cookie"]) headers.delete(key);
    headers.set("Cache-Control", "no-store");
    return new NextResponse(res.body, { status: res.status, headers });
  } catch (error) {
    if (error instanceof SyntaxError) return NextResponse.json({ error: "请求格式无效" }, { status: 400 });
    return NextResponse.json({ error: "对话服务暂不可用，请稍后重试" }, { status: 502 });
  }
}

export const GET = (req: NextRequest) => handleRequest(req, "GET");
export const POST = (req: NextRequest) => handleRequest(req, "POST");
export const PUT = (req: NextRequest) => handleRequest(req, "PUT");
export const PATCH = (req: NextRequest) => handleRequest(req, "PATCH");
export const DELETE = (req: NextRequest) => handleRequest(req, "DELETE");
export const OPTIONS = () => new NextResponse(null, { status: 204 });
