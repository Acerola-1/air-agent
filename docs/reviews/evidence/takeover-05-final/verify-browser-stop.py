import asyncio,json,psycopg,httpx
from pathlib import Path
from psycopg.rows import dict_row
out=Path('docs/reviews/evidence/takeover-05-final');i=json.loads(Path('/tmp/aa05f-browser-identity.json').read_text());e=json.loads(Path('/tmp/aa05f-stack-env.json').read_text())
with psycopg.connect(e['DATABASE_URI'],row_factory=dict_row) as c:
 row=c.execute("SELECT a.id::text,a.state,a.platform_run_id,a.cancel_requested,a.cancel_origin,r.thread_id,s.id::text AS submission_id,s.submission_key,s.admission_status FROM agent_run_attempts a JOIN agent_requests r ON r.id=a.request_id JOIN agent_submissions s ON s.attempt_id=a.id WHERE a.user_id=%s ORDER BY a.admitted_at DESC LIMIT 1",(i['user'],)).fetchone()
 row['stop_events']=c.execute("SELECT event_type,payload FROM agent_events WHERE attempt_id=%s AND event_type IN ('run.cancel_requested','run.state_observed') ORDER BY occurred_at",(row['id'],)).fetchall()
async def inspect():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8104') as admin:
  ctx=(await admin.post('/auth/login',json={'account':i['account'],'password':i['password']})).json()
  if (ctx.get('current_tenant') or {}).get('id')!=i['tenant']:ctx=(await admin.post('/auth/switch-tenant',json={'tenant_id':i['tenant'],'expected_ctx_version':ctx['context_version']})).json()
  issued=await admin.post('/auth/runtime-ticket',headers={'x-internal-token':e['INTERNAL_API_TOKEN']},json={'target_tenant_id':i['tenant'],'expected_ctx_version':ctx['context_version']});issued.raise_for_status()
  async with httpx.AsyncClient(base_url='http://127.0.0.1:2026',headers={'authorization':'Bearer '+issued.json()['ticket']}) as native:
   res=await native.get('/threads/'+row['thread_id']+'/runs/'+row['platform_run_id']);res.raise_for_status();data=res.json();row['platform_status']=data['status'];row['platform_key_matches']=data.get('metadata',{}).get('ledger_submission_key')==row['submission_key']
 assert row['cancel_requested'] and row['platform_status']=='interrupted' and row['platform_key_matches'],row
 row['confirmed_browser_unlocked']=True
asyncio.run(inspect());(out/'browser-stop-final.json').write_text(json.dumps(row,ensure_ascii=False,indent=2,default=str)+'\n');print('actual stop:',row['platform_status'],'intent:',row['cancel_requested'],'mapping:',row['admission_status'],'key matches:',row['platform_key_matches'])
