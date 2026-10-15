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
        for module_name in ['deep_research','intelligent_report','intelligent_tracing']:
            # 编译时目录为空；工具在模型请求阶段才发现。
            factory=ScopedMCPToolFactory({'controlled-one':url});set_factory(factory)
            model=ControlledModel(responses=[AIMessage(content='init')]);ModelRegistry.main_llm=model
            module=importlib.reload(importlib.import_module(module_name+'.graph'))
            cfg={'configurable':{'langgraph_auth_user':actor,'capability_mcp_mode':'allowlist','capability_tool_ids':['mcp:controlled-one:query']},'recursion_limit':30}
            model.responses=[tool_message('query',{'user_id':'forged','region':'杭州市'},'allowed-main'),tool_message('query_air',{'user_id':'forged','region':'北京市'},'denied-main'),AIMessage(content='主任务受控完成')];model.i=0
            before=await fixture.call_count()
            result=await module.graph.ainvoke({'messages':[HumanMessage(content='受控主任务调用')]},cfg)
            after=await fixture.call_count()
            main_tools=[{'name':m.name,'status':m.status,'content':str(m.content)[:200]} for m in result['messages'] if isinstance(m,ToolMessage)]
            model.responses=[tool_message('task',{'subagent_type':'general-purpose','description':'受控子任务依次使用query和query_air'},'child'),tool_message('query',{'user_id':'forged','region':'杭州市'},'allowed-child'),tool_message('query_air',{'user_id':'forged','region':'北京市'},'denied-child'),AIMessage(content='子任务受控完成'),AIMessage(content='主任务受控结束')];model.i=0
            child_before=await fixture.call_count()
            child_result=await module.graph.ainvoke({'messages':[HumanMessage(content='受控子任务调用')]},cfg)
            child_after=await fixture.call_count()
            out['graphs'][module_name]={'main_network_delta':after-before,'main_tools':main_tools,'child_network_delta':child_after-child_before,'child_messages':[{'type':m.type,'name':getattr(m,'name',None),'content':str(m.content)[:250]} for m in child_result['messages']], 'all_disclosed_schemas':model.disclosed}
            assert after-before==1, out['graphs'][module_name]
            assert child_after-child_before==1,out['graphs'][module_name]
            assert all('query_air' not in names for names in model.disclosed)
            assert any(m['status']=='error' and m['name']=='query_air' for m in main_tools)
            # 三张生产图真正的 read_file 与 task 继承；临时材料不进入产品目录。
            with tempfile.TemporaryDirectory(prefix='aa05f-sdk-skills-') as td:
                for slug in ['s1','s2']:
                    d=Path(td)/slug;d.mkdir();(d/'SKILL.md').write_text('---\nname: '+slug+'\ndescription: 受控技能\n---\nCONTROLLED_RULE_'+slug)
                graph_key=module_name.replace('_','-');old_root=GRAPH_SKILLS_DIRS[graph_key];GRAPH_SKILLS_DIRS[graph_key]=Path(td)
                backend=RecordingBackend(root_dir=td,virtual_mode=True)
                module._backend.routes={'/skills/':backend};module._backend.sorted_routes=[('/skills/',backend)]
                cfg['configurable'].update(capability_skill_mode='explicit',capability_skill_id='skill:'+module_name.replace('_','-')+':s1')
                cases={}
                for child in [False,True]:
                    for slug in ['s1','s2']:
                        backend.reads=[]
                        responses=[]
                        if child:responses.append(tool_message('task',{'subagent_type':'general-purpose','description':'受控文件读取'},'file-child'))
                        responses.extend([tool_message('read_file',{'file_path':'/skills/'+slug+'/SKILL.md'},'read-'+slug),AIMessage(content='材料验证完成')])
                        if child:responses.append(AIMessage(content='主任务完成'))
                        model.responses=responses;model.i=0
                        try:
                            result=await module.graph.ainvoke({'messages':[HumanMessage(content='受控材料读取')]},cfg)
                            text=repr(result['messages']);cases[f'{child}-{slug}']={'backend_reads':backend.reads,'completed':True,'s1_returned':'CONTROLLED_RULE_s1' in text,'s2_leaked':'CONTROLLED_RULE_s2' in text}
                            assert slug!='s2' or 'CONTROLLED_RULE_s2' not in text
                            if slug=='s1':assert any(x['contains_s1'] for x in backend.reads),cases
                        except PermissionError:
                            cases[f'{child}-{slug}']={'blocked':True};assert slug=='s2'
                out['graphs'][module_name]['real_file_access_cases']=cases
                GRAPH_SKILLS_DIRS[graph_key]=old_root
        # 整张生产 SDK 图，真实 PG 守卫，原 Auto 与显式保护的本地开销。
        model.responses=[AIMessage(content='受控本地基准')];model.i=0
        cfg['configurable'].pop('capability_skill_id',None);cfg['configurable'].pop('capability_skill_mode',None)
        timings=[]
        for round_index in range(2):
            result={}
            for mode in ['auto','allowlist']:
                cfg['configurable']['capability_mcp_mode']=mode
                for _ in range(5):await module.graph.ainvoke({'messages':[HumanMessage(content='本地基准')]},cfg)
                samples=[]
                for _ in range(35):
                    start=time.perf_counter();await module.graph.ainvoke({'messages':[HumanMessage(content='本地基准')]},cfg);samples.append((time.perf_counter()-start)*1000)
                result[mode]={'p50_ms':statistics.median(samples),'p95_ms':sorted(samples)[33],'samples_ms':samples}
            result['delta_p95_ms']=result['allowlist']['p95_ms']-result['auto']['p95_ms'];timings.append(result)
            assert result['delta_p95_ms']<=50
        out['controlled_full_SDK_PG_guard_benchmark']={'rounds':timings,'external_model_wait_s':0,'sample_errors':0,'sample_count':140}
        out['passed']=True
    finally:
        await fixture.stop()
        (Path(__file__).parent/'sdk-dispatch.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
        print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
        await close_identity();await close_ledger();await engine_dispose_all()
asyncio.run(main())
