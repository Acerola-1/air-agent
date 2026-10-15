"""真实生产 DeepAgents 主/子任务 + PG 身份 + 独立 HTTP MCP；模型输出受控。"""
from pathlib import Path
exec(Path(__file__).with_name('probe-live.py').read_text().split('async def main():')[0])
import importlib, secrets, tempfile, statistics, time
from common.capabilities import BoundedSkillsBackend
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
        for module_name in ['basic_qa','intelligent_analysis','data_analysis']:
            factory=ScopedMCPToolFactory({'controlled-one':url});set_factory(factory)
            model=ControlledModel(responses=[tool_message('helper_get_latest_time',{'user_id':'forged','region':'杭州'},'allowed'),tool_message('query_air',{'user_id':'forged','region':'北京'},'denied'),AIMessage(content='受控调度完成')]);ModelRegistry.main_llm=model
            module=importlib.reload(importlib.import_module(module_name+'.graph'))
            graph_id=module_name.replace('_','-')
            skill_id='skill:'+graph_id+':'+('air-quality-basic-query' if module_name=='basic_qa' else 'broadcast-hour')
            cfg={'configurable':{'langgraph_auth_user':actor,'capability_mcp_mode':'allowlist','capability_tool_ids':['mcp:controlled-one:helper_get_latest_time'],'capability_skill_mode':'explicit','capability_skill_id':skill_id,'chat_mode':'fast','menu_name':'CityHourBroadcast'},'recursion_limit':30}
            before=await fixture.call_count()
            result=await module.graph.ainvoke({'messages':[HumanMessage(content='受控查询杭州今天空气质量')]},cfg)
            after=await fixture.call_count()
            tools=[{'name':m.name,'status':m.status,'content':str(m.content)[:250]} for m in result['messages'] if isinstance(m,ToolMessage)]
            out['graphs'][module_name]={'network_delta':after-before,'tools':tools,'rules_loaded':bool(result.get('skill_rules_content')),'disclosed':model.disclosed}
            assert after-before==1,out['graphs'][module_name]
            assert out['graphs'][module_name]['rules_loaded']
            assert any(t['name']=='helper_get_latest_time' and t['status']=='success' for t in tools),tools
            assert any(t['name']=='query_air' and t['status']=='error' for t in tools),tools
            assert all('query_air' not in names for names in model.disclosed)
        out['passed']=True
    finally:
        await fixture.stop()
        (Path(__file__).parent/'business-dispatch.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
        print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
        await close_identity();await close_ledger();await engine_dispose_all()
asyncio.run(main())
