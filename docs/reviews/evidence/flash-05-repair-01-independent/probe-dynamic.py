from pathlib import Path
exec(Path(__file__).with_name('probe-modules.py').read_text().split('async def main():')[0])
import importlib
from langchain_core.tools import StructuredTool
from common.mcp_scoped import set_factory,build_proxy_tools
f=ScopedMCPToolFactory({});set_factory(f)
mods=[importlib.import_module(n+'.graph') for n in ['deep_research','intelligent_report','intelligent_tracing']]
before={m.__name__:len(m._mcp_tools) for m in mods}
tool=StructuredTool.from_function(lambda:'ok',name='late_query',description='late query')
f.register_catalog([tool],server_key='controlled-one')
out={'kind':'actual_imported_production_graph_objects_no_model_calls','fixed_graph_mcp_lists_before':before,'registry_proxies_after':[t.name for t in build_proxy_tools()],'fixed_graph_mcp_lists_after':{m.__name__:[t.name for t in m._mcp_tools] for m in mods},'note':'生产中间件只filter(request.tools)，没有追加新发现工具；此探针是对象装配证据，未冒充实际模型/原生运行'}
(r/'docs/reviews/evidence/flash-05-repair-01-independent/dynamic-graph-objects.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
