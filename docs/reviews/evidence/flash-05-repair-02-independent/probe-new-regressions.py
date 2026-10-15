from pathlib import Path
exec(Path(__file__).with_name('probe-modules.py').read_text().split('async def main():')[0])
from langchain_core.tools import StructuredTool
from unittest.mock import AsyncMock
from common.mcp_scoped import set_factory,build_proxy_tools,server_clean_key
from common.middleware.tenant_runtime_guard_middleware import TenantRuntimeGuardMiddleware
from common import mcp_authorization as ma
async def main():
 out={}
 tool=StructuredTool.from_function(lambda:'ok',name='query',description='review')
 f=ScopedMCPToolFactory({});f.register_catalog([tool],server_key='svc_a');f.register_catalog([tool],server_key='svc--a');set_factory(f)
 out['remaining_alias_collision']={'server_encodings':{k:server_clean_key(k) for k in ['svc_a','svc--a']},'proxy_names':[t.name for t in build_proxy_tools()]}
 request=SimpleNamespace(tools=[tool],runtime=None)
 reached=[]
 async def handler(req):reached.append(True);return 'ok'
 with patch('common.middleware.tenant_runtime_guard_middleware.guard_request_call',new=AsyncMock()):
  try:out['actual_production_middleware_plain_local_tool']={'result':await TenantRuntimeGuardMiddleware().awrap_model_call(request,handler)}
  except Exception as e:out['actual_production_middleware_plain_local_tool']={'error':type(e).__name__,'detail':str(e),'handler_reached':bool(reached),'metadata':tool.metadata}
 with patch.dict(os.environ,{'TENANT_MCP_FIXTURE_MODE':'false','DATABASE_URI':'postgresql://unused:unused@invalid/production_test_records','PYTEST_CURRENT_TEST':''}):
  out['fixture_gate_false_but_database_name_contains_test']={'fixture_mode_enabled':ma.fixture_mode_enabled(),'v1_fixture_profile_enabled':ma.resolve_protocol_profile('v1-fixture') is not None,'v1_profile_enabled':ma.resolve_protocol_profile('v1') is not None}
 # Auto mode should not disclose proxies from scopes never authorized for the current identity.
 from common.business_graph.tool_wrappers import _is_mcp_tool
 out['aliased_proxies_recognized_as_mcp']={t.name:_is_mcp_tool(SimpleNamespace(tool_call={'name':t.name},tool=t)) for t in build_proxy_tools()}
 out['auto_disclosure_from_global_catalog']=[t.name for t in cap.filter_capability_mcp_tools(build_proxy_tools(),{'configurable':{'capability_mcp_mode':'auto','langgraph_auth_user':{'tenant_id':'other-tenant','membership_id':'other-member'}}})]
 set_factory(None)
 (r/'docs/reviews/evidence/flash-05-repair-02-independent/new-regressions.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
