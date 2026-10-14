import os,sys,json,asyncio,uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True);os.environ.update(json.load(open('/tmp/air-agent-f02-env.json'))['env']);sys.path[:0]=[str(Path('admin/api').resolve()),str(Path('admin/api/tests').resolve())]
from sqlalchemy import text
import test_event_ledger_api as fx
async def main():
 g=fx.ledger_env.__wrapped__();env=await anext(g);owner=await fx._login(await fx._account_of(env['user_a']),env['tenant_a']);other=await fx._login(await fx._account_of(env['user_b']),env['tenant_a']);thread=str(uuid.uuid4());out={}
 try:
  await fx._grant_thread(env['tenant_a'],env['user_a'],thread)
  admit=await owner.post('/agent/requests',json={**fx._admit_body(thread,'review_'+uuid.uuid4().hex),'store_content':True});admit.raise_for_status();rid=admit.json()['request_id']
  resumed=await owner.post('/agent/requests',json={**fx._admit_body(thread,'resume_'+uuid.uuid4().hex),'operation_kind':'resume','content':''});out['resume_admission']={'status':resumed.status_code,'creates_new_request':resumed.status_code==200 and resumed.json()['request_id']!=rid}
  deleted=await other.post('/agent/threads/deletion',json={'thread_id':thread},headers={'origin':'http://localhost:3000'});out['other_member_deletion_status']=deleted.status_code
  detail=await owner.get('/agent/requests/'+rid);content=await owner.get('/agent/requests/'+rid+'/content');out['after_delete_requested']={'detail_status':detail.status_code,'content_status':content.status_code,'content_still_returned':bool(content.json().get('contents'))}
  async with fx.OwnerSessionLocal() as session:
   await session.execute(text("UPDATE agent_content_refs SET retention_until=now()-interval '1 day' WHERE thread_id=:th"),{'th':thread});await session.commit()
  expired=await owner.get('/agent/requests/'+rid+'/content');out['expired_content_still_returned']=expired.status_code==200 and bool(expired.json().get('contents'))
 finally:
  await owner.aclose();await other.aclose();await g.aclose()
 Path('docs/reviews/evidence/flash-02-independent/deletion-api.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
asyncio.run(main())
