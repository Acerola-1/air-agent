/** 事件台账 v1 契约校验（与 `src/common/event_ledger/contract.py` 同规则）。
 *
 * 契约单一事实源是 `docs/contracts/agent-events-v1/*.json`；浏览器运行时不允许
 * 直接读仓库文件，因此用 `contract-data.ts`（由
 * `frontend/scripts/gen-agent-events-contract.mjs` 从事实源生成）携带只读副本，
 * 并由契约测试断言副本与事实源一致（漂移即失败）。
 */

export type EventRegistry = {
  contract: string;
  major_version: number;
  schema_version: string;
  sources: Record<
    string,
    { trust: string; may_claim_terminal_facts: boolean; [k: string]: unknown }
  >;
  envelope: {
    required: string[];
    optional: string[];
    server_overrides_client: string[];
    forbidden_payload_key_patterns: string[];
    limits: {
      single_event_bytes: number;
      browser_batch_events: number;
      browser_batch_bytes: number;
      server_single_event_bytes: number;
    };
  };
  event_types: Record<
    string,
    {
      enabled: boolean;
      sources: string[];
      family: string;
      payload_fields: string[];
      required_payload_fields: string[];
      reserved_for?: string;
      summary: string;
    }
  >;
};

export type StatesContract = {
  states: Record<string, { terminal: boolean; counts_as_ended: boolean; zh: string }>;
  evidence_sources: Record<string, { rank: number; zh: string }>;
  cancel_origins: Record<string, string>;
  cancel_phases: Record<string, string>;
};

export type ValidationResult = { valid: boolean; reason: string; detail?: string };

export const OPERATION_KINDS = ['initial', 'retry', 'regenerate', 'resume'] as const;

const UUID_RE =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const TS_Z_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const ENUM_FIELDS = ['state', 'origin', 'phase', 'evidence_source', 'operation_kind'] as const;

function* iterKeys(node: unknown): Generator<string> {
  if (Array.isArray(node)) {
    for (const item of node) yield* iterKeys(item);
  } else if (node && typeof node === 'object') {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      yield key;
      yield* iterKeys(value);
    }
  }
}

function enumValues(states: StatesContract): Record<string, readonly string[]> {
  return {
    state: Object.keys(states.states),
    origin: Object.keys(states.cancel_origins),
    phase: Object.keys(states.cancel_phases),
    evidence_source: Object.keys(states.evidence_sources),
    operation_kind: OPERATION_KINDS,
  };
}

/** 与服务端一致的首个命中原因码判定。 */
export function validateEvent(
  event: unknown,
  registry: EventRegistry,
  states: StatesContract,
): ValidationResult {
  const env = registry.envelope;
  if (!event || typeof event !== 'object' || Array.isArray(event)) {
    return { valid: false, reason: 'missing_field', detail: 'event must be an object' };
  }
  const ev = event as Record<string, unknown>;

  const version = ev.schema_version;
  if (typeof version !== 'string' || !version.startsWith('1.')) {
    return { valid: false, reason: 'schema_version_unsupported', detail: String(version) };
  }
  for (const field of env.required) {
    if (ev[field] === undefined || ev[field] === null) {
      return { valid: false, reason: 'missing_field', detail: field };
    }
  }
  for (const field of ['event_id', 'tenant_id', 'user_id', 'membership_id']) {
    if (typeof ev[field] !== 'string' || !UUID_RE.test(ev[field] as string)) {
      return { valid: false, reason: 'bad_uuid', detail: field };
    }
  }
  if (typeof ev.occurred_at !== 'string' || !TS_Z_RE.test(ev.occurred_at)) {
    return { valid: false, reason: 'bad_timestamp', detail: 'occurred_at' };
  }
  const eventType = String(ev.event_type);
  const entry = registry.event_types[eventType];
  if (!entry) return { valid: false, reason: 'unknown_type', detail: eventType };
  if (!entry.enabled) return { valid: false, reason: 'type_not_enabled', detail: eventType };

  const source = String(ev.source ?? '');
  if (!entry.sources.includes(source)) {
    return { valid: false, reason: 'source_not_allowed', detail: `source=${source}` };
  }
  const payload = ev.payload;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return { valid: false, reason: 'missing_field', detail: 'payload' };
  }
  const p = payload as Record<string, unknown>;

  for (const key of iterKeys(p)) {
    const low = key.toLowerCase();
    if (env.forbidden_payload_key_patterns.some((pat) => low.includes(pat))) {
      return { valid: false, reason: 'forbidden_payload_key', detail: key };
    }
  }
  const allowed = new Set(entry.payload_fields);
  for (const key of Object.keys(p)) {
    if (!allowed.has(key)) {
      return { valid: false, reason: 'payload_field_not_allowed', detail: key };
    }
  }
  for (const field of entry.required_payload_fields) {
    if (p[field] === undefined || p[field] === null) {
      return { valid: false, reason: 'missing_required_payload_field', detail: field };
    }
  }
  const enums = enumValues(states);
  for (const field of ENUM_FIELDS) {
    if (field in p) {
      const value = String(p[field]);
      if (!enums[field].includes(value)) {
        return { valid: false, reason: 'invalid_enum_value', detail: `${field}=${value}` };
      }
    }
  }
  const size = new TextEncoder().encode(JSON.stringify(ev)).length;
  if (size > env.limits.single_event_bytes) {
    return { valid: false, reason: 'payload_too_large', detail: `${size}` };
  }
  return { valid: true, reason: 'ok' };
}

/** 批量校验：条数与整批字节上限 + 逐项结果（不宣称整批成功）。 */
export function validateBatch(
  events: unknown,
  source: string,
  registry: EventRegistry,
  states: StatesContract,
): { accepted: boolean; batch_reason: string; items: ValidationResult[] } {
  if (!Array.isArray(events) || events.length === 0) {
    return { accepted: false, batch_reason: 'empty_batch', items: [] };
  }
  const items = events.map((e) => validateEvent(e, registry, states));
  if (source === 'browser') {
    if (events.length > registry.envelope.limits.browser_batch_events) {
      return { accepted: false, batch_reason: 'batch_too_many', items };
    }
    const total = new TextEncoder().encode(JSON.stringify(events)).length;
    if (total > registry.envelope.limits.browser_batch_bytes) {
      return { accepted: false, batch_reason: `batch_too_large:${total}`, items };
    }
  }
  return { accepted: items.every((i) => i.valid), batch_reason: 'ok', items };
}

export function enabledEventTypes(registry: EventRegistry): string[] {
  return Object.entries(registry.event_types)
    .filter(([, v]) => v.enabled)
    .map(([k]) => k);
}

export function reservedEventTypes(registry: EventRegistry): string[] {
  return Object.entries(registry.event_types)
    .filter(([, v]) => !v.enabled)
    .map(([k]) => k);
}
