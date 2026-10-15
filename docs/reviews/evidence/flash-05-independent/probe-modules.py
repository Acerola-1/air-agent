import os,sys,json,asyncio,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
r=Path('/Users/acerola/Dev/Python/air_agent');sys.path[:0]=[str(r/'src'),str(r/'admin/api')]
for k in ['POSTGRES_USER','POSTGRES_PASSWORD','MCP_SERVER_URL','MAIN_LLM_API_KEY','SILICONFLOW_API_KEY','DEEPSEEK_API_KEY','XFYUN_MAAS_API_KEY','XIAOMI_MIMO_API_KEY','SERPER_API_KEY','ARK_API_KEY']:os.environ.setdefault(k,'controlled-unused')
from common import capabilities as cap
from common.identity.runtime import RuntimeIdentity
from common.mcp_scoped import ScopedMCPToolFactory
from app.schemas_ledger import CapabilitySelectionIn
from pydantic import ValidationError
async def main():
 out={'kind':'independent_module_and_real_filesystem_probes'}
 payload=json.loads(Path('/tmp/aa05-default-payload.json').read_text())
 try:CapabilitySelectionIn.model_validate(payload);out['frontend_default_payload']={'accepted':True}
 except ValidationError as e:out['frontend_default_payload']={'accepted':False,'errors':[{'loc':list(z['loc']),'type':z['type']} for z in e.errors()]}
 with tempfile.TemporaryDirectory(prefix='aa05-skills-') as td:
  root=Path(td)
  for slug in ['s1','s2']:
   (root/slug).mkdir();(root/slug/'SKILL.md').write_text('SECRET_RULE_'+slug)
  b=cap.BoundedSkillsBackend(root_dir=td,virtual_mode=True);b.set_explicit_skill('s1')
  try:b.read('/s2/SKILL.md');out['read_other_skill']={'blocked':False}
  except PermissionError:out['read_other_skill']={'blocked':True}
  d=b.download_files(['/s2/SKILL.md']);out['download_other_skill']={'returned':str(type(d)), 'non_selected_content_visible':'SECRET_RULE_s2' in repr(d)}
  g=b.grep('SECRET_RULE',path='');out['grep_root']={'non_selected_visible':'s2' in repr(g)}
 ident=RuntimeIdentity(user_id='u',tenant_id='t',membership_id='m',session_id='s')
 skills=[];tools=[cap.MCPToolCatalogItem(f'mcp:s:tool{i}','s',f'tool{i}',f'tool{i}','', 'available') for i in range(5)]
 async def discover(*a,**k):return [],tools
 pages=[];cursor=None
 with patch.object(cap,'list_graph_skills',return_value=skills),patch.object(cap,'discover_authorized_mcp_catalog',discover):
  for _ in range(5):
   p=await cap.get_capabilities_catalog(graph_id='basic-qa',identity=ident,limit=1,cursor=cursor);pages.append([t['id'] for t in p['tools']]);cursor=p['next_cursor']
   if not cursor:break
 out['pagination']={'pages':pages,'still_has_cursor':bool(cursor),'expected_5_distinct':False if len({i for p in pages for i in p})!=5 else True}
 from common.skill_router import SkillSemanticRouter
 with tempfile.TemporaryDirectory(prefix='aa05-router-') as td:
  base=Path(td);(base/'skills').mkdir();(base/'outside').mkdir();(base/'outside'/'SKILL.md').write_text('OTHER_GRAPH_RULE')
  router=SkillSemanticRouter(skills_dir=str(base/'skills'));p=router.resolve_skill_file('../outside');out['router_path_escape']={'outside_path_resolved':p is not None and p.resolve()==(base/'outside'/'SKILL.md').resolve()}
 t=SimpleNamespace(name='query',description='',args_schema={});f=ScopedMCPToolFactory({});f.register_catalog([t],server_key='service-a');f.register_catalog([t],server_key='service_a');out['alias_collision']={'alias':'mcp_service_a__query','resolved_server':f.server_of('mcp_service_a__query'),'stable_ids':sorted(f.known_tool_ids())}
 from common.mcp_scoped import set_factory,build_proxy_tools
 f2=ScopedMCPToolFactory({});f2.register_catalog([t],server_key='controlled-one');f2.register_catalog([t],server_key='controlled-two');set_factory(f2)
 proxies=build_proxy_tools();filtered=cap.filter_capability_mcp_tools(proxies,{'configurable':{'capability_mcp_mode':'allowlist','capability_tool_ids':['mcp:controlled-one:query']}})
 out['normal_two_service_disclosure']={'proxy_names':[p.name for p in proxies],'raw_query_server':f2.server_of('query'),'selected_first_service_disclosed_names':[p.name for p in filtered]}
 set_factory(None)
 Path(r/'docs/reviews/evidence/flash-05-independent/module-probes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
