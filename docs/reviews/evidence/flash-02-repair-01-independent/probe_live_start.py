import os,asyncio,json,uuid,importlib.util
from pathlib import Path
from dotenv import load_dotenv
import httpx
load_dotenv('.env',override=True);os.environ['REVIEW_FIXTURE_PASSWORD']=Path('/tmp/aa_review_pw').read_text().strip()
s=importlib.util.spec_from_file_location('live','tests/integration_tests/test_live_tenant_claims.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
async def main():
 a,c=await m._login('acc_alice');c=await m._switch(a,c,m._pick_tenant(c,'A'));ticket=await m._ticket(a,c);h=m._native_headers(ticket);tid=str(uuid.uuid4());out={};runs=[]
 try:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=25) as lg:
   r=await lg.post('/threads',headers=h,json={'thread_id':tid,'metadata':{'tenant_id':c['current_tenant']['id'],'user_id':c['user']['id']}});r.raise_for_status();assert await m._confirm_claim(ticket,tid)
   ah={'x-tenant-id':c['current_tenant']['id'],'x-tenant-context-version':str(c['context_version'])};cookie='; '.join(k+'='+v for k,v in a.cookies.items())
   ph={**ah,'cookie':cookie,'origin':'http://localhost:3000','x-submission-key':uuid.uuid4().hex};body={'assistant_id':'basic-qa','input':{'messages':[{'role':'user','content':'synthetic double claim'}]},'interrupt_before':['*']}
   async with httpx.AsyncClient(base_url=m.PROXY,timeout=30) as proxy:
    r=await proxy.post('/api/threads/'+tid+'/runs',headers=ph,json=body);out['bff_first']={'status':r.status_code,'body':r.json()};
    if r.is_success and r.json().get('run_id'):runs.append(r.json()['run_id'])
    r2=await proxy.post('/api/threads/'+tid+'/runs',headers=ph,json=body);out['bff_replay']={'status':r2.status_code,'body':r2.json()}
   # 正常直接原生创建 vs 换实际图自报原图：使用不同独立受理记录，避免之前消费影响。
   for label,actual in [('native_legal','basic-qa'),('native_graph_swap','intelligent-analysis')]:
    ad=await a.post('/agent/requests',headers=ah,json={'thread_id':tid,'submission_key':uuid.uuid4().hex,'graph_id':'basic-qa','content':'synthetic direct','operation_kind':'initial'});ad.raise_for_status();ad=ad.json();meta={'ledger_request_id':ad['request_id'],'ledger_attempt_id':ad['attempt_id'],'ledger_submission_id':ad['submission_id'],'ledger_graph_id':'basic-qa','ledger_operation_kind':'initial'}
    r=await lg.post('/threads/'+tid+'/runs',headers=h,json={'assistant_id':actual,'input':{'messages':[{'role':'user','content':'synthetic direct'}]},'metadata':meta,'interrupt_before':['*'],'multitask_strategy':'enqueue'});out[label]={'status':r.status_code}
    if r.is_success:runs.append(r.json()['run_id'])
 finally:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:
   for run in runs:await lg.post('/threads/'+tid+'/runs/'+run+'/cancel',headers=h)
   out['cleanup']=(await lg.delete('/threads/'+tid,headers=h)).status_code
  await a.post('/auth/logout');await a.aclose()
 Path('docs/reviews/evidence/flash-02-repair-01-independent/live-start.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
