"""真实生产 DeepAgents 主/子任务 + PG 身份 + 独立 HTTP MCP；模型输出受控。"""
from pathlib import Path
exec(Path(__file__).with_name('probe-live.py').read_text().split('async def main():')[0])
import importlib, secrets, tempfile, statistics, time
from common.capabilities import BoundedSkillsBackend, GRAPH_SKILLS_DIRS
from starlette.requests import Request
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from common.models import ModelRegistry
from common.mcp_scoped import get_factory, set_factory, ScopedMCPToolFactory, build_proxy_tools
from common.identity.langgraph_auth import authenticate
from mcp_fixture.server import FixtureServer
os.environ.update(TENANT_MCP_FIXTURE_MODE='true',TENANT_ENFORCE_RUNTIME_IDENTITY='true',LANGFUSE_ENABLED='false',AGENT_OBSERVABILITY_ENABLED='false', REVIEW_FIXTURE_TOK='tok-tenant-a')
class RecordingBackend(BoundedSkillsBackend):
    reads=[]
    async def aread(self,path,*args,**kwargs):
        result=await super().aread(path,*args,**kwargs)
        self.reads.append({'path':path,'contains_s1':'CONTROLLED_RULE_s1' in repr(result)})
        return result
class ControlledModel(FakeMessagesListChatModel):
    disclosed: list[list[str]] = []
    def bind_tools(self, tools, **kwargs):
        self.disclosed.append([(t.get('function',t).get('name') if isinstance(t,dict) else getattr(t,'name',str(t))) for t in tools]);return self

def tool_message(name,args,id):
    return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':id}])
async def main():
    out={'kind':'actual_production_DeepAgents_SDK_PG_guard_real_HTTP_MCP_controlled_model', 'graphs':{}}
    fixture=FixtureServer();url=await fixture.start()
    try:
        ids={k:str(uuid.uuid4()) for k in ['tenant','user','member']};account='sdk05f_'+uuid.uuid4().hex[:8];pw=secrets.token_urlsafe(20)
        async with await psycopg.AsyncConnection.connect(own) as c:
            await c.execute("INSERT INTO tenants(id,code,name,status) VALUES(%s,%s,'SDK验收','active')",(ids['tenant'],account))
            await c.execute("INSERT INTO users(id,account,display_name,password_hash,is_active) VALUES(%s,%s,'SDK验收',%s,true)",(ids['user'],account,hash_password(pw)))
            await c.execute("INSERT INTO tenant_memberships(id,tenant_id,user_id,status) VALUES(%s,%s,%s,'active')",(ids['member'],ids['tenant'],ids['user']))
            await c.execute("INSERT INTO mcp_identity_bindings(tenant_id,membership_id,server_key,external_subject,credential_ref,protocol_version,status) VALUES(%s,%s,'controlled-one','subject-a','env:REVIEW_FIXTURE_TOK','v1-fixture','active')",(ids['tenant'],ids['member']));await c.commit()
        os.environ['RUNTIME_TICKET_KEYS']=json.dumps({'final':secrets.token_urlsafe(32)})
        get_settings().runtime_ticket_keys=os.environ['RUNTIME_TICKET_KEYS']
        async with ContextClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            res=await client.post('/auth/login',json={'account':account,'password':pw});assert res.status_code==200,res.text
            me=res.json()
            res=await client.post('/auth/runtime-ticket',headers={'x-internal-token':os.environ['INTERNAL_API_TOKEN']},json={'target_tenant_id':ids['tenant'],'expected_ctx_version':me['context_version']});assert res.status_code==200,res.text
            actor=await authenticate(Request({'type':'http','headers':[(b'authorization',('Bearer '+res.json()['ticket']).encode())]}))
        factory=ScopedMCPToolFactory({'controlled-one':url});set_factory(factory)
        model=ControlledModel(responses=[AIMessage(content='受控本地执行')]);ModelRegistry.main_llm=model
        module=importlib.reload(importlib.import_module('deep_research.graph'))
        cfg={'configurable':{'langgraph_auth_user':actor,'capability_tool_ids':['mcp:controlled-one:query']}}
        with tempfile.TemporaryDirectory(prefix='aa05f-benchmark-skill-') as td:
            root=Path(td);(root/'s1').mkdir();(root/'s1'/'SKILL.md').write_text('---\nname: s1\ndescription: 受控性能材料\n---\n这是已选技能规则，请只回答验收成功。')
            old=GRAPH_SKILLS_DIRS['deep-research'];GRAPH_SKILLS_DIRS['deep-research']=root
            rounds=[]
            try:
                async def execute(explicit):
                    target=cfg['configurable'];target['capability_mcp_mode']='allowlist' if explicit else 'auto'
                    if explicit:target.update(capability_skill_mode='explicit',capability_skill_id='skill:deep-research:s1')
                    else:target.pop('capability_skill_mode',None);target.pop('capability_skill_id',None)
                    t=time.perf_counter();await module.graph.ainvoke({'messages':[HumanMessage(content='本地控制问题')]},cfg);return (time.perf_counter()-t)*1000
                for _ in range(5):await execute(False);await execute(True)
                for round_index in range(2):
                    auto=[];explicit=[]
                    for _ in range(35):
                        auto.append(await execute(False));explicit.append(await execute(True))
                    row={}
                    for label,values in [('auto',auto),('explicit_skill_and_MCP',explicit)]:
                        row[label]={'p50_ms':statistics.median(values),'p95_ms':sorted(values)[33],'samples_ms':values}
                    row['delta_p95_ms']=row['explicit_skill_and_MCP']['p95_ms']-row['auto']['p95_ms'];rounds.append(row)
                    assert row['delta_p95_ms']<=50,row
                out['benchmark']={'sample_size_each_mode_each_round':35,'rounds':rounds,'errors':0,'external_model_wait_s':0,'scope':'真实完整SDK图+PG身份守卫+作用域目录+显式技能加载+完整请求预算；模型输出受控'}
                out['passed']=True
            finally:GRAPH_SKILLS_DIRS['deep-research']=old
    finally:
        await fixture.stop()
        (Path(__file__).parent/'full-protection-benchmark.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
        print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
        await close_identity();await close_ledger();await engine_dispose_all()
asyncio.run(main())
