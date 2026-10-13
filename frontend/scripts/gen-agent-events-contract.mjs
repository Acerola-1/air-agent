#!/usr/bin/env node
/** 从契约事实源生成前端只读副本 `lib/agent-events/contract-data.ts`。
 *
 * 契约事实源：`docs/contracts/agent-events-v1/{event-registry,states}.json`。
 * 浏览器运行时不能读仓库文件，故嵌入；契约测试断言嵌入副本与事实源一致。
 * 改动事实源后重跑：`node scripts/gen-agent-events-contract.mjs`
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(__dirname, '..');
const repo = path.resolve(frontend, '..');
const contracts = path.join(repo, 'docs/contracts/agent-events-v1');

const registry = JSON.parse(fs.readFileSync(path.join(contracts, 'event-registry.json'), 'utf8'));
const states = JSON.parse(fs.readFileSync(path.join(contracts, 'states.json'), 'utf8'));

const header =
  '/** 由 frontend/scripts/gen-agent-events-contract.mjs 生成，勿手改。\n' +
  ' * 事实源：docs/contracts/agent-events-v1/{event-registry,states}.json */\n\n' +
  "import type { EventRegistry, StatesContract } from './contract';\n\n";

const body =
  `export const EVENT_REGISTRY: EventRegistry = ${JSON.stringify(registry, null, 2)} as EventRegistry;\n\n` +
  `export const RUN_STATES: StatesContract = ${JSON.stringify(states, null, 2)} as StatesContract;\n`;

fs.writeFileSync(path.join(frontend, 'lib/agent-events/contract-data.ts'), header + body);
console.log('wrote lib/agent-events/contract-data.ts');
