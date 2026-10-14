from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,asyncio,json,uuid,importlib.util,time,base64,hmac,hashlib
from pathlib import Path
from dotenv import load_dotenv
import psycopg
load_dotenv('.env',override=True)
os.environ['REVIEW_FIXTURE_PASSWORD']=os.environ['REVIEW_FIXTURE_PASSWORD']
s=importlib.util.spec_from_file_location('live','tests/integration_tests/test_live_tenant_claims.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def signed(thread,tenant,user):
 raw=os.environ['RUNTIME_TICKET_KEYS'].strip().strip("'");keys=json.loads(raw);kid,secret=next(iter(keys.items())) if isinstance(keys,dict) else ('v1',keys[0])
 now=int(time.time()); p={'typ':'mt-v1','thread':thread,'tenant':tenant,'user':user,'aud':'langgraph-migration','iat':now,'exp':now+60}
 enc=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
 body=enc(json.dumps({'kid':kid,'p':p},separators=(',',':')).encode()); return body+'.'+enc(hmac.new(secret.encode(),body.encode(),hashlib.sha256).digest())
async def main():
 b,cb=await m._login('acc_bob');cb=await m._switch(b,cb,m._pick_tenant(cb,'B') or cb['tenants'][-1]['tenant']['id']);tid=str(uuid.uuid4());tenant,user=cb['current_tenant']['id'],cb['user']['id']; out={};created=False
 try:
  hb=m._native_headers(await m._ticket(b,cb))
  async with m.httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:
   r=await lg.post('/threads',headers=hb,json={'thread_id':tid,'metadata':{'tenant_id':tenant,'user_id':user}});assert r.status_code==200;created=True
   assert await m._confirm_claim(hb['Authorization'][7:],tid,tenant,user);m._drop_ownership(tid)
   mh=m._native_headers(signed(tid,tenant,user)); body={'metadata':{'tenant_id':tenant,'user_id':user}}
   with psycopg.connect(m.OWNER_URI) as conn:
    conn.execute("INSERT INTO thread_ownership(thread_id,tenant_id,user_id,source) VALUES (%s,%s,%s,'migrated')",(tid,tenant,user))
    r=await lg.patch('/threads/'+tid,headers=mh,json=body);out['migration_patch_before_ownership_commit']=r.status_code
    conn.commit()
   r=await lg.patch('/threads/'+tid,headers=mh,json=body);out['migration_patch_after_ownership_commit']=r.status_code
 finally:
  if created:
   m._restore_ownership(tid,tenant,user)
   async with m.httpx.AsyncClient(base_url=m.NATIVE,timeout=20) as lg:out['cleanup_native_delete']=(await lg.delete('/threads/'+tid,headers=m._native_headers(await m._ticket(b,cb)))).status_code
   m._drop_ownership(tid)
  await b.post('/auth/logout');await b.aclose()
 print(json.dumps(out,indent=2));Path(str(EVIDENCE_ROOT)+'/migration-live.json').write_text(json.dumps(out,indent=2)+'\n')
asyncio.run(main())
