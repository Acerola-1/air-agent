from pathlib import Path
exec(Path(__file__).with_name('probe-modules.py').read_text().split('async def main():')[0])
os.environ['AGENT_OBSERVABILITY_ENABLED']='false';os.environ['LANGFUSE_ENABLED']='false'
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage,HumanMessage
class ControlledModel(FakeMessagesListChatModel):
 def bind_tools(self,*args,**kwargs):return self
from common.models import ModelRegistry
ModelRegistry.main_llm=ControlledModel(responses=[AIMessage(content='controlled success')])
import importlib,importlib.util
spec=importlib.util.spec_from_file_location('aa05_baseline_guard',str(Path(__file__).with_name('baseline-guard.py')));baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
from common.middleware.tenant_runtime_guard_middleware import TenantRuntimeGuardMiddleware
from unittest.mock import AsyncMock
async def main():
 out={'kind':'actual_three_production_DeepAgents_graph_ainvoke_with_controlled_model','guard_isolation':'Only identity guard is patched to allow; all model/tool middleware and compiled graphs are production implementations. No provider network.'}
 for n in ['deep_research','intelligent_report','intelligent_tracing']:
  module=importlib.import_module(n+'.graph')
  with patch('common.middleware.tenant_runtime_guard_middleware.guard_request_call',new=AsyncMock()):
   try:
    result=await module.graph.ainvoke({'messages':[HumanMessage(content='受控审计问候')]},{'configurable':{'capability_mcp_mode':'allowlist','capability_tool_ids':[]}})
    out[n]={'ok':True,'last_message':str(result.get('messages',[])[-1].content)[:100]}
   except Exception as e:out[n]={'ok':False,'error':type(e).__name__,'detail':str(e)}
 out['positive_control_original_guard']={}
 with patch.object(TenantRuntimeGuardMiddleware,'awrap_model_call',baseline.TenantRuntimeGuardMiddleware.awrap_model_call),patch.object(baseline,'guard_request_call',new=AsyncMock()):
  for n in ['deep_research','intelligent_report','intelligent_tracing']:
   module=importlib.reload(importlib.import_module(n+'.graph'))
   try:
    result=await module.graph.ainvoke({'messages':[HumanMessage(content='受控审计问候')]},{'configurable':{'capability_mcp_mode':'allowlist','capability_tool_ids':[]}})
    out['positive_control_original_guard'][n]={'ok':True,'last_message':str(result.get('messages',[])[-1].content)[:100]}
   except Exception as e:out['positive_control_original_guard'][n]={'ok':False,'error':type(e).__name__,'detail':str(e)}
 (r/'docs/reviews/evidence/flash-05-repair-02-independent/production-sdk-graphs.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
asyncio.run(main())
