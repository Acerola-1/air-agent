import os,sys,json,asyncio,hashlib
from pathlib import Path
import psycopg,httpx
from psycopg.rows import dict_row
r=Path('/Users/acerola/Dev/Python/air_agent');sys.path[:0]=[str(r/'src'),str(r/'admin/api')];env=json.loads(Path('/tmp/aa05f-stack-env.json').read_text());os.environ.update(env)
from common.capabilities import GRAPH_SKILLS_DIRS,calculate_skill_material_version
from common.business_graph.skill_content import load_skill_rules
from common.skill_router import SkillSemanticRouter
OUT=r/'docs/reviews/evidence/takeover-05-final';base=json.loads((OUT/'api-matrix-combined.json').read_text())['cells'];deep=json.loads((OUT/'deep-explicit-fixture.json').read_text())['cells'];cells=[c for c in base if c['explicit'] and c.get('selected_skill')]+deep;i=json.loads(Path('/tmp/aa05f-browser-identity.json').read_text());proof=[]
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8104',timeout=20) as admin,httpx.AsyncClient(base_url='http://127.0.0.1:2026',timeout=20) as native,httpx.AsyncClient(base_url='http://localhost:3104',timeout=20,headers={'origin':'http://localhost:3104'}) as bff:
  ctx=(await admin.post('/auth/login',json={'account':i['account'],'password':i['password']})).json()
  if (ctx.get('current_tenant') or {}).get('id')!=i['tenant']:
   ctx=(await admin.post('/auth/switch-tenant',json={'tenant_id':i['tenant'],'expected_ctx_version':ctx['context_version']})).json()
  res=await admin.post('/auth/runtime-ticket',headers={'x-internal-token':env['INTERNAL_API_TOKEN']},json={'target_tenant_id':i['tenant'],'expected_ctx_version':ctx['context_version']});native.headers['authorization']='Bearer '+res.json()['ticket']
  await bff.post('/api/auth/login',json={'account':i['account'],'password':i['password']})
  bctx=(await bff.get('/api/auth/me')).json()
  if (bctx.get('current_tenant') or {}).get('id')!=i['tenant']:
   bctx=(await bff.post('/api/auth/switch-tenant',json={'tenant_id':i['tenant'],'expected_ctx_version':bctx['context_version']})).json()
  bff.headers.update({'x-tenant-id':i['tenant'],'x-tenant-context-version':str(bctx['context_version'])})
  # 重放确认修复前遗留的wait未知状态，不创建新运行。
  for cell in [v for v in base if v['entry']=='bff']:
   fixed=await bff.post('/api/agent-runs/confirm',json={'thread_id':cell['thread_id'],'run_id':cell['run_id']})
   assert fixed.status_code==200 and fixed.json().get('confirmed'),fixed.text
   cell['confirm_after_fix']=fixed.json();cell['confirm']=fixed.json()
  (OUT/'api-matrix-reconfirmed.json').write_text(json.dumps(base,ensure_ascii=False,indent=2))
  with psycopg.connect(env['DATABASE_URI'],row_factory=dict_row) as c:
   for cell in cells:
    graph=cell['graph'];ad=cell.get('admission') or cell.get('confirm_after_fix') or cell['confirm'];aid=ad['attempt_id'];skill=cell['selected_skill'];slug=skill.split(':')[-1];body=load_skill_rules(SkillSemanticRouter(GRAPH_SKILLS_DIRS[graph]),slug,'fast');
    if graph=='data-analysis':
     folder=GRAPH_SKILLS_DIRS[graph]/slug;body='\n\n=====\n\n'.join([(folder/'SKILL.md').read_text().split('---',2)[2].strip(),(folder/'references'/'fast.md').read_text()])
    body_hash=hashlib.sha256(body.encode()).hexdigest()
    current=calculate_skill_material_version(GRAPH_SKILLS_DIRS[graph]/slug)
    rows=c.execute('SELECT stage,status,skill_version,selection_source FROM agent_capability_spans WHERE attempt_id=%s AND span_kind=\'skill\' ORDER BY created_at',(aid,)).fetchall()
    selection=c.execute('SELECT id::text,selection_digest,material_version FROM agent_capability_selections WHERE attempt_id=%s ORDER BY created_at DESC LIMIT 1',(aid,)).fetchone()
    events=c.execute("SELECT payload FROM agent_events WHERE attempt_id=%s AND event_type='runtime.span' AND payload->>'stage' IN ('loaded','executed')",(aid,)).fetchall()
    record={'graph':graph,'entry':cell['entry'],'attempt_id':aid,'skill_id':skill,'current_material_version':current,'accepted_selection':dict(selection),'expected_loaded_body_sha256':body_hash,'stages':rows,'event_selection_links':[v['payload'] for v in events]};proof.append(record)
    assert selection['material_version']==current,record
    assert any(row['stage']=='loaded' and row['skill_version']==body_hash and row['selection_source']=='manual' for row in rows),record
    assert any(row['stage']=='executed' and row['selection_source']=='manual' for row in rows),record
    assert any(v['payload'].get('selection_id')==selection['id'] and v['payload'].get('selection_digest')==selection['selection_digest'] for v in events),record
    if graph in ['basic-qa','intelligent-analysis','data-analysis']:
     state=(await native.get('/threads/'+cell['thread_id']+'/state')).json();actual=state['values'].get('skill_rules_content');record['checkpoint_rules_match']=actual==body;assert actual==body,record
    else:record['real_answer']=cell['answers']
 (OUT/'skill-stage-real-proof.json').write_text(json.dumps({'passed':True,'cells':proof},ensure_ascii=False,indent=2));print('real loaded/executed/selection correlation',len(proof),'passed')
try:asyncio.run(main())
except BaseException:
 (OUT/'skill-stage-real-proof.json').write_text(json.dumps({'passed':False,'cells':proof},ensure_ascii=False,indent=2));raise
