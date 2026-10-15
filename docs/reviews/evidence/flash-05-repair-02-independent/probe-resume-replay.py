import os,sys,json,uuid,asyncio
from pathlib import Path
from types import SimpleNamespace
r=Path('/Users/acerola/Dev/Python/air_agent');os.chdir(r);sys.path[:0]=[str(r/'src'),str(r/'tests'),str(r/'admin/api'),str(r/'admin/api/tests')]
own='postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05';runtime='postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05'
os.environ.update({'DATABASE_URI':own,'ADMIN_RUNTIME_DATABASE_URI':runtime,'RUNTIME_DATABASE_URI':runtime,'AUTH_SECRET':'sel05-independent-ephemeral-key','INTERNAL_API_TOKEN':'sel05-independent-internal','AGENT_CAPABILITY_SELECTION_ENABLED':'true'})
for k in ['POSTGRES_USER','POSTGRES_PASSWORD','MCP_SERVER_URL','MAIN_LLM_API_KEY','SILICONFLOW_API_KEY','DEEPSEEK_API_KEY','XFYUN_MAAS_API_KEY','XIAOMI_MIMO_API_KEY','SERPER_API_KEY','ARK_API_KEY']:os.environ.setdefault(k,'controlled-unused')
import psycopg
from httpx import ASGITransport
from ctx_client import ContextClient
from app.main import app
from app.config import get_settings
from app.security import hash_password
from common.event_ledger.store import LedgerScope
from common.event_ledger.store import claim_start,fetch_capability_selection_snapshot,close_pool as close_ledger
from common.identity.db import close_pool as close_identity
from common.identity import langgraph_auth as auth
from app.db import engine_dispose_all
async def main():
 ids={k:str(uuid.uuid4()) for k in ['tenant','user','member','thread']};pw='Controlled-review-'+uuid.uuid4().hex;account='ind05_'+uuid.uuid4().hex[:10]
 async with await psycopg.AsyncConnection.connect(own) as conn:
  await conn.execute("INSERT INTO tenants(id,code,name,status) VALUES(%s,%s,'独立审计','active')",(ids['tenant'],account))
  await conn.execute("INSERT INTO users(id,account,display_name,password_hash,is_active) VALUES(%s,%s,'独立审计',%s,true)",(ids['user'],account,hash_password(pw)))
  await conn.execute("INSERT INTO tenant_memberships(id,tenant_id,user_id,status) VALUES(%s,%s,%s,'active')",(ids['member'],ids['tenant'],ids['user']))
  await conn.execute("INSERT INTO thread_ownership(thread_id,tenant_id,user_id,source) VALUES(%s,%s,%s,'native')",(ids['thread'],ids['tenant'],ids['user']))
  await conn.execute("INSERT INTO mcp_identity_bindings(tenant_id,membership_id,server_key,external_subject,credential_ref,protocol_version,status) VALUES(%s,%s,'controlled-one','synthetic','env:REVIEW_ONLY','unverified','active')",(ids['tenant'],ids['member']))
  await conn.commit()
 out={'kind':'actual_admin_ASGI_auth_and_nonowner_PostgreSQL_native_gate','model_network_calls':0,'ids':ids}
 async with ContextClient(transport=ASGITransport(app=app),base_url='http://test') as client:
  login=await client.post('/auth/login',json={'account':account,'password':pw});assert login.status_code==200,login.text
  me=login.json()
  if (me.get('current_tenant') or {}).get('id')!=ids['tenant']:
   res=await client.post('/auth/switch-tenant',json={'tenant_id':ids['tenant'],'expected_ctx_version':me['context_version']});assert res.status_code==200,res.text;me=res.json()
  async def admit(sel=None,kind='initial',source=None):
   body={'thread_id':ids['thread'],'submission_key':str(uuid.uuid4()),'graph_id':'basic-qa','operation_kind':kind,'content':'独立验收同一问题'}
   if sel is not None:body['capability_selection']=sel
   if source:body['source_attempt_id']=source
   res=await client.post('/agent/requests',json=body);return res
  frontend=json.loads(Path('/tmp/aa05r2-default-payload.json').read_text());res=await admit(frontend);out['actual_frontend_payload']={'status':res.status_code,'errors':[{'loc':e['loc'],'type':e['type']} for e in res.json().get('detail',[])] if isinstance(res.json().get('detail'),list) else res.json()}
  res=await admit();out['old_client_omits_selection']={'status':res.status_code};assert res.status_code==200,res.text;ad=res.json();out['auto_admission']=ad
  async with await psycopg.AsyncConnection.connect(own) as conn:
   cur=await conn.execute('SELECT id::text,ctx_version FROM auth_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(ids['user'],));sid,crev=await cur.fetchone()
  scope=LedgerScope(tenant_id=ids['tenant'],user_id=ids['user'],membership_id=ids['member'])
  decision=await claim_start(scope,submission_id=ad['submission_id'],attempt_id=ad['attempt_id'],thread_id=ids['thread'],graph_id='basic-qa',operation_kind='initial',content='独立验收同一问题',source='native');out['actual_native_claim_after_real_admission']=decision
  actor=SimpleNamespace(identity=ids['user'],tenant_id=ids['tenant'],membership_id=ids['member'],session_id=sid,display_name='review',permissions=['agent:access'],auth_revision=0,user_auth_revision=0,tenant_auth_revision=0,membership_revision=0,session_ctx_version=crev)
  ctx=SimpleNamespace(user=actor,permissions=['agent:access']);auth._GRAPH_NAMES_CACHE=tuple(auth._public_graph_names()) if hasattr(auth,'_public_graph_names') else ('basic-qa','intelligent-analysis','data-analysis','intelligent-report','deep-research','intelligent-tracing')
  fresh=await admit();ad=fresh.json()
  value={'thread_id':ids['thread'],'assistant_id':'basic-qa','metadata':{'ledger_attempt_id':ad['attempt_id'],'ledger_submission_id':ad['submission_id']},'kwargs':{'input':{'messages':[{'type':'human','content':'独立验收同一问题'}]}}}
  try:await auth.on_thread_create_run(ctx,value);out['native_create_run_hook']={'accepted':True}
  except Exception as e:out['native_create_run_hook']={'accepted':False,'exception':type(e).__name__,'status':getattr(e,'status_code',None),'detail':getattr(e,'detail',str(e))}
  async with await psycopg.AsyncConnection.connect(own) as conn:
   await conn.execute("UPDATE mcp_identity_bindings SET protocol_version='v1-fixture' WHERE tenant_id=%s",(ids['tenant'],));await conn.commit()
  from common.mcp_scoped import set_factory,ScopedMCPToolFactory
  from langchain_core.tools import StructuredTool
  factory=ScopedMCPToolFactory({});set_factory(factory)
  base={'schema_version':'agent.capability-selection.v1','skill':{'mode':'auto','skill_id':None},'mcp':{'mode':'allowlist','tool_ids':['mcp:controlled-one:does_not_exist']},'catalog_version':'cat_000000000000000000000000'}
  res=await admit(base);out['valid_shape_forged_catalog_unknown_tool_empty_factory']={'status':res.status_code,'body':res.json()}
  tool=StructuredTool.from_function(lambda: 'ok',name='exists_elsewhere',description='review')
  factory.register_catalog([tool],server_key='other-service')
  sel={**base,'mcp':{'mode':'allowlist','tool_ids':['mcp:controlled-one:exists_elsewhere']}}
  res=await admit(sel);out['tool_exists_only_on_another_server']={'status':res.status_code,'body':res.json()}
  async with await psycopg.AsyncConnection.connect(own) as conn:
   await conn.execute("UPDATE mcp_identity_bindings SET protocol_version='future-unknown-protocol' WHERE tenant_id=%s",(ids['tenant'],));await conn.commit()
  res=await admit(sel);out['unknown_protocol_not_literal_unverified']={'status':res.status_code,'body':res.json()}
  async with await psycopg.AsyncConnection.connect(own) as conn:
   await conn.execute("UPDATE mcp_identity_bindings SET protocol_version='v1-fixture' WHERE tenant_id=%s",(ids['tenant'],));await conn.commit()
  factory.register_catalog([tool],server_key='controlled-one')
  key=str(uuid.uuid4());body={'thread_id':ids['thread'],'submission_key':key,'graph_id':'basic-qa','operation_kind':'initial','content':'同键不同选择','capability_selection':sel}
  res=await client.post('/agent/requests',json=body);first=res.json();out['replay_explicit_first_status']=res.status_code
  body['capability_selection']={'schema_version':'agent.capability-selection.v1','skill':{'mode':'auto','skill_id':None},'mcp':{'mode':'auto','tool_ids':[]},'catalog_version':None}
  res=await client.post('/agent/requests',json=body);out['same_key_explicit_changed_to_explicit_auto']={'status':res.status_code,'body':res.json()}
  if first.get('attempt_id'):
   async with await psycopg.AsyncConnection.connect(own) as conn:
    await conn.execute("UPDATE agent_run_attempts SET state='waiting_input' WHERE id=%s",(first['attempt_id'],));await conn.commit()
   resume_body={'thread_id':ids['thread'],'submission_key':uuid.uuid4().hex,'graph_id':'basic-qa','operation_kind':'resume','source_attempt_id':first['attempt_id'],'content':'恢复补充'}
   ares=await client.post('/agent/requests',json=resume_body);bres=await client.post('/agent/requests',json=resume_body)
   out['resume_same_key_omitted_choice_replay']={'first_status':ares.status_code,'replay_status':bres.status_code,'replay_body':bres.json()}
   res=await admit(kind='resume',source=first['attempt_id']);out['resume_inherit']={'status':res.status_code,'body':res.json()}
   if res.status_code==200:
    resumed=res.json();val={'thread_id':ids['thread'],'assistant_id':'basic-qa','metadata':{'ledger_attempt_id':resumed['attempt_id'],'ledger_submission_id':resumed['submission_id'],'ledger_operation_kind':'resume'},'kwargs':{'input':{'messages':[{'type':'human','content':'独立验收同一问题'}]}}}
    try:await auth.on_thread_create_run(ctx,val);out['native_resume_with_selected_mcp']={'accepted':True}
    except Exception as e:out['native_resume_with_selected_mcp']={'accepted':False,'status':getattr(e,'status_code',None),'detail':getattr(e,'detail',str(e))}
  res=await admit({'schema_version':'agent.capability-selection.v1','skill':{'mode':'explicit','skill_id':'skill:basic-qa:air-quality-basic-query'},'mcp':{'mode':'allowlist','tool_ids':[]},'catalog_version':'cat_000000000000000000000000'})
  out['forged_catalog_with_valid_skill_and_no_mcp']={'status':res.status_code,'body':res.json()}
  set_factory(None)
  get_settings().agent_capability_selection_enabled=False
  res=await admit();out['flag_disabled_auto_admission']={'status':res.status_code}
  if res.status_code==200:
   a=res.json();out['flag_disabled_auto_claim']=await claim_start(scope,submission_id=a['submission_id'],attempt_id=a['attempt_id'],thread_id=ids['thread'],graph_id='basic-qa',operation_kind='initial',content='独立验收同一问题',source='native')
 # Retain synthetic rows for independent reproducibility in the dedicated test DB.
 (r/'docs/reviews/evidence/flash-05-repair-02-independent/live-replay.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2));await close_ledger();await close_identity();await engine_dispose_all()
asyncio.run(main())
