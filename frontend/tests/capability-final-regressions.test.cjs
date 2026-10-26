const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const jsx = require(path.join(root, 'node_modules/react/jsx-runtime'));

function composer(disabled = false) {
  const sends = [];
  const initialStates = [
    '中文候选词',
    {
      scope: JSON.stringify(['basic-qa', 'u', 'A', 1, 1]),
      value: { graph_id: 'basic-qa', max_input_chars: 8000, character_count_mode: 'unicode_code_point' },
    },
    null,
    false,
    0,
  ];
  let stateIndex = 0;
  const react = {
    useState: () => [initialStates[stateIndex++] ?? null, () => {}],
    useRef: (initial) => ({ current: initial }),
    useCallback: (fn) => fn,
    useEffect: () => {},
  };
  const source = fs.readFileSync(path.join(root, 'components/chat/ChatPanel.tsx'), 'utf8') + '\nexport const __ComposerForTest = Composer;';
  const compiled = swc.transformSync(source, { filename: 'ChatPanel.tsx',
    jsc: { parser: { syntax: 'typescript', tsx: true }, target: 'es2022', transform: { react: { runtime: 'automatic' } } },
    module: { type: 'commonjs' } }).code;
  const module = { exports: {} };
  const ignored = new Proxy({}, { get: () => () => null });
  const requireLocal = (name) => name === 'react' ? react : name === 'react/jsx-runtime' ? jsx
    : name === '@/lib/chat-store' ? { useChatStore: (fn) => fn({ graphId: 'basic-qa' }) }
    : name === '@/lib/session-client' ? { useSessionStore: (fn) => fn({
      username: null, sessionEpoch: 1,
      ctx: { user: { id: 'u' }, current_tenant: { id: 'A' }, context_version: 1 },
    }) }
    : name === '@/lib/chat-config' ? {
      fetchChatConfig: async () => ({ graph_id: 'basic-qa', max_input_chars: 8000, character_count_mode: 'unicode_code_point' }),
      countUnicodeCodePoints: (text) => Array.from(String(text)).length,
    } : ignored;
  new Function('require', 'module', 'exports', compiled)(requireLocal, module, module.exports);
  const tree = module.exports.__ComposerForTest({ disabled, onSend: (text) => sends.push(text), onStop: () => {} });
  const find = (node) => {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'textarea') return node;
    for (const child of [node.props?.children].flat(Infinity)) { const found = find(child); if (found) return found; }
    return null;
  };
  return { textarea: find(tree).props, sends };
}
const event = (overrides = {}) => ({ key: 'Enter', shiftKey: false, nativeEvent: { isComposing: false, keyCode: 13 }, preventDefault() {}, ...overrides });

test('真实 Composer 的中文组合输入与 Safari 229 不触发发送', () => {
  const { textarea, sends } = composer();
  textarea.onKeyDown(event({ nativeEvent: { isComposing: true, keyCode: 13 } }));
  textarea.onKeyDown(event({ nativeEvent: { isComposing: false, keyCode: 229 } }));
  textarea.onCompositionStart(); textarea.onKeyDown(event());
  assert.deepEqual(sends, []);
  textarea.onCompositionEnd(); textarea.onKeyDown(event());
  assert.deepEqual(sends, ['中文候选词']);
});

test('真实 Composer 的换行与运行锁定不提交', () => {
  const a = composer(); a.textarea.onKeyDown(event({ shiftKey: true })); assert.deepEqual(a.sends, []);
  const b = composer(true); b.textarea.onKeyDown(event()); assert.deepEqual(b.sends, []);
});
