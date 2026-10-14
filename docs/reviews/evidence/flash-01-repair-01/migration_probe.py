from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,sys,json,asyncio,uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True)
db=json.load(open(str(EVIDENCE_ROOT / 'fresh-migrations.json')))['databases'][0]
for name,source in [('DATABASE_URI','TENANT_TEST_OWNER_URI'),('ADMIN_RUNTIME_DATABASE_URI','TENANT_TEST_RUNTIME_URI')]: os.environ[name]=os.environ[source].rsplit('/',1)[0]+'/'+db
sys.path.insert(0,str(Path('admin/api').resolve()))
from app.db import OwnerSessionLocal
from sqlalchemy import text
from scripts.migrate_identity import MigrationRunner
import psycopg
async def main():
 uid,tid=str(uuid.uuid4()),str(uuid.uuid4()); account='review_resume_'+uuid.uuid4().hex[:6]; tc='review_migration_'+uuid.uuid4().hex[:6]; calls=[]
 async with OwnerSessionLocal() as s:
  await s.execute(text("INSERT INTO users(id,account,display_name,password_hash) VALUES (:u,:a,:a,'synthetic-unused')"),{'u':uid,'a':account})
  await s.execute(text("CREATE TABLE IF NOT EXISTS thread(thread_id UUID PRIMARY KEY,metadata JSONB NOT NULL DEFAULT '{}'::jsonb)"))
  await s.execute(text('INSERT INTO thread(thread_id) VALUES (:t)'),{'t':tid}); await s.commit()
 manifest={'version':1,'tenants':[{'code':tc,'name':'synthetic migration','create_if_missing':True}],'users':[{'account':account,'source':'pg','pg_user_id':uid}],'memberships':[{'account':account,'tenant_code':tc,'role_codes':['tenant_admin']}],'threads':[{'thread_id':tid,'account':account,'tenant_code':tc}]}
 async def platform_hook(t,tenant,user):
  # API auth 使用另一条非owner连接，与真实 fetch_thread_owner 相同事务可见性。
  with psycopg.connect(os.environ['ADMIN_RUNTIME_DATABASE_URI']) as c:
   c.execute("SELECT set_config('air_agent.tenant_id',%s,true)",(tenant,)); c.execute("SELECT set_config('air_agent.user_id',%s,true)",(user,))
   visible=c.execute('SELECT source FROM thread_ownership WHERE thread_id=%s',(t,)).fetchone()
  calls.append(bool(visible)); return 200 if visible and visible[0]=='migrated' else 403
 r=MigrationRunner(manifest,{},platform_metadata_patch=platform_hook)
 try:
  first=await r.apply(); second=await MigrationRunner(manifest,{},platform_metadata_patch=platform_hook).apply()
  out={'first_metadata_pending':first['applied']['metadata_pending'],'ownership_visible_at_first_api_call':calls[0],'platform_callback_count_after_rerun':len(calls),'second_already_applied':second.get('already_applied',False)}
  print(json.dumps(out,indent=2)); Path(str(EVIDENCE_ROOT)+'/migration-proofs.json').write_text(json.dumps(out,indent=2)+'\n')
 finally:
  async with OwnerSessionLocal() as s:
   await s.execute(text('DELETE FROM thread_ownership WHERE thread_id=:t'),{'t':tid}); await s.execute(text('DELETE FROM thread WHERE thread_id=:t'),{'t':tid}); await s.execute(text('DELETE FROM tenant_memberships WHERE user_id=:u'),{'u':uid}); await s.execute(text('DELETE FROM users WHERE id=:u'),{'u':uid}); await s.execute(text('DELETE FROM tenants WHERE code=:c'),{'c':tc}); await s.execute(text('DELETE FROM migration_ledger WHERE manifest_fingerprint=:f'),{'f':r.fingerprint}); await s.commit()
asyncio.run(main())
