import os,sys,json,asyncio,uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True);os.environ.update(json.load(open('/tmp/air-agent-ivrp01-env.json'))['env']);sys.path[:0]=[str(Path('admin/api').resolve()),str(Path('admin/api/tests').resolve())]
from sqlalchemy import text
import test_event_ledger_api as fx
async def main():
 gen=fx.ledger_env.__wrapped__();e=await anext(gen);c=await fx._login(await fx._account_of(e['user_a']),e['tenant_a']);th=str(uuid.uuid4());await fx._grant_thread(e['tenant_a'],e['user_a'],th);out={}
 try:
  initial=(await c.post('/agent/requests',json=fx._admit_body(th,'more-initial-key','question'))).json()
  resume=(await c.post('/agent/requests',json={**fx._admit_body(th,'more-resume-key',''),'operation_kind':'resume','source_attempt_id':initial['attempt_id']})).json()
  claim=await c.post('/agent/runs/start-claim',headers={'x-internal-token':fx._internal_token()},json={'submission_id':resume['submission_id'],'attempt_id':resume['attempt_id'],'thread_id':th,'graph_id':'basic-qa','operation_kind':'resume','content':''})
  out['resume']={'same_request':resume['request_id']==initial['request_id'],'same_attempt':resume['attempt_id']==initial['attempt_id'],'start_claim_status':claim.status_code,'decision':claim.json()}
  ad=await c.post('/agent/requests',json={**fx._admit_body(th,'more-content-key','text'),'store_content':True});assert ad.status_code==200
  deletion=await c.post('/agent/threads/deletion',headers={'origin':'http://localhost:3000'},json={'thread_id':th});assert deletion.status_code==200
  rid=ad.json()['request_id'];out['delete_pending_content_status']=(await c.get('/agent/requests/'+rid+'/content')).status_code
  async with fx.OwnerSessionLocal() as s:
   await s.execute(text("UPDATE agent_thread_deletions SET state='completed' WHERE thread_id=:th"),{'th':th});await s.execute(text("UPDATE agent_content_refs SET deleted_at=now() WHERE thread_id=:th"),{'th':th});await s.commit()
  out['after_cleanup']={'request_detail_status':(await c.get('/agent/requests/'+rid)).status_code,'new_write_status':(await c.post('/agent/requests',json=fx._admit_body(th,'more-after-cleanup','new'))).status_code}
 finally:await c.aclose();await gen.aclose()
 Path('docs/reviews/evidence/flash-02-repair-01-independent/resume-deletion.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
asyncio.run(main())
