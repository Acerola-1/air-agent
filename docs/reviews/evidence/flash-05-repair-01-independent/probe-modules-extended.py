from pathlib import Path
exec(Path(__file__).with_name('probe-modules.py').read_text().split('async def main():')[0])
from langchain_core.tools import StructuredTool
async def main():
 out={}
 with tempfile.TemporaryDirectory() as td:
  root=Path(td)
  for slug in ['s1','s2']:
   (root/slug).mkdir();(root/slug/'SKILL.md').write_text('SECRET_RULE_'+slug)
  b=cap.BoundedSkillsBackend(root_dir=td,virtual_mode=True);b.set_explicit_skill('s1')
  out['root_listing']=repr(b.ls('/'))
  for name,fn in [('glob_sdk_signature',lambda:b.glob('*.md',path='/s1')),('glob_author_signature',lambda:b.glob('s1/*.md'))]:
   try:out[name]={'result':repr(fn())}
   except Exception as e:out[name]={'error':type(e).__name__,'message':str(e)}
  try:out['async_glob']=repr(await b.aglob('s1/*.md'))
  except Exception as e:out['async_glob']={'error':type(e).__name__,'message':str(e)}
 from common.mcp_scoped import set_factory,build_proxy_tools
 tool=StructuredTool.from_function(lambda:'ok',name='query',description='review')
 f=ScopedMCPToolFactory({});f.register_catalog([tool],server_key='service-a');f.register_catalog([tool],server_key='service_a');set_factory(f)
 out['colliding_proxy_names']=[t.name for t in build_proxy_tools()]
 set_factory(None)
 import common.observability.skill_adapter as sa
 captures=[]
 ctx=SimpleNamespace(tenant_id='t',attempt_id='a',submission_id='sub')
 sa._LOADED.clear()
 with patch.object(sa,'observability_enabled',return_value=True),patch.object(sa,'observation_context_from_config',return_value=ctx),patch.object(sa,'get_collector') as collector:
  collector.return_value.enqueue.side_effect=lambda item:captures.append(item)
  sa.emit_skill_stage({'configurable':{'capability_selection_id':'selection-id','capability_selection_digest':'trusted-digest'}},'s1','loaded',status='succeeded',body='rule',source='manual')
  sa.record_loaded_execution({},succeeded=True)
  with patch.object(sa,'record_span') as record:
   for item in captures:await item.fn()
   out['skill_span_calls']=[{k:v for k,v in call.kwargs.items() if k in ['stage','selection_source','selection_id','selection_digest','submission_id']} for call in record.call_args_list]
 (r/'docs/reviews/evidence/flash-05-repair-01-independent/modules-extended.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
