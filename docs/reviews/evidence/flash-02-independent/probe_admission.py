import os,sys,asyncio,json,uuid,importlib.util
from pathlib import Path
from dotenv import load_dotenv
import httpx
load_dotenv('.env',override=True);os.environ['REVIEW_FIXTURE_PASSWORD']=os.environ['REVIEW_FIXTURE_PASSWORD']
s=importlib.util.spec_from_file_location('live','tests/integration_tests/test_live_tenant_claims.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
async def main():
 a,ctx=await m._login('acc_alice');ctx=await m._switch(a,ctx,m._pick_tenant(ctx,'A'));ticket=await m._ticket(a,ctx);h=m._native_headers(ticket);tenant=ctx['current_tenant']['id'];tid=str(uuid.uuid4());out={};ids=[]
 try:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:
   r=await lg.post('/threads',headers=h,json={'thread_id':tid,'metadata':{'tenant_id':tenant,'user_id':ctx['user']['id'],'graph_id':'basic-qa','title':'Independent synthetic admission review'}});r.raise_for_status();assert await m._confirm_claim(ticket,tid)
   ah={'x-tenant-id':tenant,'x-tenant-context-version':str(ctx['context_version'])};body={'thread_id':tid,'submission_key':uuid.uuid4().hex,'graph_id':'basic-qa','operation_kind':'initial','content':'synthetic reservation A'}
   admitted=await a.post('/agent/requests',headers=ah,json=body);admitted.raise_for_status();ad=admitted.json()
   reservation={'ledger_request_id':ad['request_id'],'ledger_attempt_id':ad['attempt_id']}
   def run(graph,text):return {'assistant_id':graph,'input':{'messages':[{'role':'user','content':text}]},'interrupt_before':['*'],'metadata':reservation,'config':{'configurable':reservation},'multitask_strategy':'enqueue'}
   results=[]
   for graph,text in [('basic-qa','synthetic reservation A'),('basic-qa','synthetic reservation A'),('intelligent-analysis','different content B')]:
    r=await lg.post('/threads/'+tid+'/runs',headers=h,json=run(graph,text));results.append({'status':r.status_code,'graph':graph});
    if r.is_success:ids.append(r.json()['run_id'])
   detail=(await a.get('/agent/requests/'+ad['request_id'])).json();out['native']={'runs':results,'distinct_platform_runs':len(set(ids)),'ledger_attempts':len(detail.get('attempts',[])),'registered_graph':detail.get('request',{}).get('graph_id')}
   # 提交重放必须不重复创建平台运行；直接测BFF真实入口。
   proxyh={**ah,'origin':'http://localhost:3000','x-submission-key':uuid.uuid4().hex,'cookie':'; '.join(k+'='+v for k,v in a.cookies.items())}
   data={'assistant_id':'basic-qa','input':{'messages':[{'role':'user','content':'proxy synthetic replay'}]},'interrupt_before':['*'],'multitask_strategy':'enqueue'}
   pr=[];pids=[]
   async with httpx.AsyncClient(base_url=m.PROXY,timeout=25) as proxy:
    for _ in range(2):
     r=await proxy.post('/api/threads/'+tid+'/runs',headers=proxyh,json=data);pr.append(r.status_code)
     if r.is_success:pids.append(r.json()['run_id']);ids.append(r.json()['run_id'])
   out['proxy']={'statuses':pr,'distinct_platform_runs':len(set(pids))}
   # 原始提交key的关联在regen之后不得漂到新attempt。
   regen=await a.post('/agent/requests',headers=ah,json={**body,'submission_key':uuid.uuid4().hex,'operation_kind':'regenerate','source_attempt_id':ad['attempt_id']});regen.raise_for_status();again=await a.post('/agent/requests',headers=ah,json=body);again.raise_for_status();out['replay_mapping']={'initial_attempt':ad['attempt_id'],'regenerated_attempt':regen.json()['attempt_id'],'initial_key_returns_initial':again.json()['attempt_id']==ad['attempt_id'],'initial_key_returns_regenerated':again.json()['attempt_id']==regen.json()['attempt_id']}
 finally:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:
   for rid in ids:
    await lg.post('/threads/'+tid+'/runs/'+rid+'/cancel',headers=h)
   out['cleanup']=(await lg.delete('/threads/'+tid,headers=h)).status_code
  await a.post('/auth/logout');await a.aclose()
 Path('docs/reviews/evidence/flash-02-independent/live-admission.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
asyncio.run(main())
