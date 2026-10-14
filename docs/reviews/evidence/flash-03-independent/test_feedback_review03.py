import asyncio,datetime as dt,json,uuid
from pathlib import Path
import pytest
from httpx import ASGITransport
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import AsyncSession
from app.main import app
from app.db import OwnerSessionLocal
from app.models_feedback import AgentFeedbackCurrent
from tests.test_feedback_api import _make_confirmed_answer,_fb_payload,_bev
from tests.test_event_ledger_api import ledger_env,_grant_thread,_login,_account_of

OUT=Path('/Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-03-independent/backend-probes.json')
pytestmark=pytest.mark.asyncio

def record(name,data):
 old=json.loads(OUT.read_text()) if OUT.exists() else {}
 old[name]=data;OUT.write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n')

async def fixture_answer(env,label):
 thread=str(uuid.uuid4());await _grant_thread(env['tenant_a'],env['user_a'],thread)
 client=await _login(await _account_of(env['user_a']),env['tenant_a'])
 client._transport=ASGITransport(app=app,raise_app_exceptions=False)
 data=await _make_confirmed_answer(client,env,thread,'rv03-'+label+'-'+uuid.uuid4().hex[:12],user_message_id=uuid.uuid4().hex)
 return client,thread,data

async def counts(query,params):
 async with OwnerSessionLocal() as s:return (await s.execute(text(query),params)).scalar()

async def test_real_concurrent_cas(ledger_env,monkeypatch):
 client,thread,data=await fixture_answer(ledger_env,'cas')
 aid=data['answer']['answer_id'];url='/agent/feedback/answers/'+aid
 try:
  first=await client.put(url,json=_fb_payload('positive'));assert first.status_code==200
  original=AsyncSession.execute;gate=asyncio.Event();reads=0
  async def scheduled(self,statement,*args,**kw):
   nonlocal reads
   result=await original(self,statement,*args,**kw)
   ds=getattr(statement,'column_descriptions',[])
   if len(ds)==1 and ds[0].get('entity') is AgentFeedbackCurrent and getattr(statement,'_for_update_arg',None) is None:
    reads+=1
    if reads==2:gate.set()
    await asyncio.wait_for(gate.wait(),5)
   return result
  monkeypatch.setattr(AsyncSession,'execute',scheduled)
  responses=await asyncio.gather(client.put(url,json=_fb_payload('negative',expected_revision=1)),client.put(url,json=_fb_payload('none',expected_revision=1)))
  monkeypatch.setattr(AsyncSession,'execute',original)
  values={'statuses':[r.status_code for r in responses],'result_revisions':[r.json().get('revision') if r.status_code==200 else None for r in responses],'history_result_revisions':[]}
  async with OwnerSessionLocal() as s:
   values['history_result_revisions']=list((await s.execute(text('SELECT result_revision FROM agent_feedback_operations WHERE answer_id=:a ORDER BY id'),{'a':aid})).scalars())
  record('concurrent_cas',values)
  assert sorted(values['statuses'])==[200,409],values
 finally:await client.aclose()

async def test_idempotency_payload_and_original_result(ledger_env):
 c,t,d=await fixture_answer(ledger_env,'idem');aid=d['answer']['answer_id'];url='/agent/feedback/answers/'+aid
 try:
  op='audit-'+uuid.uuid4().hex;body=_fb_payload('negative',operation_id=op,comment='synthetic first comment')
  first=await c.put(url,json=body);assert first.status_code==200
  altered=await c.put(url,json={**body,'comment':'synthetic changed comment'})
  c2,t2,d2=await fixture_answer(ledger_env,'idem-target')
  try:target=await c2.put('/agent/feedback/answers/'+d2['answer']['answer_id'],json=body)
  finally:await c2.aclose()
  later=await c.put(url,json=_fb_payload('positive',expected_revision=1));assert later.status_code==200
  replay=await c.put(url,json=body)
  result={'changed_comment_status':altered.status_code,'changed_target_status':target.status_code,'changed_target_returns_old_answer':target.json().get('answer_id')==aid,'original_revision':first.json()['revision'],'later_revision':later.json()['revision'],'replay_revision':replay.json().get('revision'),'replay_rating':replay.json().get('rating')}
  record('idempotency',result)
  assert altered.status_code==409 and target.status_code==409 and replay.json()['revision']==first.json()['revision'],result
 finally:await c.aclose()

async def test_copy_replay_outbox_and_mixed_batch(ledger_env):
 c,t,d=await fixture_answer(ledger_env,'copy');aid=d['answer']['answer_id']
 try:
  event=_bev('browser.copy',{'answer_id':aid,'copy_scope':'full'});headers={'origin':'http://localhost:3000'}
  first=await c.post('/agent/events/browser',json={'events':[event]},headers=headers)
  outboxes=await counts('SELECT count(*) FROM agent_outbox WHERE event_id=:e',{'e':event['event_id']})
  replay=await c.post('/agent/events/browser',json={'events':[event]},headers=headers)
  fresh=_bev('browser.copy',{'answer_id':aid,'copy_scope':'full'})
  mixed=await c.post('/agent/events/browser',json={'events':[fresh,event]},headers=headers)
  fresh_count=await counts('SELECT count(*) FROM agent_events WHERE event_id=:e',{'e':fresh['event_id']})
  result={'first_status':first.status_code,'outbox_count':outboxes,'replay_status':replay.status_code,'mixed_status':mixed.status_code,'valid_new_event_survived':fresh_count}
  record('copy_replay_and_outbox',result)
  assert first.status_code==200 and outboxes==1 and replay.status_code==200 and mixed.status_code==200 and fresh_count==1,result
 finally:await c.aclose()

async def test_deleted_feedback_and_timing(ledger_env):
 c,t,d=await fixture_answer(ledger_env,'delete');aid=d['answer']['answer_id'];body=_fb_payload('positive')
 try:
  first=await c.put('/agent/feedback/answers/'+aid,json=body);assert first.status_code==200
  deletion=await c.post('/agent/threads/deletion',json={'thread_id':t},headers={'origin':'http://localhost:3000'});assert deletion.status_code==200,deletion.text
  replay=await c.put('/agent/feedback/answers/'+aid,json=body)
  listing=await c.get('/agent/feedback/operations',params={'answer_id':aid})
  timing=_bev('browser.timing',{'attempt_id':d['attempt_id'],'clock':'monotonic','first_visible_answer_ms':3,'measurement_id':'deleted-measurement'})
  after=await c.post('/agent/events/browser',json={'events':[timing]},headers={'origin':'http://localhost:3000'})
  metric=await c.get('/agent/feedback/metrics')
  result={'deletion_status':deletion.status_code,'replay_after_delete':replay.status_code,'visible_deleted_operations':len(listing.json().get('items',[])),'late_timing_accepted':after.json()['items'][0]['valid'],'metrics_eligible_after_delete':metric.json()['feedback']['eligible_answers']}
  record('deletion',result)
  assert replay.status_code==404 and not result['visible_deleted_operations'] and not result['late_timing_accepted'] and result['metrics_eligible_after_delete']==0,result
 finally:await c.aclose()

async def test_copy_answer_cohort_and_future_time(ledger_env):
 clients=[];records=[]
 try:
  for n in range(3):
   c,t,d=await fixture_answer(ledger_env,'cohort'+str(n));clients.append(c);records.append(d)
  old=dt.datetime(2026,11,1,12,tzinfo=dt.UTC);start=dt.datetime(2026,11,2,tzinfo=dt.UTC);end=start+dt.timedelta(days=1)
  async with OwnerSessionLocal() as s:
   for i,d in enumerate(records):await s.execute(text('UPDATE agent_answers SET confirmed_at=:ts WHERE id=:a'),{'ts':old if i<2 else start+dt.timedelta(hours=1),'a':d['answer']['answer_id']})
   await s.commit()
  future_result=None
  for i in range(2):
   event=_bev('browser.copy',{'answer_id':records[i]['answer']['answer_id'],'copy_scope':'full'})
   event['occurred_at']=(dt.datetime.now(dt.UTC)+dt.timedelta(days=100)).isoformat().replace('+00:00','Z')
   res=await clients[i].post('/agent/events/browser',json={'events':[event]},headers={'origin':'http://localhost:3000'})
   future_result=res.json()['items'][0]['valid']
   async with OwnerSessionLocal() as s:
    await s.execute(text('UPDATE agent_events SET occurred_at=:ts WHERE event_id=:e'),{'ts':start+dt.timedelta(hours=2),'e':event['event_id']});await s.commit()
  metric=await clients[0].get('/agent/feedback/metrics',params={'start':start.isoformat(),'end':end.isoformat()})
  result={'future_copy_accepted':future_result,**metric.json()['copy']};record('copy_cohort',result)
  assert future_result is False and result['distinct_copied_answers']==0 and result['copy_rate']==0,result
 finally:
  for c in clients:await c.aclose()
