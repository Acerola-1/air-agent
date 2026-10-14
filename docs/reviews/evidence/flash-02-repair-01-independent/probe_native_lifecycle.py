import os,sys,asyncio,json,uuid,importlib.util,time
from pathlib import Path
from dotenv import load_dotenv
import httpx
load_dotenv('.env',override=True);os.environ['REVIEW_FIXTURE_PASSWORD']=Path('/tmp/aa_review_pw').read_text().strip()
s=importlib.util.spec_from_file_location('live','tests/integration_tests/test_live_tenant_claims.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
async def main():
 a,c=await m._login('acc_alice');c=await m._switch(a,c,m._pick_tenant(c,'A'));ticket=await m._ticket(a,c);h=m._native_headers(ticket);tid=str(uuid.uuid4());out={};rid=None
 try:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=25) as lg:
   rr=await lg.post('/threads',headers=h,json={'thread_id':tid,'metadata':{'tenant_id':c['current_tenant']['id'],'user_id':c['user']['id'],'graph_id':'basic-qa'}});rr.raise_for_status();assert await m._confirm_claim(ticket,tid)
   ad=await a.post('/agent/requests',headers={'x-tenant-id':c['current_tenant']['id'],'x-tenant-context-version':str(c['context_version'])},json={'thread_id':tid,'submission_key':uuid.uuid4().hex,'graph_id':'basic-qa','content':'只回复数字2'});ad.raise_for_status();b=ad.json()
   run=await lg.post('/threads/'+tid+'/runs',headers=h,json={'assistant_id':'basic-qa','input':{'messages':[{'role':'user','content':'只回复数字2'}]},'metadata':{'ledger_attempt_id':b['attempt_id'],'ledger_request_id':b['request_id'],'ledger_submission_id':b['submission_id'],'ledger_graph_id':'basic-qa','ledger_operation_kind':'initial'},'config':{'configurable':{'chat_mode':'fast'}}});out['native_create_status']=run.status_code;run.raise_for_status();rid=run.json()['run_id']
   for _ in range(35):
    status=(await lg.get('/threads/'+tid+'/runs/'+rid,headers=h)).json().get('status')
    if status in ['success','error','interrupted','timeout']:break
    await asyncio.sleep(1)
   detail=(await a.get('/agent/requests/'+b['request_id'])).json();attempt=detail['attempts'][0];out.update(platform_status=status,ledger_state=attempt['state'],platform_id_persisted=bool(attempt['platform_run_id']),answer_count=len(detail.get('answers',[])))
 finally:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:
   if rid:await lg.post('/threads/'+tid+'/runs/'+rid+'/cancel',headers=h)
   out['cleanup']=(await lg.delete('/threads/'+tid,headers=h)).status_code
  await a.post('/auth/logout');await a.aclose()
 Path('docs/reviews/evidence/flash-02-repair-01-independent/native-lifecycle.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
asyncio.run(main())
