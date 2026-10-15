import os,sys,json,uuid,asyncio,time
from pathlib import Path
import httpx
r=Path('/Users/acerola/Dev/Python/air_agent');env=json.loads(Path('/tmp/aa05f-stack-env.json').read_text());identity=json.loads(Path('/tmp/aa05f-native-smoke-state.json').read_text())['identity'];out=[]
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8104',timeout=30) as admin,httpx.AsyncClient(base_url='http://127.0.0.1:2026',timeout=150) as native,httpx.AsyncClient(base_url='http://localhost:3104',timeout=150,headers={'origin':'http://localhost:3104'}) as bff:
  res=await admin.post('/auth/login',json={'account':identity['account'],'password':identity['password']});ctx=res.json();headers={'x-tenant-id':identity['tenant'],'x-tenant-context-version':str(ctx['context_version'])};admin.headers.update(headers)
  res=await bff.post('/api/auth/login',json={'account':identity['account'],'password':identity['password']});assert res.status_code==200,res.text;bff.headers.update(headers)
  async def refresh():
   res=await admin.post('/auth/runtime-ticket',headers={'x-internal-token':env['INTERNAL_API_TOKEN']},json={'target_tenant_id':identity['tenant'],'expected_ctx_version':ctx['context_version']});native.headers['authorization']='Bearer '+res.json()['ticket']
  for graph in ['basic-qa','intelligent-analysis','data-analysis','intelligent-report','deep-research','intelligent-tracing']:
   for entry in ['bff']:
    record={'graph':graph,'entry':entry};out.append(record)
    try:
     await refresh();client=native if entry=='native' else bff;prefix='' if entry=='native' else '/api';thread=str(uuid.uuid4())
     res=await client.post(prefix+'/threads',json={'thread_id':thread,'metadata':{'graph_id':graph,'tenant_id':identity['tenant'],'user_id':identity['user']}});assert res.status_code in (200,201),res.text;thread=res.json()['thread_id'];record['thread_id']=thread
     if entry=='native':
      res=await native.post('/internal/threads/claim-confirmation',headers={'x-internal-token':env['INTERNAL_API_TOKEN']},json={'thread_id':thread});assert res.status_code==200,res.text
     cfg={'configurable':{'chat_mode':'fast'}}
     if graph=='data-analysis':cfg['configurable']['menu_name']='CityHourBroadcast'
     res=await native.get('/capabilities',params={'graph_id':graph,**({'menu_name':'CityHourBroadcast'} if graph=='data-analysis' else {})});cat=res.json()
     selection={'schema_version':'agent.capability-selection.v1','skill':{'mode':'auto'},'mcp':{'mode':'allowlist','tool_ids':[]},'catalog_version':cat['catalog_version']}
     content='你好，请回复一句简短问候。';body={'assistant_id':graph,'input':{'messages':[{'type':'human','content':content}]},'config':cfg,'interrupt_before':['call_model' if graph in ['basic-qa','intelligent-analysis','data-analysis'] else 'model']};extra={}
     if entry=='native':
      res=await admin.post('/agent/requests',json={'thread_id':thread,'submission_key':uuid.uuid4().hex,'graph_id':graph,'content':content,'operation_kind':'initial','capability_selection':selection,'menu_name':'CityHourBroadcast' if graph=='data-analysis' else None});assert res.status_code==200,res.text;ad=res.json();body['metadata']={k:ad[k.removeprefix('ledger_')] for k in ['ledger_request_id','ledger_attempt_id','ledger_submission_id']}
     else:extra={'x-submission-key':uuid.uuid4().hex,'x-capability-selection':json.dumps(selection)}
     res=await client.post(prefix+f'/threads/{thread}/runs/wait',json=body,headers=extra);assert res.status_code==200,res.text
     await refresh();runs=(await native.get(f'/threads/{thread}/runs')).json();original=runs[0];record['interrupted_run_id']=original['run_id'];record['interrupted_status']=original['status']
     checkpoint=(await native.get(f'/threads/{thread}/state')).json();record['paused_next']=checkpoint.get('next');assert record['paused_next'],checkpoint
     record['pause_semantics']='官方0.15.1静态interrupt_before：run status=success，但checkpoint.next仍待执行，不能当作整图完成'

     mapped=await admin.get('/agent/feedback/attempts/resolve',params={'thread_id':thread,'platform_run_id':original['run_id']})
     if mapped.status_code!=200:
      res=await bff.post('/api/agent-runs/confirm',json={'thread_id':thread,'run_id':original['run_id']});assert res.json().get('confirmed'),res.text
      mapped=await admin.get('/agent/feedback/attempts/resolve',params={'thread_id':thread,'platform_run_id':original['run_id']})
     assert mapped.status_code==200,mapped.text;attempt=mapped.json()['attempt_id'];accepted=mapped.json()['capability_selection'];record['original_selection']=accepted
     # 来自刚刚回读的官方 interrupted 事实；用内部状态观测契约写入 waiting_input。
     res=await admin.post(f'/agent/runs/{attempt}/state',headers={'x-internal-token':env['INTERNAL_API_TOKEN']},json={'state':'waiting_input','evidence_source':'platform_read','platform_status':original['status']});assert res.status_code==200,res.text
     resume={'assistant_id':graph,'input':None,'config':cfg};extra={'x-operation-kind':'resume','x-source-attempt-id':attempt,'x-submission-key':uuid.uuid4().hex}
     if entry=='native':
      res=await admin.post('/agent/requests',json={'thread_id':thread,'submission_key':extra['x-submission-key'],'graph_id':graph,'content':'','operation_kind':'resume','source_attempt_id':attempt,'menu_name':'CityHourBroadcast' if graph=='data-analysis' else None});assert res.status_code==200,res.text;ad=res.json();record['resume_admission']=ad
      resume['metadata']={k:ad[k.removeprefix('ledger_')] for k in ['ledger_request_id','ledger_attempt_id','ledger_submission_id']};resume['metadata']['ledger_operation_kind']='resume';extra={}
     res=await client.post(prefix+f'/threads/{thread}/runs/wait',json=resume,headers=extra);record['resume_http_status']=res.status_code;assert res.status_code==200,res.text
     await refresh();runs=(await native.get(f'/threads/{thread}/runs')).json();last=runs[0];record['resumed_run_id']=last['run_id'];record['resumed_status']=last['status']
     res=await bff.post('/api/agent-runs/confirm',json={'thread_id':thread,'run_id':last['run_id']});record['resumed_confirmation']=res.json();new_selection=res.json().get('capability_selection');record['passed']=last['status']=='success' and new_selection and new_selection['selection_digest']==accepted['selection_digest'];assert record['passed'],record
    except Exception as e:record.update(passed=False,error=type(e).__name__,detail=str(e)[:700])
    (r/'docs/reviews/evidence/takeover-05-final/interrupt-resume.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(graph,entry,record.get('passed'),record.get('detail',''),flush=True)
asyncio.run(main())
