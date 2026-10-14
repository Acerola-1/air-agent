import asyncio,os,sys,json,uuid,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()));x=json.load(open('/tmp/air-agent-f02-env.json'));os.environ.update(x['env'])
import psycopg
from common.event_ledger import store,worker
async def main():
 t,u=os.environ['LEDGER_TEST_TENANT_ID'],os.environ['LEDGER_TEST_USER_ID'];scope=store.LedgerScope(t,u,u);event=str(uuid.uuid4());start=dt.datetime.now(dt.UTC)
 await store.write_event(scope,event_type='trace.linked',source='runtime',payload={'provider':'review','trace_id':'rebuild_isolated','primary':False},event_id=event)
 w=worker.OutboxWorker(batch_size=100);tasks=await w._claim();task=next(z for z in tasks if z.event_id==event);await w._handle(task);await w._finish(task)
 result=await worker.replay(tenant_id=t,consumer_version=2,since=start,until=dt.datetime.now(dt.UTC),dry_run=True)
 async with await psycopg.AsyncConnection.connect(os.environ['LEDGER_TEST_OWNER_URI']) as c:
  outbox=(await(await c.execute('SELECT status FROM agent_outbox WHERE event_id=%s',(event,))).fetchone())[0]
 out={'completed_event_status':outbox,'new_consumer_version':2,'would_rebuild_from_completed_event':result['would_replay']};Path('docs/reviews/evidence/flash-02-independent/rebuild.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out));await store.close_pool()
asyncio.run(main())
