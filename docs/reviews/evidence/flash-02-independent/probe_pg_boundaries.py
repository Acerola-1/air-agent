import os,json,sys,asyncio,uuid,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()))
import psycopg
x=json.load(open('/tmp/air-agent-f02-env.json'));os.environ.update(x['env'])
from common.event_ledger import store,worker
async def main():
 t,u=os.environ['LEDGER_TEST_TENANT_ID'],os.environ['LEDGER_TEST_USER_ID'];scope=store.LedgerScope(t,u,u);event=str(uuid.uuid4());out={};old=store._run
 async def fail(sql,*args,**kwargs):
  if sql.startswith('INSERT INTO agent_outbox'):raise store.LedgerBackendUnavailable('synthetic outbox failure')
  return await old(sql,*args,**kwargs)
 store._run=fail
 try:
  await store.write_event(scope,event_type='trace.linked',source='runtime',payload={'provider':'review','trace_id':'synthetic','primary':True},event_id=event)
 except store.LedgerBackendUnavailable:out['injected_outbox_failure']=True
 finally:store._run=old
 out['retry']=await store.write_event(scope,event_type='trace.linked',source='runtime',payload={'provider':'review','trace_id':'synthetic','primary':True},event_id=event)
 async with await psycopg.AsyncConnection.connect(os.environ['LEDGER_TEST_OWNER_URI']) as c:
  out['atomicity']=dict(zip(['events','outbox'],await(await c.execute('SELECT (SELECT count(*) FROM agent_events WHERE event_id=%s),(SELECT count(*) FROM agent_outbox WHERE event_id=%s)',(event,event))).fetchone()))
 # 删除清理最小权限实测：worker声明完成后，原文是否真的失效。
 thread='review_'+uuid.uuid4().hex;deletion=str(uuid.uuid4());content=str(uuid.uuid4())
 async with await psycopg.AsyncConnection.connect(os.environ['LEDGER_TEST_OWNER_URI']) as c:
  await c.execute("INSERT INTO agent_content_refs(id,tenant_id,user_id,membership_id,thread_id,content_snapshot) VALUES(%s,%s,%s,%s,%s,'synthetic')",(content,t,u,u,thread))
  await c.execute("INSERT INTO agent_thread_deletions(tenant_id,thread_id,requested_by) VALUES(%s,%s,%s)",(t,thread,u))
 r=await store.write_event(scope,event_type='resource.deleted',source='server',payload={'resource_type':'thread','resource_id':thread,'deletion_state':'requested','deleted_at':dt.datetime.now(dt.UTC).isoformat()},event_id=deletion,thread_id=thread,consumers=('deletion_cleanup',));assert r['written']
 w=worker.OutboxWorker(batch_size=100);tasks=await w._claim();task=next(task for task in tasks if task.event_id==deletion);await w._handle(task);await w._finish(task)
 async with await psycopg.AsyncConnection.connect(os.environ['LEDGER_TEST_OWNER_URI']) as c:
  row=await(await c.execute('SELECT d.state,c.deleted_at,o.status FROM agent_thread_deletions d JOIN agent_content_refs c USING(tenant_id,thread_id) JOIN agent_outbox o ON o.event_id=%s WHERE c.id=%s',(deletion,content))).fetchone();out['deletion']={'state':row[0],'content_marked_deleted':row[1] is not None,'outbox_state':row[2]}
 # 已完成事件的重建必须可重放：当前实现只选未done任务。
 await store.write_event(scope,event_type='trace.linked',source='runtime',payload={'provider':'review','trace_id':'replay','primary':False},event_id=str(uuid.uuid4()))
 w=worker.OutboxWorker(batch_size=100);await w.run_once();rr=await worker.replay(tenant_id=t,consumer_version=2,dry_run=True);out['rebuild_v2']=rr
 await store.close_pool();Path('docs/reviews/evidence/flash-02-independent/pg-boundaries.json').write_text(json.dumps(out,default=str,indent=2)+'\n');print(json.dumps(out,default=str,indent=2))
asyncio.run(main())
