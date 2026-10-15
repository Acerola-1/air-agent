const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const React = require(path.join(root, 'node_modules/react'));
const rd = require(path.join(root, 'node_modules/react-dom/server'));

// 实测 Skill 项：包含服务端真实字段与前端兼容字段
const actualSkill = {
  id: 'skill:basic-qa:air-quality-basic-query',
  name: 'air-quality-basic-query',
  title: '空气质量基础查询',
  description: '查询实时与历史空气质量指标',
  material_version: 'sha256:1234567890abcdef',
  status: 'available',
  required_tool_ids: ['mcp:controlled:query'],
  required_tools: ['mcp:controlled:query'],
};

const actualTool = {
  id: 'mcp:controlled:query',
  server_key: 'controlled',
  name: 'query',
  tool_name: 'query',
  display_name: '受控查询',
  description: '受控数据查询工具',
  status: 'available',
};

const cap = {
  catalog: {
    skills: [actualSkill],
    mcp_services: [{ server_key: 'controlled', display_name: '受控服务', status: 'available' }],
    tools: [actualTool],
    catalog_version: 'cat_test_v1',
  },
  catalogLoading: false,
  catalogError: null,
  selection: {
    skillMode: 'auto',
    selectedSkillId: null,
    mcpMode: 'auto',
    selectedToolIds: [],
  },
  fetchCatalog: async () => {},
  setSkillMode: () => {},
  setMcpMode: () => {},
  toggleTool: () => {},
  resetToDefault: () => {},
};

const primitive = ({ children }) => React.createElement('div', null, children);
const dialogs = Object.fromEntries(
  ['Dialog', 'DialogContent', 'DialogDescription', 'DialogHeader', 'DialogTitle', 'DialogTrigger'].map((k) => [
    k,
    primitive,
  ])
);

const mocks = {
  react: React,
  'react/jsx-runtime': require(path.join(root, 'node_modules/react/jsx-runtime')),
  'lucide-react': require(path.join(root, 'node_modules/lucide-react')),
  '@/lib/capability-store': { useCapabilityStore: (selector) => selector(cap) },
  '@/lib/chat-store': { useChatStore: (sel) => sel({ graphId: 'basic-qa', isStreaming: false }) },
  '@/lib/session-client': { useSessionStore: (sel) => sel({ ctx: { current_tenant: { id: 't' } } }) },
  '@/components/ui/button': { Button: primitive },
  '@/components/ui/dialog': dialogs,
  '@/lib/utils': { cn: (...s) => s.filter(Boolean).join(' ') },
};

const source = swc.transformSync(
  fs.readFileSync(path.join(root, 'components/chat/CapabilitySelector.tsx'), 'utf8'),
  {
    filename: 'CapabilitySelector.tsx',
    jsc: {
      parser: { syntax: 'typescript', tsx: true },
      target: 'es2022',
      transform: { react: { runtime: 'automatic' } },
    },
    module: { type: 'commonjs' },
  }
).code;

const mod = { exports: {} };
new Function('require', 'module', 'exports', source)(
  (n) => {
    if (n in mocks) return mocks[n];
    throw Error(`Unmocked dependency in component: ${n}`);
  },
  mod,
  mod.exports
);

const result = {
  kind: 'real_React_server_render_actual_TSX_controlled_stores',
  backend_skill_fields: Object.keys(actualSkill),
  rendered: false,
};

try {
  const html = rd.renderToString(React.createElement(mod.exports.CapabilitySelector));
  result.rendered = typeof html === 'string' && html.length > 0;
  result.contains_label = html.includes('能力与工具');
  result.all_passed = result.rendered && result.contains_label;
} catch (e) {
  result.rendered = false;
  result.error = e.message;
  result.all_passed = false;
}

console.log(JSON.stringify(result, null, 2));
