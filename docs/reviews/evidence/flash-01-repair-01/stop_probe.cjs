// 独立复验输入，读取当前项目模块；无真实网络或凭据。
const fs=require('node:fs');
const outDir=process.env.REVIEW_REPAIR_OUTPUT_DIR || '/tmp/air-agent-review02-replay';
const path=require('node:path');
if (path.resolve(outDir).includes('/docs/reviews/evidence/flash-01')) throw new Error('不得覆写评审证据');
fs.mkdirSync(outDir,{recursive:true});
const p='/Users/acerola/Dev/Python/air_agent/frontend/tests/chat-contract.test.cjs';
const src=fs.readFileSync(p,'utf8');
const prefix=src.slice(0,src.indexOf("test('context meter"));
const helpers=src.slice(src.indexOf('function deferred()'),src.indexOf("test('every intermediate frame"));
const fixture=new Function('require','__dirname',prefix+helpers+'\nreturn fixture;')(require,require('node:path').dirname(p));
(async()=>{
 const out={};
 for(const [key,options] of [['context_changed_409',{cancelThrows:409}],['unknown_status',{runStatuses:[{found:true,status:null}]}],['run_not_found',{runStatuses:[{found:false,status:null}]}]]) {
  const f=fixture(options), store=f.useChatStore;
  const running=store.getState().sendMessage('synthetic','fast'); await new Promise(r=>setImmediate(r));
  f.streams[0].handlers.onRun('synthetic-run');
  const stopped=await store.getState().stopStreaming();out[key+'_confirmed']=stopped.confirmed;
  f.streams[0].wait.resolve();await running;
 }
 fs.writeFileSync(path.join(outDir,'stop-proofs.json'),JSON.stringify(out,null,2)+'\n');console.log(JSON.stringify(out,null,2));
})();
