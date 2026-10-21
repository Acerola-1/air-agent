/**
 * agent-output-stream-v1 client consumer.
 *
 * The server publishes answer text only through this lifecycle.  The consumer
 * binds events to one run/output, enforces contiguous sequence numbers, ignores
 * replayed frames, dedupes DeepAgents section replays and merges `done` into the
 * same answer instead of appending a second one.  A gap, a conflicting duplicate
 * or a done whose text does not equal the accumulated deltas triggers a trusted
 * snapshot re-read (`resync`) rather than blind concatenation.
 */

export type FinalOutputEvent = {
  type: "final_output_start" | "final_output_delta" | "final_output_done" | "final_output_aborted";
  output_protocol_version?: number;
  output_id?: string;
  message_id?: string;
  thread_id?: string;
  run_id?: string;
  graph_id?: string;
  tenant_id?: string;
  seq?: number;
  delivery_mode?: string;
  message?: string;
  content_sha256?: string;
  reason_code?: string;
  partial_chars?: number;
  section_id?: string;
  plan_digest?: string;
  replay?: boolean;
  report_id?: string;
  revision?: number;
};

export type OutputAction =
  | { action: "ignore"; reason?: string }
  | { action: "start"; outputId: string; messageId: string; deliveryMode: string }
  | { action: "append"; text: string; seq: number; sectionId?: string }
  | { action: "done"; text: string; messageId: string; outputId: string; seq: number }
  | { action: "abort"; text: string; reason: string; outputId: string; seq: number }
  | { action: "legacy"; text: string; messageId: string }
  | { action: "resync"; reason: string };

export type OutputScope = {
  threadId?: string | null;
  runId?: string | null;
  graphId?: string | null;
};

export type OutputSnapshot = {
  outputId: string | null;
  messageId: string | null;
  lastSeq: number;
  text: string;
  deliveryMode: string | null;
  status: "idle" | "streaming" | "done" | "aborted" | "legacy";
  sections: string[];
  firstDeltaSource: "new_delta" | "legacy_done" | null;
};

export function isFinalOutputEvent(value: unknown): value is FinalOutputEvent {
  if (!value || typeof value !== "object") return false;
  const type = (value as { type?: unknown }).type;
  return (
    type === "final_output_start" ||
    type === "final_output_delta" ||
    type === "final_output_done" ||
    type === "final_output_aborted"
  );
}

export class AgentOutputStream {
  private scope: OutputScope;
  private outputId: string | null = null;
  private messageId: string | null = null;
  private lastSeq = -1;
  private lastDelta: string | null = null;
  private text = "";
  private deliveryMode: string | null = null;
  private status: OutputSnapshot["status"] = "idle";
  private sections = new Set<string>();
  private firstDeltaSource: OutputSnapshot["firstDeltaSource"] = null;

  constructor(scope: OutputScope = {}) {
    this.scope = scope;
  }

  /** Reset for a new submission/thread. Old output ids can no longer mutate this. */
  reset(scope: OutputScope = {}): void {
    this.scope = scope;
    this.outputId = null;
    this.messageId = null;
    this.lastSeq = -1;
    this.lastDelta = null;
    this.text = "";
    this.deliveryMode = null;
    this.status = "idle";
    this.sections = new Set();
    this.firstDeltaSource = null;
  }

  /** Update the trusted run identity after the platform returns the run id. */
  setScope(scope: Partial<OutputScope>): void {
    this.scope = { ...this.scope, ...scope };
  }

  snapshot(): OutputSnapshot {
    return {
      outputId: this.outputId,
      messageId: this.messageId,
      lastSeq: this.lastSeq,
      text: this.text,
      deliveryMode: this.deliveryMode,
      status: this.status,
      sections: [...this.sections],
      firstDeltaSource: this.firstDeltaSource,
    };
  }

  apply(event: FinalOutputEvent): OutputAction {
    // Legacy done-only frames have no protocol version. They may complete the
    // UI but are labelled legacy and never count as new-protocol streaming.
    if (
      event.type === "final_output_done" &&
      event.output_protocol_version === undefined &&
      event.output_id === undefined
    ) {
      const message = typeof event.message === "string" ? event.message : "";
      const messageId = typeof event.message_id === "string" ? event.message_id : "";
      this.status = "legacy";
      this.text = message;
      this.messageId = messageId || null;
      this.firstDeltaSource = "legacy_done";
      return { action: "legacy", text: message, messageId };
    }

    if (event.output_protocol_version !== 1) {
      return { action: "resync", reason: "unsupported_protocol" };
    }
    if (event.thread_id && this.scope.threadId && event.thread_id !== this.scope.threadId) {
      return { action: "ignore", reason: "other_thread" };
    }
    if (event.run_id && this.scope.runId && event.run_id !== this.scope.runId) {
      return { action: "ignore", reason: "other_run" };
    }
    // Graph switches always create a new thread/run; thread+run+output are the
    // isolation keys. A stale graph label must not hide a valid run's deltas.

    const outputId = typeof event.output_id === "string" ? event.output_id : "";
    const messageId = typeof event.message_id === "string" ? event.message_id : "";

    if (event.type === "final_output_start") {
      if (!outputId || !messageId) return { action: "resync", reason: "missing_identity" };
      // A fixed failure message may replace an aborted output only if no body
      // was ever published. Partial answers must keep their terminal identity.
      if (this.status === "aborted" && !this.text && event.delivery_mode === "single_shot") {
        this.reset(this.scope);
      }
      if (this.outputId && this.outputId !== outputId) {
        return { action: "resync", reason: "output_changed" };
      }
      if (this.status === "done" || this.status === "aborted") {
        return { action: "ignore", reason: "terminal_already" };
      }
      if (this.status === "streaming") return { action: "ignore", reason: "duplicate_start" };
      this.outputId = outputId;
      this.messageId = messageId;
      this.lastSeq = typeof event.seq === "number" ? event.seq : 0;
      this.deliveryMode = typeof event.delivery_mode === "string" ? event.delivery_mode : null;
      this.status = "streaming";
      this.text = "";
      this.sections = new Set();
      return {
        action: "start",
        outputId,
        messageId,
        deliveryMode: this.deliveryMode ?? "unknown",
      };
    }

    if (!this.outputId || outputId !== this.outputId) {
      return { action: "ignore", reason: "unknown_output" };
    }
    if (this.status === "done" || this.status === "aborted") {
      return { action: "ignore", reason: "terminal_already" };
    }
    if (typeof event.seq !== "number") {
      return { action: "resync", reason: "missing_seq" };
    }

    if (event.type === "final_output_delta") {
      const message = typeof event.message === "string" ? event.message : "";
      if (!message) return { action: "ignore", reason: "empty_delta" };
      if (event.seq <= this.lastSeq) {
        if (event.seq === this.lastSeq && message === this.lastDelta) {
          return { action: "ignore", reason: "duplicate_frame" };
        }
        return { action: "resync", reason: "conflicting_replay" };
      }
      if (event.seq !== this.lastSeq + 1) {
        return { action: "resync", reason: "sequence_gap" };
      }
      this.lastSeq = event.seq;
      this.lastDelta = message;
      if (event.section_id && this.sections.has(event.section_id)) {
        if (event.replay) return { action: "ignore", reason: "section_already_delivered" };
        return { action: "resync", reason: "section_conflict" };
      }
      if (event.section_id) this.sections.add(event.section_id);
      if (!this.firstDeltaSource) this.firstDeltaSource = "new_delta";
      this.text += message;
      return {
        action: "append",
        text: message,
        seq: event.seq,
        sectionId: event.section_id,
      };
    }

    if (event.type === "final_output_done") {
      const message = typeof event.message === "string" ? event.message : "";
      if (event.seq !== this.lastSeq + 1) {
        return { action: "resync", reason: "done_sequence_gap" };
      }
      if (message !== this.text) {
        return { action: "resync", reason: "done_text_mismatch" };
      }
      this.lastSeq = event.seq;
      this.status = "done";
      this.text = message;
      return {
        action: "done",
        text: message,
        messageId: this.messageId ?? messageId,
        outputId: this.outputId,
        seq: event.seq,
      };
    }

    // final_output_aborted
    if (event.seq !== this.lastSeq + 1) {
      return { action: "resync", reason: "abort_sequence_gap" };
    }
    this.lastSeq = event.seq;
    this.status = "aborted";
    return {
      action: "abort",
      text: this.text,
      reason: typeof event.reason_code === "string" ? event.reason_code : "unknown",
      outputId: this.outputId,
      seq: event.seq,
    };
  }
}
