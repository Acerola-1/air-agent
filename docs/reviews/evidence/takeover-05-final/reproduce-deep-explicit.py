import os,sys,json,uuid,asyncio,time
from pathlib import Path
import httpx
r=Path('/Users/acerola/Dev/Python/air_agent');env=json.loads(Path('/tmp/aa05f-stack-env.json').read_text());state=json.loads(Path('/tmp/aa05f-native-smoke-state.json').read_text());identity=state['identity']
OUT=r/'docs/reviews/evidence/takeover-05-final'
graphs=['intelligent-report','deep-research','intelligent-tracing']
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8104',timeout=25) as admin,httpx.AsyncClient(base_url='http://127.0.0.1:2026',timeout=150) as native,httpx.AsyncClient(base_url='http://localhost:3104',timeout=150,headers={'origin':'http://localhost:3104'}) as bff:
  res=await admin.post('/auth/login',json={'account':identity['account'],'password':identity['password']});assert res.status_code==200,res.text;ctx=res.json()
  if (ctx.get('current_tenant') or {}).get('id')!=identity['tenant']:
   switched=await admin.post('/auth/switch-tenant',json={'tenant_id':identity['tenant'],'expected_ctx_version':ctx['context_version']});assert switched.status_code==200,switched.text;ctx=switched.json()
  headers={'x-tenant-id':identity['tenant'],'x-tenant-context-version':str(ctx['context_version'])};admin.headers.update(headers)
  res=await bff.post('/api/auth/login',json={'account':identity['account'],'password':identity['password']});assert res.status_code==200,res.text
  bctx=(await bff.get('/api/auth/me')).json()
  if (bctx.get('current_tenant') or {}).get('id')!=identity['tenant']:
   switched=await bff.post('/api/auth/switch-tenant',json={'tenant_id':identity['tenant'],'expected_ctx_version':bctx['context_version']});assert switched.status_code==200,switched.text;bctx=switched.json()
  bff.headers.update({'x-tenant-id':identity['tenant'],'x-tenant-context-version':str(bctx['context_version'])})
  async def ticket():
   res=await admin.post('/auth/runtime-ticket',headers={'x-internal-token':env['INTERNAL_API_TOKEN']},json={'target_tenant_id':identity['tenant'],'expected_ctx_version':ctx['context_version']});assert res.status_code==200,res.text;return 'Bearer '+res.json()['ticket']
  results=[];sem=asyncio.Semaphore(3)
  async def cell(graph,entry,mode,explicit=False):
   async with sem:
    t=time.monotonic();thread=str(uuid.uuid4());text='你好，请按本次指定技能给出验收问候，不查询业务数据。';client=native if entry=='native' else bff;prefix='' if entry=='native' else '/api';ad=None;record={'graph':graph,'entry':entry,'mode':mode,'explicit':explicit,'thread_id':thread}
    try:
     auth={'authorization':await ticket()};native.headers.update(auth)
     res=await client.post(prefix+'/threads',json={'thread_id':thread,'metadata':{'graph_id':graph,'user_id':identity['user'],'tenant_id':identity['tenant']}})
     assert res.status_code in (200,201),f'thread {res.status_code} {res.text[:300]}'
     if entry=='bff':
      thread=res.json()['thread_id'];record['thread_id']=thread
     if entry=='native':
      res=await native.post('/internal/threads/claim-confirmation',headers={'x-internal-token':env['INTERNAL_API_TOKEN'],**auth},json={'thread_id':thread});assert res.status_code==200,res.text
     cfg={'configurable':{'chat_mode':'fast'}}
     if graph=='data-analysis':cfg['configurable']['menu_name']='CityHourBroadcast'
     selection=None
     if explicit:
      res=await native.get('/capabilities',params={'graph_id':graph,**({'menu_name':'CityHourBroadcast'} if graph=='data-analysis' else {})},headers=auth);assert res.status_code==200,res.text;catalog=res.json();skill=catalog.get('skills',[])
      selection={'schema_version':'agent.capability-selection.v1','skill':{'mode':'explicit','skill_id':skill[0]['id']} if skill else {'mode':'auto'},'mcp':{'mode':'allowlist','tool_ids':[]},'catalog_version':catalog['catalog_version']};record['selected_skill']=selection['skill'].get('skill_id')
     body={'assistant_id':graph,'input':{'messages':[{'type':'human','content':text}]},'config':cfg,'stream_mode':['values','messages','custom']};extra={}
     if entry=='native':
      data={'thread_id':thread,'submission_key':uuid.uuid4().hex,'graph_id':graph,'operation_kind':'initial','content':text}
      if graph=='data-analysis':data['menu_name']='CityHourBroadcast'
      if selection:data['capability_selection']=selection
      res=await admin.post('/agent/requests',json=data);assert res.status_code==200,f'admit {res.status_code} {res.text[:300]}';ad=res.json();record['admission']=ad
      body['metadata']={key:ad[key.removeprefix('ledger_')] for key in ['ledger_request_id','ledger_attempt_id','ledger_submission_id']}
     else:
      extra={'x-submission-key':uuid.uuid4().hex}
      if selection:extra['x-capability-selection']=json.dumps(selection)
     if mode=='wait':
      res=await client.post(prefix+f'/threads/{thread}/runs/wait',json=body,headers=extra);record['http_status']=res.status_code
      result=res.json();record['error']=result.get('__error__') if isinstance(result,dict) else str(result)[:100]
     else:
      lines=[]
      async with client.stream('POST',prefix+f'/threads/{thread}/runs/stream',json=body,headers=extra) as res:
       record['http_status']=res.status_code
       async for line in res.aiter_lines():lines.append(line)
      record['sse_error_events']=[line for i,line in enumerate(lines) if i and lines[i-1]=='event: error']
     res=await native.get(f'/threads/{thread}/runs',headers={'authorization':await ticket()});assert res.status_code==200,res.text
     runs=res.json();run=runs[0];record['run_id']=run['run_id'];record['run_status']=run.get('status')
     if entry=='bff':
      resolved=await bff.post('/api/agent-runs/confirm',json={'thread_id':thread,'run_id':run['run_id']});record['confirm']=resolved.json()
     res=await native.get(f'/threads/{thread}/state',headers={'authorization':await ticket()});assert res.status_code==200,res.text
     vals=res.json().get('values',{});messages=vals.get('messages',[]);answers=[m for m in messages if m.get('type')=='ai' and m.get('content') and not m.get('tool_calls')];record['answers']=[{'content':str(m['content'])[:300],'usage':m.get('usage_metadata')} for m in answers]
     record['loaded_skill']=vals.get('selected_skill') or vals.get('selected_skill_name');record['state_keys']=list(vals)
     record['passed']=record['http_status']==200 and record['run_status']=='success' and bool(answers) and not record.get('sse_error_events') and not record.get('error') and not any('服务暂' in a['content'] or '服务暂时不可用' in a['content'] for a in record['answers'])
    except Exception as exc:record.update(passed=False,exception=type(exc).__name__,detail=str(exc)[:500])
    record['elapsed_s']=round(time.monotonic()-t,3);results.append(record);(OUT/'deep-explicit-fixture.json').write_text(json.dumps({'kind':'official_0.15.1_API_real_original_hy3_same_gateway_bridge_corrected','cells':results},ensure_ascii=False,indent=2));print(graph,entry,mode,explicit,record.get('passed'),record.get('run_status'),record.get('detail',''),flush=True)

  await asyncio.gather(*(cell(g,e,'wait',True) for g in graphs for e in ['native','bff']))
  print('passed',sum(c.get('passed',False) for c in results),'/',len(results),flush=True)
asyncio.run(main())
