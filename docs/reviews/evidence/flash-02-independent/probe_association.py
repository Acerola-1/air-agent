import asyncio,os,sys,json,uuid
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()));os.environ.update(json.load(open('/tmp/air-agent-f02-env.json'))['env'])
import psycopg
from common.event_ledger import store
async def main():
 t,u=os.environ['LEDGER_TEST_TENANT_ID'],os.environ['LEDGER_TEST_USER_ID'];bt,bu,request,attempt=[str(uuid.uuid4()) for _ in range(4)]
 async with await psycopg.AsyncConnection.connect(os.environ['LEDGER_TEST_OWNER_URI']) as c:
  await c.execute("INSERT INTO agent_requests(id,tenant_id,user_id,membership_id,thread_id,graph_id,operation_kind) VALUES(%s,%s,%s,%s,'synthetic-other','basic-qa','initial')",(request,bt,bu,bu))
  await c.execute("INSERT INTO agent_run_attempts(id,tenant_id,user_id,membership_id,request_id,graph_id,attempt_index,state) VALUES(%s,%s,%s,%s,%s,'basic-qa',1,'admitting')",(attempt,bt,bu,bu,request))
 result=await store.write_event(store.LedgerScope(t,u,u),event_type='trace.linked',source='runtime',payload={'provider':'review','trace_id':'synthetic_cross_tenant','primary':False},attempt_id=attempt)
 out={'cross_tenant_attempt_association_accepted':result['written'],'reason':result['reason']};Path('docs/reviews/evidence/flash-02-independent/association.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out));await store.close_pool()
asyncio.run(main())
