# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,sys,asyncio,json
from pathlib import Path
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path('src').resolve()))
os.environ['LANGFUSE_ENABLED']='false'
from common.identity.runtime import RuntimeIdentity,revalidate
from common.identity.db import IdentityState
from common.identity.ticket import verify_ticket,parse_keys
from common.mcp_authorization import resolve_binding
async def main():
    ident=RuntimeIdentity('u','t','m','s')
    changed=IdentityState(True,True,3,True,'test',99,'active',99,'test','active',99)
    with patch('common.identity.runtime.fetch_identity_state',AsyncMock(return_value=changed)):
        try:await revalidate(ident);rev=False
        except Exception:rev=True
    row={'tenant_id':'t','membership_id':'m','server_key':'s','external_subject':'e','credential_ref':'env:REVIEW_SYNTHETIC_CREDENTIAL','protocol_version':'never-verified-protocol','auth_revision':1,'status':'active'}
    os.environ['REVIEW_SYNTHETIC_CREDENTIAL']='synthetic-test-value'
    with patch('common.mcp_authorization.fetch_mcp_binding',AsyncMock(return_value=row)):
        try:await resolve_binding(tenant_id='t',membership_id='m',server_key='s');denied=False
        except Exception:denied=True
    from langgraph.runtime import Runtime
    from common.middleware.tenant_runtime_guard_middleware import TenantRuntimeGuardMiddleware
    from types import SimpleNamespace
    cfg=TenantRuntimeGuardMiddleware._config_of(SimpleNamespace(runtime=Runtime(context={})))
    print(json.dumps({'revision_changed_runtime_denied':rev,'unknown_protocol_denied':denied,'model_runtime_config_extracted':cfg is not None},indent=2))
asyncio.run(main())
