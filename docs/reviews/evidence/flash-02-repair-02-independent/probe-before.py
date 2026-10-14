import os,asyncio,json,uuid,importlib.util,datetime as dt
from pathlib import Path
from dotenv import load_dotenv
import httpx,psycopg
load_dotenv('.env',override=True);os.environ['RUNTIME_DATABASE_URI']=os.environ['ADMIN_RUNTIME_DATABASE_URI'];os.environ['REVIEW_FIXTURE_PASSWORD']=Path('/tmp/aa_review_pw').read_text().strip()
s=importlib.util.spec_from_file_location('live','tests/integration_tests/test_live_tenant_claims.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
async def main():
 a,c=await m._login('acc_alice');c=await m._switch(a,c,m._pick_tenant(c,'A'));ticket=await m._ticket(a,c);h=m._native_headers(ticket);tid=str(uuid.uuid4());out={};runs=[]
 try:
  async with httpx.AsyncClient(base_url=m.NATIVE,timeout=25) as lg:
   r=await lg.post('/threads',headers=h,json={'thread_id':tid,'metadata':{'tenant_id':c['current_tenant']['id'],'user_id':c['user']['id']}});r.raise_for_status();assert await m._confirm_claim(ticket,tid)
   ah={'x-tenant-id':c['current_tenant']['id'],'x-tenant-context-version':str(c['context_version'])}
   ad=await a.post('/agent/requests',headers=ah,json={'thread_id':tid,'submission_key':uuid.uuid4().hex,'graph_id':'basic-qa','content':'synthetic private content','operation_kind':'initial'});ad.raise_for_status();ad=ad.json()
   meta={'ledger_request_id':ad['request_id'],'ledger_attempt_id':ad['attempt_id'],'ledger_submission_id':ad['submission_id'],'ledger_graph_id':'basic-qa','ledger_operation_kind':'initial','tenant_id':c['current_tenant']['id'],'user_id':c['user']['id']}
   body={'assistant_id':'basic-qa','input':{'messages':[{'role':'user','content':'synthetic private content'}]},'metadata':meta,'interrupt_before':['*'],'multitask_strategy':'enqueue'}
   r=await lg.post('/threads/'+tid+'/runs',headers=h,json=body);r.raise_for_status();runs.append(r.json()['run_id'])
   st=await a.post('/agent/reconcile/ticket',headers={'x-internal-token':os.environ['INTERNAL_API_TOKEN']},json={'tenant_id':c['current_tenant']['id'],'thread_ids':[tid],'ttl_seconds':300});st.raise_for_status();sh={'authorization':'Bearer '+st.json()['ticket']}
   for key,path in [('run',f'/threads/{tid}/runs/{runs[0]}'),('state',f'/threads/{tid}/state'),('history',f'/threads/{tid}/history')]:
    r=await (lg.post(path,headers=sh,json={'limit':1}) if key=='history' else lg.get(path,headers=sh));j=r.json()
    out['service_'+key]={'status':r.status_code,'contains_private_text':'synthetic private content' in r.text,'response_keys':list(j) if isinstance(j,dict) else ['list']}
   ur=(await lg.get('/threads/'+tid+'/runs',headers=h)).json();sr=(await lg.get('/threads/'+tid+'/runs',headers=sh)).json();out['run_counts']={'user':len(ur),'service':len(sr)}
   await a.post('/auth/logout');out['user_after_logout']=(await lg.get('/threads/'+tid+'/runs',headers=h)).status_code
   from common.event_ledger.store import LedgerScope,close_pool
   from common.event_ledger.reconcile import Reconciler,HttpRunStatusProbe
   scope=LedgerScope(c['current_tenant']['id'],c['user']['id'],c['membership']['id'] if 'membership' in c else ad.get('membership_id',''))
   # crash simulation: only own synthetic attempt; platform has a run but mapping did not commit.
   with psycopg.connect(os.environ['DATABASE_URI']) as co:
    user=co.execute('SELECT membership_id::text FROM agent_run_attempts WHERE id=%s',(ad['attempt_id'],)).fetchone()[0]
    co.execute("UPDATE agent_run_attempts SET platform_run_id=NULL,state='admission_unknown',last_progress_at=now()-interval '600 seconds' WHERE id=%s",(ad['attempt_id'],))
   scope=LedgerScope(c['current_tenant']['id'],c['user']['id'],user)
   outcomes=await Reconciler(probe=HttpRunStatusProbe(m.NATIVE,st.json()['ticket']),release_after_seconds=300).reconcile_scope(scope,thread_id=tid)
   out['reconcile']=[{'action':o.action,'detail':o.detail} for o in outcomes];await close_pool()
   with psycopg.connect(os.environ['DATABASE_URI']) as co:
    out['claim_released']=co.execute('SELECT start_claimed_at IS NULL FROM agent_submissions WHERE id=%s',(ad['submission_id'],)).fetchone()[0]
   # New user ticket after logout: relogin only this dedicated fixture, no password reset.
   b,bc=await m._login('acc_alice');bc=await m._switch(b,bc,m._pick_tenant(bc,'A'));bt=await m._ticket(b,bc)
   r=await lg.post('/threads/'+tid+'/runs',headers=m._native_headers(bt),json=body);out['same_submission_retry_status']=r.status_code
   if r.is_success:runs.append(r.json()['run_id'])
   out['platform_runs_after_retry']=len((await lg.get('/threads/'+tid+'/runs',headers=m._native_headers(bt))).json())
   for run in runs:await lg.post('/threads/'+tid+'/runs/'+run+'/cancel',headers=m._native_headers(bt))
   out['cleanup']=(await lg.delete('/threads/'+tid,headers=m._native_headers(bt))).status_code
   await b.post('/auth/logout');await b.aclose()
 finally:await a.aclose()
 Path('docs/reviews/evidence/flash-02-repair-02-independent/service-boundary-before-final.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
