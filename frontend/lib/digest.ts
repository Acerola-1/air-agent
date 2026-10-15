/**
 * 确定性规范选择摘要与选择 ID 生成器（Change 4 客户端回读规范）
 */

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
  for (let candidate = 2; primeCounter < 64; candidate++) {
    if (!isComposite[candidate]) {
      for (i = 0; i < 300; i += candidate) {
        isComposite[i] = candidate;
      }
      hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
      k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
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
  materialVersion: string = "auto",
): string {
  const skill = (selection.skill as Record<string, unknown> | undefined) ?? {};
  const mcp = (selection.mcp as Record<string, unknown> | undefined) ?? {};
  const skillMode = (skill.mode as string) || (selection.skill_mode as string) || "auto";
  const skillId = (skill.skill_id as string | null) ?? (selection.skill_id as string | null) ?? null;
  const mcpMode = (mcp.mode as string) || (selection.mcp_mode as string) || "auto";
  const rawTools = (mcp.tool_ids as string[]) || (selection.tool_ids as string[]) || [];
  const uniqueTools = Array.from(new Set(rawTools)).sort();

  const payload: Record<string, unknown> = {
    graph_id: graphId,
    material_version: materialVersion,
    mcp_mode: mcpMode,
    skill_id: skillMode === "auto" ? null : skillId,
    skill_mode: skillMode,
    tool_ids: mcpMode === "auto" ? [] : uniqueTools,
  };

  const canonicalJson = JSON.stringify(payload, Object.keys(payload).sort());
  return sha256(canonicalJson);
}

export function deriveSelectionId(selectionDigest: string): string {
  const h = selectionDigest.slice(0, 32).padEnd(32, "0");
  return [
    h.slice(0, 8),
    h.slice(8, 12),
    "5" + h.slice(13, 16),
    "a" + h.slice(17, 20),
    h.slice(20, 32),
  ].join("-");
}
