import os,sys,json,uuid,asyncio
from pathlib import Path
from types import SimpleNamespace
r=Path('/Users/acerola/Dev/Python/air_agent');os.chdir(r);sys.path[:0]=[str(r/'src'),str(r/'tests'),str(r/'admin/api'),str(r/'admin/api/tests'),str(r/'admin/api/.venv/lib/python3.13/site-packages')]
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
  frontend=json.loads(Path('/tmp/aa05-default-payload.json').read_text());res=await admit(frontend);out['actual_frontend_payload']={'status':res.status_code,'errors':[{'loc':e['loc'],'type':e['type']} for e in res.json().get('detail',[])] if isinstance(res.json().get('detail'),list) else res.json()}
  res=await admit();out['old_client_omits_selection']={'status':res.status_code};assert res.status_code==200,res.text;ad=res.json();out['auto_admission']=ad
  async with await psycopg.AsyncConnection.connect(own) as conn:
   cur=await conn.execute('SELECT id::text,ctx_version FROM auth_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(ids['user'],));sid,crev=await cur.fetchone()
  scope=LedgerScope(tenant_id=ids['tenant'],user_id=ids['user'],membership_id=ids['member'])
  decision=await claim_start(scope,submission_id=ad['submission_id'],attempt_id=ad['attempt_id'],thread_id=ids['thread'],graph_id='basic-qa',operation_kind='initial',content='独立验收同一问题',source='native');out['actual_native_claim_after_real_admission']=decision
  actor=SimpleNamespace(identity=ids['user'],tenant_id=ids['tenant'],membership_id=ids['member'],session_id=sid,display_name='review',permissions=['agent:access'],auth_revision=0,user_auth_revision=0,tenant_auth_revision=0,membership_revision=0,session_ctx_version=crev)
  ctx=SimpleNamespace(user=actor,permissions=['agent:access']);auth._GRAPH_NAMES_CACHE=tuple(auth._public_graph_names()) if hasattr(auth,'_public_graph_names') else ('basic-qa','intelligent-analysis','data-analysis','intelligent-report','deep-research','intelligent-tracing')
  value={'thread_id':ids['thread'],'assistant_id':'basic-qa','metadata':{'ledger_attempt_id':ad['attempt_id'],'ledger_submission_id':ad['submission_id']},'kwargs':{'input':{'messages':[{'type':'human','content':'独立验收同一问题'}]}}}
  try:await auth.on_thread_create_run(ctx,value);out['native_create_run_hook']={'accepted':True}
  except Exception as e:out['native_create_run_hook']={'accepted':False,'exception':type(e).__name__,'status':getattr(e,'status_code',None),'detail':getattr(e,'detail',str(e))}
  select={'schema_version':'agent.capability-selection.v1','skill':{'mode':'auto','skill_id':None},'mcp':{'mode':'allowlist','tool_ids':['mcp:controlled-one:does_not_exist']},'catalog_version':'not-a-real-catalog'}
  res=await admit(select);out['forged_catalog_unknown_tool_unverified_protocol']={'status':res.status_code};unknown=res.json()
  if res.status_code==200:
   out['forged_catalog_unknown_tool_unverified_protocol']['accepted_selection']=unknown['capability_selection']
   async with await psycopg.AsyncConnection.connect(own) as conn:
    await conn.execute("UPDATE agent_run_attempts SET state='waiting_input' WHERE id=%s",(unknown['attempt_id'],));await conn.commit()
   res=await admit(kind='resume',source=unknown['attempt_id']);out['resume_inherit_explicit_without_new_selection']={'status':res.status_code,'body':res.json()}
   res=await admit(kind='retry',source=unknown['attempt_id']);out['retry_inherit_explicit_without_new_selection']={'status':res.status_code,'body':res.json()}
   val={'thread_id':ids['thread'],'assistant_id':'basic-qa','metadata':{'ledger_attempt_id':unknown['attempt_id'],'ledger_submission_id':unknown['submission_id'],'ledger_operation_kind':'resume'},'kwargs':{'input':{'messages':[]}}}
   try:await auth.on_thread_create_run(ctx,val);out['native_resume_with_selected_mcp']={'accepted':True}
   except Exception as e:out['native_resume_with_selected_mcp']={'accepted':False,'exception':type(e).__name__,'status':getattr(e,'status_code',None),'detail':getattr(e,'detail',str(e))}
  get_settings().agent_capability_selection_enabled=False
  res=await admit();out['flag_disabled_auto_admission']={'status':res.status_code}
  if res.status_code==200:
   a=res.json();out['flag_disabled_auto_claim']=await claim_start(scope,submission_id=a['submission_id'],attempt_id=a['attempt_id'],thread_id=ids['thread'],graph_id='basic-qa',operation_kind='initial',content='独立验收同一问题',source='native')
 # Retain synthetic rows for independent reproducibility in the dedicated test DB.
 (r/'docs/reviews/evidence/flash-05-independent/live-probes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2));await close_ledger();await close_identity();await engine_dispose_all()
asyncio.run(main())
