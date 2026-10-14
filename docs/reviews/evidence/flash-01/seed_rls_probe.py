# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,sys,asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True)
os.environ['DATABASE_URI']=os.environ['TENANT_TEST_OWNER_URI'].rsplit('/',1)[0]+'/air_agent_review01_rls'
os.environ['ADMIN_RUNTIME_DATABASE_URI']=os.environ['TENANT_TEST_RUNTIME_URI'].rsplit('/',1)[0]+'/air_agent_review01_rls'
sys.path.insert(0,str(Path('admin/api').resolve()))
from app.db import OwnerSessionLocal
from scripts.seed import seed_permissions
async def main():
    async with OwnerSessionLocal() as s:
        await seed_permissions(s);await s.commit()
asyncio.run(main())
print('RLS 验收库前置权限字典已补齐')
