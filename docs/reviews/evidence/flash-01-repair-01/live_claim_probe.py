from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,sys,asyncio,json,uuid,importlib.util
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True)
os.environ['REVIEW_FIXTURE_PASSWORD']=os.environ['REVIEW_FIXTURE_PASSWORD']
spec=importlib.util.spec_from_file_location('live',Path('tests/integration_tests/test_live_tenant_claims.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
async def main():
 a,ca=await m._login('acc_alice'); b,cb=await m._login('acc_bob')
 ca=await m._switch(a,ca,m._pick_tenant(ca,'A') or ca['tenants'][0]['tenant']['id'])
 cb=await m._switch(b,cb,m._pick_tenant(cb,'B') or cb['tenants'][-1]['tenant']['id'])
 tid=str(uuid.uuid4()); result={}; created=False
 try:
  ha=m._native_headers(await m._ticket(a,ca)); hb=m._native_headers(await m._ticket(b,cb))
  async with m.httpx.AsyncClient(base_url=m.NATIVE,timeout=25) as lg:
   r=await lg.post('/threads',headers=hb,json={'thread_id':tid,'metadata':{'tenant_id':cb['current_tenant']['id'],'user_id':cb['user']['id'],'review_marker':'synthetic-private-b'}})
   result['bob_create']=r.status_code; assert r.status_code in (200,201); created=True
   assert await m._confirm_claim(hb['Authorization'][7:],tid,cb['current_tenant']['id'],cb['user']['id'])
   m._drop_ownership(tid)
   result['alice_read_before']=(await lg.get('/threads/'+tid,headers=ha)).status_code
   headers={'x-tenant-id':ca['current_tenant']['id'],'x-tenant-context-version':str(ca['context_version'])}
   async with m.httpx.AsyncClient(base_url=m.PROXY,cookies=a.cookies,timeout=30) as proxy:
    r=await proxy.post('/api/threads',headers=headers,json={'thread_id':tid,'if_exists':'raise','metadata':{'graph_id':'basic-qa'}})
    result['alice_strict_proxy_create']=r.status_code
    result['proxy_exposes_bob_marker']=r.status_code==200 and r.json().get('metadata',{}).get('review_marker')=='synthetic-private-b'
    read=await proxy.get('/api/threads/'+tid,headers=headers)
    result['alice_proxy_read_after']=read.status_code
    result['read_exposes_bob_marker']=read.status_code==200 and read.json().get('metadata',{}).get('review_marker')=='synthetic-private-b'
   result['alice_native_read_after']=(await lg.get('/threads/'+tid,headers=ha)).status_code
 finally:
  if created:
   m._restore_ownership(tid,cb['current_tenant']['id'],cb['user']['id'])
   async with m.httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:
    result['cleanup_native_delete']=(await lg.delete('/threads/'+tid,headers=m._native_headers(await m._ticket(b,cb)))).status_code
   m._drop_ownership(tid)
  for c in (a,b):
   await c.post('/auth/logout'); await c.aclose()
 print(json.dumps(result,ensure_ascii=False,indent=2))
 Path(str(EVIDENCE_ROOT)+'/live-claim.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
asyncio.run(main())
