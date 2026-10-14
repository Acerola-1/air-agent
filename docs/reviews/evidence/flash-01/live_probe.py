# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,uuid,json
from dotenv import load_dotenv
import httpx,psycopg
load_dotenv('.env',override=True)
base='http://127.0.0.1:2024'
results={}
def login(account):
    c=httpx.Client(base_url='http://127.0.0.1:8100',timeout=15)
    r=c.post('/auth/login',json={'account':account,'password':os.environ['REVIEW_FIXTURE_PASSWORD']})
    if r.status_code!=200: raise RuntimeError('本地验收账号不可用:'+str(r.status_code))
    ctx=r.json();items=ctx['tenants']
    # 用户预置的本地验收账号，不枚举其他账号或历史对话
    chosen=next((x['tenant'] for x in items if x['tenant']['name'].endswith('A')),items[0]['tenant'])
    if ctx['current_tenant'] is None or ctx['current_tenant']['id']!=chosen['id']:
        ctx=c.post('/auth/switch-tenant',json={'tenant_id':chosen['id'],'expected_ctx_version':ctx['context_version']}).json()
    return c,ctx

def ticket(c,ctx):
    r=c.post('/auth/runtime-ticket',headers={'x-internal-token':os.environ['INTERNAL_API_TOKEN']},json={'target_tenant_id':ctx['current_tenant']['id']})
    if r.status_code!=200: raise RuntimeError('运行票据不可用:'+str(r.status_code))
    return {'Authorization':'Bearer '+r.json()['ticket']}
a,ca=login('acc_alice');b,cb=login('acc_bob')
ha=ticket(a,ca);hb=ticket(b,cb)
tid=str(uuid.uuid4());aid=str(uuid.uuid4())
dburi=os.environ['DATABASE_URI'];assert dburi.rsplit('/',1)[1]=='air_agent'
try:
    with httpx.Client(base_url=base,timeout=20) as lg:
        r=lg.post('/threads',headers=hb,json={'thread_id':tid,'metadata':{'tenant_id':cb['current_tenant']['id'],'user_id':cb['user']['id'],'title':'review01 synthetic private marker'}})
        results['synthetic_bob_thread_created']=r.status_code
        assert r.status_code==200
        with psycopg.connect(dburi) as db:
            db.execute('DELETE FROM thread_ownership WHERE thread_id=%s AND user_id=%s',(tid,cb['user']['id']))
        results['legacy_read_before_claim']=lg.get('/threads/'+tid,headers=ha).status_code
        r=lg.post('/threads',headers=ha,json={'thread_id':tid,'if_exists':'do_nothing','metadata':{'tenant_id':ca['current_tenant']['id'],'user_id':ca['user']['id']}})
        results['legacy_claim_create_status']=r.status_code
        r=lg.get('/threads/'+tid,headers=ha)
        results['legacy_read_after_claim']=r.status_code
        results['other_user_synthetic_marker_exposed']=r.status_code==200 and r.json().get('metadata',{}).get('title')=='review01 synthetic private marker'
        r=lg.post('/threads',headers=ha,json={'thread_id':aid,'metadata':{'tenant_id':ca['current_tenant']['id'],'user_id':ca['user']['id']}})
        assert r.status_code==200
        r=lg.patch('/threads/'+aid,headers=ha,json={'metadata':{'tenant_id':cb['current_tenant']['id'],'user_id':cb['user']['id'],'title':'review01 own synthetic marker'}})
        observed=lg.get('/threads/'+aid,headers=ha)
        results['native_patch_keeps_trusted_owner']=observed.status_code==200 and observed.json().get('metadata',{}).get('tenant_id')==ca['current_tenant']['id'] and observed.json().get('metadata',{}).get('user_id')==ca['user']['id']
        lg.delete('/threads/'+aid,headers=ha)
        # 只恢复刚创建的合成线程原有归属，然后由原所有者删除。
        with psycopg.connect(dburi) as db:
            db.execute("INSERT INTO thread_ownership(thread_id,tenant_id,user_id,source) VALUES (%s,%s,%s,'native') ON CONFLICT(thread_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,user_id=EXCLUDED.user_id",(tid,cb['current_tenant']['id'],cb['user']['id']))
        results['synthetic_thread_cleanup']=lg.delete('/threads/'+tid,headers=hb).status_code
    old=ca
    target=next(x['tenant'] for x in ca['tenants'] if x['tenant']['id']!=ca['current_tenant']['id'])
    r=a.post('/auth/switch-tenant',json={'tenant_id':target['id'],'expected_ctx_version':ca['context_version']})
    assert r.status_code==200;new=r.json();sid=str(uuid.uuid4())
    # 旧标签页持旧上下文，浏览器共用 cookie；只创建无内容的合成线程。
    r=a.post('http://127.0.0.1:3000/api/threads',headers={'x-tenant-context-version':str(old['context_version']),'x-tenant-id':old['current_tenant']['id']},json={'thread_id':sid,'metadata':{'user_id':old['user']['id'],'tenant_id':old['current_tenant']['id'],'title':'review01 stale tab synthetic'}})
    results['stale_tab_create_status']=r.status_code
    results['stale_tab_wrote_into_new_tenant']=r.status_code==200 and r.json().get('metadata',{}).get('tenant_id')==new['current_tenant']['id']
    if r.status_code==200:
        httpx.delete(base+'/threads/'+sid,headers=ticket(a,new),timeout=15)
    with psycopg.connect(dburi) as db:
        rows=db.execute("SELECT relname,relrowsecurity,pg_get_userbyid(relowner) FROM pg_class WHERE relname IN ('tool_artifact_storage','artifact_computations')").fetchall()
        results['archive_db_policies']=[{'table':x[0],'rls_enabled':x[1],'owner_role':x[2]} for x in rows]
finally:
    for c in (a,b):
        try:c.post('/auth/logout')
        finally:c.close()
print(json.dumps(results,ensure_ascii=False,indent=2))
