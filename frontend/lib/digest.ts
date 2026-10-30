/** 确定性能力选择摘要（SHA-256）。 */

// 标准 SHA-256 实现（零外部依赖，Node/浏览器/测试通用）
function sha256(ascii: string): string {
  function rightRotate(value: number, amount: number): number {
    return (value >>> amount) | (value << (32 - amount));
  }

  const mathPow = Math.pow;
  const maxWord = mathPow(2, 32);
  let i = 0;
  let j = 0;

  const words: number[] = [];
  const asciiBitLength = ascii.length * 8;

  let hash: number[] = [];
  const k: number[] = [];
  let primeCounter = 0;

  const isComposite: Record<number, number> = {};
  // SHA-256 的 64 个常量用到第 64 个素数 311；筛到 400 以覆盖 301 等合数。
  for (let candidate = 2; primeCounter < 64; candidate++) {
    if (!isComposite[candidate]) {
      for (i = candidate * candidate; i < 400; i += candidate) {
        isComposite[i] = candidate;
      }
      hash[primeCounter] = ((mathPow(candidate, 0.5) % 1) * maxWord) | 0;
      k[primeCounter++] = ((mathPow(candidate, 1 / 3) % 1) * maxWord) | 0;
    }
  }

  hash = hash.slice(0, 8);

  for (i = 0; i < ascii.length; i++) {
    const code = ascii.charCodeAt(i);
    words[i >> 2] |= (code & 0xff) << (8 * (3 - (i % 4)));
  }

  words[asciiBitLength >> 5] |= 0x80 << (24 - (asciiBitLength % 32));
  words[(((asciiBitLength + 64) >> 9) << 4) + 15] = asciiBitLength;

  for (let block = 0; block < words.length; block += 16) {
    const w: number[] = [];
    for (i = 0; i < 16; i++) {
      w[i] = words[block + i] | 0;
    }
    for (i = 16; i < 64; i++) {
      const s0 = rightRotate(w[i - 15], 7) ^ rightRotate(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rightRotate(w[i - 2], 17) ^ rightRotate(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (((w[i - 16] + s0) | 0) + ((w[i - 7] + s1) | 0)) | 0;
    }

    let a = hash[0];
    let b = hash[1];
    let c = hash[2];
    let d = hash[3];
    let e = hash[4];
    let f = hash[5];
    let g = hash[6];
    let h = hash[7];

    for (i = 0; i < 64; i++) {
      const S1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = ((((h + S1) | 0) + ((ch + k[i]) | 0)) | 0) + w[i] | 0;
      const S0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) | 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) | 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) | 0;
    }

    hash[0] = (hash[0] + a) | 0;
    hash[1] = (hash[1] + b) | 0;
    hash[2] = (hash[2] + c) | 0;
    hash[3] = (hash[3] + d) | 0;
    hash[4] = (hash[4] + e) | 0;
    hash[5] = (hash[5] + f) | 0;
    hash[6] = (hash[6] + g) | 0;
    hash[7] = (hash[7] + h) | 0;
  }

  let result = "";
  for (i = 0; i < 8; i++) {
    for (j = 3; j >= 0; j--) {
      const b = (hash[i] >> (8 * j)) & 255;
      result += (b < 16 ? "0" : "") + b.toString(16);
    }
  }
  return result;
}

export function computeCanonicalSelectionDigest(
  graphId: string,
  selection: Record<string, unknown>,
): string {
  const retiredFields = ["skill", "skill_mode", "skill_id", "material_version"];
  if (retiredFields.some((field) => Object.hasOwn(selection, field))) {
    throw new Error("能力选择包含已撤回的 Skill 字段");
  }
  if (selection.schema_version !== "agent.capability-selection.v2") {
    throw new Error("能力选择协议版本不受支持");
  }
  const mcp = selection.mcp as { mode?: unknown; tool_ids?: unknown } | undefined;
  if (
    !mcp ||
    (mcp.mode !== "auto" && mcp.mode !== "allowlist") ||
    !Array.isArray(mcp.tool_ids) ||
    !mcp.tool_ids.every((toolId) => typeof toolId === "string")
  ) {
    throw new Error("MCP 能力选择格式无效");
  }
  const mcpMode = mcp.mode;
  const toolIds = mcpMode === "auto" ? [] : Array.from(new Set(mcp.tool_ids as string[])).sort();
  const payload = {
    graph_id: graphId,
    mcp_mode: mcpMode,
    tool_ids: toolIds,
  };

  const canonicalJson = JSON.stringify(payload, Object.keys(payload).sort());
  return sha256(canonicalJson);
}
