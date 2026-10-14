import os,sys,json,asyncio,uuid
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()));os.environ.update(json.load(open('/tmp/air-agent-f02-env.json'))['env'])
import psycopg
from common.event_ledger import store,worker
async def main():
 t,u=os.environ['LEDGER_TEST_TENANT_ID'],os.environ['LEDGER_TEST_USER_ID'];event=str(uuid.uuid4());await store.write_event(store.LedgerScope(t,u,u),event_type='trace.linked',source='runtime',payload={'provider':'review','trace_id':'receipt','primary':False},event_id=event)
 w=worker.OutboxWorker(batch_size=100);task=next(x for x in await w._claim() if x.event_id==event);calls=0;original=w._apply_run_projection
 async def count(c,z):
  nonlocal calls
  calls+=1;await original(c,z)
 w._apply_run_projection=count;await w._handle(task);await w._handle(task)
 async with await psycopg.AsyncConnection.connect(os.environ['LEDGER_TEST_OWNER_URI']) as c:
  receipts=(await(await c.execute('SELECT count(*) FROM agent_consumer_receipts WHERE event_id=%s',(event,))).fetchone())[0]
 out={'same_event_handler_invocations':calls,'receipts':receipts};Path('docs/reviews/evidence/flash-02-independent/receipt.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out));await store.close_pool()
asyncio.run(main())
