from pathlib import Path
exec(Path(__file__).with_name('probe-live.py').read_text().split('async def main():')[0])
import subprocess,secrets,socket,time,signal,httpx
from mcp_fixture.server import FixtureServer
async def main():
 ids={k:str(uuid.uuid4()) for k in ['tenant','user','member','thread']};pw='Process-review-'+uuid.uuid4().hex;account='ind05p_'+uuid.uuid4().hex[:10]
 async with await psycopg.AsyncConnection.connect(own) as conn:
  await conn.execute("INSERT INTO tenants(id,code,name,status) VALUES(%s,%s,'进程边界验收','active')",(ids['tenant'],account))
  await conn.execute("INSERT INTO users(id,account,display_name,password_hash,is_active) VALUES(%s,%s,'合成验收',%s,true)",(ids['user'],account,hash_password(pw)))
  await conn.execute("INSERT INTO tenant_memberships(id,tenant_id,user_id,status) VALUES(%s,%s,%s,'active')",(ids['member'],ids['tenant'],ids['user']))
  await conn.execute("INSERT INTO thread_ownership(thread_id,tenant_id,user_id,source) VALUES(%s,%s,%s,'native')",(ids['thread'],ids['tenant'],ids['user']))
  await conn.execute("INSERT INTO mcp_identity_bindings(tenant_id,membership_id,server_key,external_subject,credential_ref,protocol_version,status) VALUES(%s,%s,'controlled-one','subject-a','env:REVIEW_FIXTURE_TOK','v1-fixture','active')",(ids['tenant'],ids['member']));await conn.commit()
 fixture=FixtureServer();procs=[];out={'kind':'real_HTTP_separate_admin_and_runtime_custom_app_processes_with_real_MCP_fixture','ids':ids}
 try:
  url=await fixture.start();env=os.environ.copy();env.update({'RUNTIME_TICKET_KEYS':json.dumps({'review05':secrets.token_urlsafe(32)}),'DATABASE_URI':own,'ADMIN_RUNTIME_DATABASE_URI':runtime,'RUNTIME_DATABASE_URI':runtime,'TENANT_MCP_FIXTURE_MODE':'true','AGENT_CAPABILITY_SELECTION_ENABLED':'true','MCP_SERVER_NAME':'controlled-one','MCP_SERVER_URL':url,'MCP_SERVER_URL_CONTROLLED_ONE':url,'REVIEW_FIXTURE_TOK':'tok-tenant-a','LANGFUSE_ENABLED':'false','AGENT_OBSERVABILITY_ENABLED':'false','PYTHONPATH':str(r/'src')+':'+str(r/'admin/api')+':'+str(r/'tests')})
  ports=[]
  for _ in range(2):
   with socket.socket() as s:s.bind(('127.0.0.1',0));ports.append(s.getsockname()[1])
  for label,module,port in [('admin','app.main:app',ports[0]),('catalog','common.artifact_http:app',ports[1])]:
   f=open('/tmp/aa05r2-process-'+label+'.log','w');p=subprocess.Popen(['/tmp/aa-takeover35-py313/bin/python','-m','uvicorn',module,'--host','127.0.0.1','--port',str(port),'--lifespan','off'],cwd=r,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True);f.close();procs.append(p)
  async with httpx.AsyncClient(timeout=15) as client:
   for port in ports:
    end=time.monotonic()+20
    while True:
     try:
      res=await client.get(f'http://127.0.0.1:{port}/openapi.json')
      if res.status_code==200:break
     except httpx.HTTPError:pass
     if time.monotonic()>end:raise RuntimeError('owned process failed to start')
     await asyncio.sleep(.1)
  async with ContextClient(base_url=f'http://127.0.0.1:{ports[0]}',timeout=15) as admin,httpx.AsyncClient(base_url=f'http://127.0.0.1:{ports[1]}',timeout=15) as native:
   res=await admin.post('/auth/login',json={'account':account,'password':pw});assert res.status_code==200,res.text
   me=res.json()
   if (me.get('current_tenant') or {}).get('id')!=ids['tenant']:
    res=await admin.post('/auth/switch-tenant',json={'tenant_id':ids['tenant'],'expected_ctx_version':me['context_version']});assert res.status_code==200,res.text;me=res.json()
   res=await admin.post('/auth/runtime-ticket',headers={'x-internal-token':env['INTERNAL_API_TOKEN']},json={'target_tenant_id':ids['tenant'],'expected_ctx_version':me['context_version']});assert res.status_code==200,res.text;ticket=res.json()['ticket']
   res=await native.get('/capabilities',params={'graph_id':'basic-qa'},headers={'authorization':'Bearer '+ticket});out['native_catalog_status']=res.status_code;catalog=res.json();out['native_tools']=[t['id'] for t in catalog.get('tools',[])];assert 'mcp:controlled-one:query_air' in out['native_tools'],catalog
   payload={'thread_id':ids['thread'],'submission_key':uuid.uuid4().hex,'graph_id':'basic-qa','operation_kind':'initial','content':'受控进程隔离正例','capability_selection':{'schema_version':'agent.capability-selection.v1','skill':{'mode':'auto','skill_id':None},'mcp':{'mode':'allowlist','tool_ids':['mcp:controlled-one:query_air']},'catalog_version':catalog['catalog_version']}}
   res=await admin.post('/agent/requests',json=payload);out['admit_legitimate_catalog_selection_status']=res.status_code;out['admit_legitimate_catalog_selection_body']=res.json();out['mcp_call_count']=await fixture.call_count();out['process_ids']=[p.pid for p in procs]
 finally:
  for p in procs:
   if p.poll() is None:
    os.killpg(p.pid,signal.SIGTERM)
    try:await asyncio.wait_for(asyncio.to_thread(p.wait),timeout=5)
    except TimeoutError:os.killpg(p.pid,signal.SIGKILL)
  await fixture.stop()
  out['owned_processes_stopped']=all(p.poll() is not None for p in procs)
  (r/'docs/reviews/evidence/flash-05-repair-02-independent/process-boundary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
