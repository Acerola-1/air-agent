# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,sys,asyncio,json,uuid,importlib.util
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True)
for name,source in [('DATABASE_URI','TENANT_TEST_OWNER_URI'),('ADMIN_RUNTIME_DATABASE_URI','TENANT_TEST_RUNTIME_URI')]:
    os.environ[name]=os.environ[source].rsplit('/',1)[0]+'/air_agent_review01_api'
sys.path.insert(0,str(Path('admin/api').resolve()));sys.path.insert(0,str(Path('src').resolve()))
from app.db import OwnerSessionLocal
from app.main import app
from app.security import hash_password,RuntimeTicket,sign_runtime_ticket
from scripts.migrate_identity import MigrationRunner
from sqlalchemy import text
from httpx import AsyncClient,ASGITransport
spec=importlib.util.spec_from_file_location('review_world','admin/api/tests/test_tenant_api.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
async def main():
    async with OwnerSessionLocal() as ss:
        await ss.execute(text("CREATE TABLE IF NOT EXISTS thread(thread_id UUID PRIMARY KEY,metadata JSONB NOT NULL DEFAULT '{}'::jsonb)"));await ss.commit()
    out={};gen=m.world.__wrapped__();world=await anext(gen)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',timeout=15) as c:
        x=(await c.post('/auth/login',json={'account':world['both_account'],'password':m.PASSWORD})).json()
        x=(await c.post('/auth/switch-tenant',json={'tenant_id':world['tenant_a'],'expected_ctx_version':x['context_version']})).json()
        out['enterprise_roles_api_status']=(await c.get('/roles')).status_code
        out['enterprise_permission_tree_status']=(await c.get('/permissions/tree')).status_code
        # 让本地 fixture 在 B 也有管理员权限，以准确验证“旧页面写入错误企业”。
        async with OwnerSessionLocal() as s:
            rid=await s.scalar(text("SELECT id FROM roles WHERE tenant_id=:t AND code='tenant_admin'"),{'t':world['tenant_b']})
            await s.execute(text('INSERT INTO membership_roles(membership_id,role_id,tenant_id) VALUES (:m,:r,:t)'),{'m':world['member_b_both'],'r':rid,'t':world['tenant_b']});await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as platform:
            await platform.post('/auth/login',json={'account':world['platform_account'],'password':m.PASSWORD})
            fresh_account='review_link_'+uuid.uuid4().hex[:7]
            fresh=(await platform.post('/users',json={'account':fresh_account,'display_name':'synthetic role link','password':'synthetic-review-password'})).json()
            try:
                linked=await platform.post('/tenants/'+world['tenant_a']+'/members',json={'user_id':fresh['id'],'role_codes':['tenant_admin']})
                out['platform_link_tenant_admin_status']=linked.status_code
            except Exception as exc:
                out['platform_link_tenant_admin_error']=type(exc).__name__
            async with OwnerSessionLocal() as ss:
                await ss.execute(text('DELETE FROM users WHERE id=:i'),{'i':fresh['id']});await ss.commit()
            await platform.patch('/tenants/'+world['tenant_a'],json={'status':'suspended'})
            blocked=await c.post('/departments',json={'dept_code':'review-suspended-'+uuid.uuid4().hex[:6],'name':'synthetic suspended write'})
            out['suspended_tenant_department_write_status']=blocked.status_code
            await platform.patch('/tenants/'+world['tenant_a'],json={'status':'active'})
            if blocked.status_code==201:await c.delete('/departments/'+blocked.json()['id'])
        old=x['context_version']
        await c.post('/auth/switch-tenant',json={'tenant_id':world['tenant_b'],'expected_ctx_version':old})
        r=await c.post('/departments',headers={'x-tenant-context-version':str(old),'x-tenant-id':world['tenant_a']},json={'dept_code':'review-stale-'+uuid.uuid4().hex[:6],'name':'review synthetic stale context'})
        out['admin_stale_write_status']=r.status_code
        out['admin_stale_write_wrong_tenant']=r.status_code==201 and r.json()['tenant_id']==world['tenant_b']
        if r.status_code==201:await c.delete('/departments/'+r.json()['id'])
    # 同名 JSON/PG，不提供显式 canonical：预检应报冲突，实际会吞并成 PG 身份。
    account='review_collide_'+uuid.uuid4().hex[:7];uid=str(uuid.uuid4());jid=str(uuid.uuid4())
    async with OwnerSessionLocal() as s:
        await s.execute(text('INSERT INTO users(id,account,display_name,password_hash) VALUES (:i,:a,:a,:h)'),{'i':uid,'a':account,'h':hash_password('pg-password')});await s.commit()
    manifest={'version':1,'tenants':[],'users':[{'account':account,'source':'json','json_id':jid}],'memberships':[],'threads':[]}
    runner=MigrationRunner(manifest,{jid:{'id':jid,'username':account,'passwordHash':hash_password('json-password')}})
    out['same_name_preflight_clean']=(await runner.preflight())['clean']
    await runner.apply()
    async with OwnerSessionLocal() as s:
        out['same_name_silently_reused_pg']=str(await s.scalar(text('SELECT id FROM users WHERE account=:a'),{'a':account}))==uid
        await s.execute(text('DELETE FROM users WHERE id=:i'),{'i':uid});await s.execute(text('DELETE FROM migration_ledger WHERE manifest_fingerprint=:f'),{'f':runner.fingerprint});await s.commit()
    try:await anext(gen)
    except StopAsyncIteration:pass
    # 用真实签发实现与 Agent 验签模块：移动旧密钥数组位置导致旧票据失效。
    from app.security import verify_runtime_ticket
    def parse_keys(raw):return [(f"v{i+1}",k) for i,k in enumerate(json.loads(raw))]
    def verify_ticket(token,keys,**kw):
        if verify_runtime_ticket(token,keys) is None:raise ValueError("invalid")
    import time
    now=int(time.time());tk=RuntimeTicket(str(uuid.uuid4()),'u','t','m','langgraph-runtime',now,now+60,1,0,1,1,'v1')
    token=sign_runtime_ticket(tk,parse_keys('["synthetic-old-key"]'))
    try:verify_ticket(token,parse_keys('["synthetic-new-key","synthetic-old-key"]'),expected_audience='langgraph-runtime');rotation=True
    except Exception:rotation=False
    out['previous_ticket_valid_during_rotation']=rotation
    print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
