const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname,'..');
const React = require(path.join(root,'node_modules/react'));
const jsx = require(path.join(root,'node_modules/react/jsx-runtime'));
const {renderToStaticMarkup} = require(path.join(root,'node_modules/react-dom/server'));
const swc = require(path.join(root,'node_modules/next/dist/build/swc'));
function load(file, imports={}) {
  const {code} = swc.transformSync(fs.readFileSync(path.resolve(root,file),'utf8'), {
    filename:file, jsc:{parser:{syntax:'typescript',tsx:true},target:'es2022',transform:{react:{runtime:'automatic'}}}, module:{type:'commonjs'}
  });
  const mod={exports:{}};
  const req=(name)=> {if(name==='react/jsx-runtime') return jsx; if(name in imports) return imports[name]; throw new Error(`Missing ${name}`);};
  new Function('require','module','exports',code)(req,mod,mod.exports);
  return mod.exports;
}
test('运营外壳允许单独 metrics 或 operations；用户用量仍需双权限',()=>{
  for(const permissions of [[],['agent:metrics:read'],['agent:operations:read'],['agent:metrics:read','agent:operations:read']]) {
    const {default: Layout}=load('../admin/web/app/(dashboard)/operations/layout.tsx', {
      'next/link': ({children,...props})=>React.createElement('a',props,children),
      'next/navigation': {usePathname:()=>'/operations'},
      '@/lib/session': {useSession:()=>({can:(...codes)=>codes.every(c=>permissions.includes(c))})},
      '@/lib/utils': {cn:(...xs)=>xs.filter(Boolean).join(' ')}
    });
    const html=renderToStaticMarkup(React.createElement(Layout,null,'CONTENT_CONTROL'));
    assert.equal(html.includes('CONTENT_CONTROL'),permissions.length>0);
    assert.equal(html.includes('href="/operations/users"'),permissions.length===2);
    assert.equal(html.includes('href="/operations/performance"'),permissions.includes('agent:metrics:read'));
    assert.equal(html.includes('href="/operations/runs"'),permissions.includes('agent:operations:read'));
  }
});
test('无标题、空白标题的可见回退参与搜索；实际标题保持原样',()=>{
  const {threadTitle}=load('lib/thread-title.ts');
  for(const thread of [{},{metadata:null},{metadata:{title:''}},{metadata:{title:'  '}}]) {
    assert.equal(threadTitle(thread),'新会话');
    assert.ok(threadTitle(thread).includes('新会话'));
  }
  assert.equal(threadTitle({metadata:{title:'洛阳空气质量'}}),'洛阳空气质量');
});
test('数据分析选择器仅在该图展示，运行和停止待确认期间锁定',()=>{
  const menus=JSON.parse(fs.readFileSync(path.join(root,'lib/data-analysis-menus.json'),'utf8'));
  for(const graphId of ['basic-qa','data-analysis','intelligent-tracing']) {
    for(const isStreaming of [false,true]) {
      const {AnalysisMenuSelector}=load('components/chat/AnalysisMenuSelector.tsx', {
        '@/lib/data-analysis-menus.json':menus,
        '@/lib/chat-store':{useChatStore:fn=>fn({graphId,analysisMenuName:'CityHourBroadcast',isStreaming,stopPending:false,setAnalysisMenu:()=>{}})}
      });
      const html=renderToStaticMarkup(React.createElement(AnalysisMenuSelector));
      assert.equal(html.includes('<select'),graphId==='data-analysis');
      if(graphId==='data-analysis') {
        assert.equal(html.includes('disabled=""'),isStreaming);
        assert.ok(html.includes('小时播报')); assert.ok(html.includes('月考核排名'));
      }
    }
  }
});
