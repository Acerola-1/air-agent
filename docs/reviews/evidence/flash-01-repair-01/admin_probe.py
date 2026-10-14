from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,sys,json,asyncio,importlib.util,uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True)
db=json.load(open(str(EVIDENCE_ROOT / 'fresh-migrations.json')))['databases'][0]
for name,source in [('DATABASE_URI','TENANT_TEST_OWNER_URI'),('ADMIN_RUNTIME_DATABASE_URI','TENANT_TEST_RUNTIME_URI')]: os.environ[name]=os.environ[source].rsplit('/',1)[0]+'/'+db
sys.path[:0]=[str(Path('admin/api').resolve()),str(Path('admin/api/tests').resolve()),str(Path('src').resolve())]
from app.db import OwnerSessionLocal
from app.main import app
from common.identity.ticket import verify_ticket
from app.config import get_settings
from sqlalchemy import text
from httpx import AsyncClient,ASGITransport
spec=importlib.util.spec_from_file_location('review_world','admin/api/tests/test_tenant_api.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
async def main():
 out={}; gen=m.world.__wrapped__(); w=await anext(gen)
 try:
  async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c, AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as p:
   x=(await c.post('/auth/login',json={'account':w['both_account'],'password':m.PASSWORD})).json()
   x=(await c.post('/auth/switch-tenant',json={'tenant_id':w['tenant_a'],'expected_ctx_version':x['context_version']})).json()
   hc={'x-tenant-id':w['tenant_a'],'x-tenant-context-version':str(x['context_version'])}
   px=(await p.post('/auth/login',json={'account':w['platform_account'],'password':m.PASSWORD})).json()
   hp={'x-tenant-id':px['current_tenant']['id'] if px['current_tenant'] else 'none','x-tenant-context-version':str(px['context_version'])}
   out['enterprise_permission_tree']=(await c.get('/permissions/grantable-tree')).status_code
   out['missing_context_department']=(await c.post('/departments',json={'dept_code':'missing','name':'missing'})).status_code
   r=await p.patch('/tenants/'+w['tenant_a'],headers=hp,json={'status':'suspended'}); assert r.status_code==200
   out['suspended_department']=(await c.post('/departments',headers=hc,json={'dept_code':'suspended','name':'suspended'})).status_code
   role=await c.post('/roles',headers=hc,json={'code':'repair_suspended_'+uuid.uuid4().hex[:6],'name':'synthetic suspended role'})
   out['suspended_role_create']=role.status_code
   await p.patch('/tenants/'+w['tenant_a'],headers=hp,json={'status':'active'})
   if role.status_code==201: await c.delete('/roles/'+role.json()['id'],headers=hc)
   out['roles_read']=(await c.get('/roles',headers=hc)).status_code
   # 授权撤销通过角色权限更新API；捕获前后成员revision和旧运行票据可用性。
   tr=await c.post('/auth/runtime-ticket',headers={'x-internal-token':os.environ['INTERNAL_API_TOKEN']},json={'target_tenant_id':w['tenant_a']}); assert tr.status_code==200
   claims=verify_ticket(tr.json()['ticket'],get_settings().runtime_ticket_key_pairs,expected_audience='langgraph-runtime')
   async with OwnerSessionLocal() as s:
    rid=str(await s.scalar(text("SELECT id FROM roles WHERE tenant_id=:t AND code='tenant_admin'"),{'t':w['tenant_a']}))
    before=await s.scalar(text('SELECT revision FROM tenant_memberships WHERE id=:m'),{'m':w['member_a_both']})
   rr=await c.put('/roles/'+rid+'/permissions',headers=hc,json={'permission_codes':[]}); out['remove_role_permissions']=rr.status_code
   async with OwnerSessionLocal() as s:
    after=await s.scalar(text('SELECT revision FROM tenant_memberships WHERE id=:m'),{'m':w['member_a_both']})
   out['member_revision_before']=before; out['member_revision_after']=after
   out['next_admin_read_after_revoke']=(await c.get('/departments',headers=hc)).status_code
   os.environ['RUNTIME_DATABASE_URI']=os.environ['ADMIN_RUNTIME_DATABASE_URI']
   from common.identity.runtime import identity_from_config,revalidate
   from common.identity import db as idb
   ident=identity_from_config({'configurable':{'langgraph_auth_user':{'identity':claims.user_id,'tenant_id':claims.tenant_id,'membership_id':claims.membership_id,'session_id':claims.session_id,'session_ctx_version':claims.session_ctx_version,'user_auth_revision':claims.user_auth_revision,'tenant_auth_revision':claims.tenant_auth_revision,'membership_revision':claims.membership_revision}}})
   try: await revalidate(ident); out['runtime_allows_after_role_permission_revoke']=True
   except Exception as e: out['runtime_allows_after_role_permission_revoke']=False; out['runtime_denial_type']=type(e).__name__
   await c.post('/auth/logout'); await p.post('/auth/logout')
 finally:
  try: await anext(gen)
  except StopAsyncIteration: pass
 print(json.dumps(out,ensure_ascii=False,indent=2)); Path(str(EVIDENCE_ROOT)+'/admin-proofs.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
asyncio.run(main())
