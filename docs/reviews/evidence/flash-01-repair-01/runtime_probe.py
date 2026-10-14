from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,sys,json,asyncio
from pathlib import Path
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path('src').resolve()))
os.environ.update(LANGFUSE_ENABLED='false',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',TENANT_ENFORCE_RUNTIME_IDENTITY='true')
from common.identity.runtime import RuntimeIdentity,revalidate
from common.identity.db import IdentityState
from common.business_graph.nodes import BusinessGraphNodes
from langchain_core.messages import HumanMessage,AIMessage
from common.text_reduction import summarize_complete_text
async def main():
 out={}; active=IdentityState(True,True,3,True,'synthetic',99,'active',99,'t','active',99)
 with patch('common.identity.runtime.fetch_identity_state',AsyncMock(return_value=active)):
  try: await revalidate(RuntimeIdentity('u','t','m','s')); out['missing_revision_snapshot_allowed']=True
  except Exception: out['missing_revision_snapshot_allowed']=False
 nodes=BusinessGraphNodes(router=None,base_system_prompt='',load_skill_tool=None,graph_name='basic_qa')
 nodes._available_tools=lambda state: []
 from types import SimpleNamespace
 current={'revoked':False,'calls':0}
 good=IdentityState(True,True,1,True,'synthetic',0,'active',1,'t','active',1)
 async def state(*a,**kw): return active if current['revoked'] else good
 async def invoke(*a,**kw):
  current['calls']+=1
  if current['calls']==1:
   current['revoked']=True
   raise RuntimeError('synthetic transient then revoked')
  return AIMessage(content='ok')
 user={'identity':'u','tenant_id':'t','membership_id':'m','session_id':'s','session_ctx_version':1,'user_auth_revision':0,'tenant_auth_revision':1,'membership_revision':1}
 with patch('common.identity.runtime.fetch_identity_state',AsyncMock(side_effect=state)) as dbcheck, patch('common.business_graph.nodes.enforce_request_budget',return_value={}), patch('common.business_graph.nodes.fit_tool_schemas',return_value=[]), patch('common.business_graph.nodes.emit_event'), patch('common.business_graph.nodes.ModelRegistry') as models:
  models.main_llm=SimpleNamespace(ainvoke=invoke)
  await nodes.call_model({'messages':[HumanMessage(content='synthetic')]},{'configurable':{'langgraph_auth_user':user}},writer=lambda *a:None)
  out['model_attempts']=current['calls']; out['runtime_checks_during_model_retry']=dbcheck.await_count
  out['model_retry_sent_after_simulated_revision_revoke']=current['calls']>1 and current['revoked']
 with patch('common.identity.runtime.fetch_identity_state',AsyncMock(return_value=None)) as state, patch('common.text_reduction.ModelRegistry') as models:
  models.summary_llm.ainvoke=AsyncMock(return_value=AIMessage(content='summary'))
  await summarize_complete_text('synthetic text',instruction='summarize')
  out['summary_external_calls_without_identity']=models.summary_llm.ainvoke.await_count; out['summary_identity_checks']=state.await_count
 print(json.dumps(out,ensure_ascii=False,indent=2)); Path(str(EVIDENCE_ROOT)+'/runtime-proofs.json').write_text(json.dumps(out,indent=2)+'\n')
asyncio.run(main())
