import os,uuid
import pytest,psycopg
from sqlalchemy import text
from app.db import OwnerSessionLocal
from tests.test_event_ledger_api import ledger_env
from test_feedback_review03 import fixture_answer,record
pytestmark=pytest.mark.asyncio

async def test_rls_positive_and_parent_cleanup(ledger_env):
 c,t,d=await fixture_answer(ledger_env,'rls-integrity');aid=d['answer']['answer_id']
 try:
  r=await c.put('/agent/feedback/answers/'+aid,json={'operation_id':'audit-rls-'+uuid.uuid4().hex,'rating':'positive','expected_revision':0});assert r.status_code==200
  uri=os.environ['ADMIN_RUNTIME_DATABASE_URI'].replace('postgresql+psycopg://','postgresql://')
  async with await psycopg.AsyncConnection.connect(uri,autocommit=True) as p:
   noctx=(await (await p.execute('SELECT count(*) FROM agent_feedback_current')).fetchone())[0]
   async with p.transaction():
    await p.execute("SELECT set_config('air_agent.tenant_id',%s,true),set_config('air_agent.user_id',%s,true)",(str(ledger_env['tenant_a']),str(ledger_env['user_a'])))
    own=(await (await p.execute('SELECT count(*) FROM agent_feedback_current')).fetchone())[0]
   after=(await (await p.execute('SELECT count(*) FROM agent_feedback_current')).fetchone())[0]
   async with p.transaction():
    await p.execute("SELECT set_config('air_agent.tenant_id',%s,true),set_config('air_agent.user_id',%s,true)",(str(ledger_env['tenant_b']),str(ledger_env['user_a'])))
    other=(await (await p.execute('SELECT count(*) FROM agent_feedback_current')).fetchone())[0]
  async with OwnerSessionLocal() as s:
   fk=(await s.execute(text("SELECT count(*) FROM pg_constraint WHERE conrelid='agent_feedback_current'::regclass AND contype='f'"))).scalar()
   await s.execute(text('DELETE FROM agent_answers WHERE id=:a'),{'a':aid});await s.commit()
   orphan=(await s.execute(text('SELECT count(*) FROM agent_feedback_current WHERE answer_id=:a'),{'a':aid})).scalar()
  values={'without_context':noctx,'own_positive_control':own,'other_tenant':other,'after_transaction_no_context':after,'current_foreign_keys':fk,'current_rows_after_parent_delete':orphan}
  record('rls_and_parent_integrity',values)
  assert [noctx,own,other,after]==[0,1,0,0]
  assert fk>=1 and orphan==0,values
 finally:await c.aclose()

async def test_comment_requires_both_permissions(ledger_env):
 from tests.test_event_ledger_api import _make_platform_user,_make_membership,_account_of,_login
 c,t,d=await fixture_answer(ledger_env,'comment-permissions');aid=d['answer']['answer_id'];op='audit-comment-'+uuid.uuid4().hex
 try:
  submitted=await c.put('/agent/feedback/answers/'+aid,json={'operation_id':op,'rating':'negative','comment':'synthetic-private-comment-R03','expected_revision':0});assert submitted.status_code==200
  reader_id=await _make_platform_user('review03_content_'+uuid.uuid4().hex[:12],[])
  await _make_membership(ledger_env['tenant_a'],reader_id,['agent:content:read'])
  reader=await _login(await _account_of(reader_id),ledger_env['tenant_a'])
  try:
   metadata=await reader.get('/agent/feedback/operations')
   opinion=await reader.get('/agent/feedback/operations/'+op+'/comment')
  finally:await reader.aclose()
  values={'operations_without_operations_permission':metadata.status_code,'comment_without_operations_permission':opinion.status_code,'synthetic_private_comment_visible':opinion.status_code==200 and opinion.json().get('comment')=='synthetic-private-comment-R03'}
  record('comment_permission_split',values)
  assert opinion.status_code==403,values
 finally:await c.aclose()

async def test_batch_bytes_limit_is_enforced(ledger_env):
 import json
 from tests.test_feedback_api import _bev
 c,t,d=await fixture_answer(ledger_env,'batch-bytes')
 try:
  events=[_bev('browser.copy',{'answer_id':d['answer']['answer_id'],'copy_scope':'full','client_operation_id':'S'*10000+str(i)}) for i in range(30)]
  body={'events':events};size=len(json.dumps(body,ensure_ascii=False).encode())
  r=await c.post('/agent/events/browser',json=body,headers={'origin':'http://localhost:3000'})
  result={'bytes':size,'status':r.status_code,'accepted':r.json().get('accepted'),'written':r.json().get('written'),'batch_reason':r.json().get('batch_reason')}
  record('batch_size_limit',result)
  assert not result['accepted'] and result['written']==0,result
 finally:await c.aclose()
