import "server-only";
import { confirmAdmission } from "@/lib/agent-ledger";

/** 运行受理确认的服务端辅助（普通模块，**不是** Next Route 导出）。
 *
 * Next.js 的 Route 文件只允许约定导出（HTTP 方法 + 少量配置）；在 Route 文件里
 * 额外 `export` 函数会让干净生产构建的类型校验报 TS2344。这里承载共享逻辑，
 * Route 只导入使用。
 */

/** 回读平台 run，确认其 metadata 携带本次台账标记后才回填受理结果。
 *
 * 不轻信调用方给的 run_id：只有平台侧 metadata 与本次 request/attempt 一致才确认。
 */
export async function confirmRunWithMarker(input: {
  base: string;
  ticket: string;
  cookie: string;
  threadId: string;
  runId: string;
  marker: string;
  requestId: string;
  attemptId: string;
  submissionId?: string | null;
}): Promise<boolean> {
  try {
    const res = await fetch(
      `${input.base}/threads/${input.threadId}/runs/${input.runId}`,
      { headers: { authorization: `Bearer ${input.ticket}` }, cache: "no-store" }
    );
    if (!res.ok) return false;
    const data = (await res.json()) as {
      run_id?: string;
      metadata?: Record<string, unknown>;
    };
    const matches =
      data.metadata?.ledger_request_id === input.requestId &&
      data.metadata?.ledger_attempt_id === input.attemptId;
    if (!matches) return false;
    await confirmAdmission({
      cookie: input.cookie,
      requestId: input.requestId,
      attemptId: input.attemptId,
      submissionId: input.submissionId ?? null,
      outcome: "confirmed",
      platformRunId: String(data.run_id ?? input.runId),
    });
    return true;
  } catch {
    return false;
  }
}

/** 标记受理结果待确认（不冒充成功、不自动重发）。 */
export async function markAdmissionUnknown(input: {
  cookie: string;
  requestId: string;
  attemptId: string;
  submissionId?: string | null;
  reason: string;
}): Promise<void> {
  try {
    await confirmAdmission({
      cookie: input.cookie,
      requestId: input.requestId,
      attemptId: input.attemptId,
      submissionId: input.submissionId ?? null,
      outcome: "admission_unknown",
      platformRunId: null,
      reason: input.reason,
    });
  } catch {
    // 保持"受理中"；校准流程后续处理。不回退为成功。
  }
}
