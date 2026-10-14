# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,sys,subprocess,asyncio
from pathlib import Path
from urllib.parse import urlparse,urlunparse
from dotenv import load_dotenv
import psycopg
load_dotenv('.env',override=True)
base=os.environ['TENANT_TEST_OWNER_URI']
runtime=os.environ['TENANT_TEST_RUNTIME_URI']
assert urlparse(base).path=='/air_agent_tenant_test'
def uri(raw,db):
    p=urlparse(raw);return urlunparse(p._replace(path='/'+db))
names=['air_agent_review01_api','air_agent_review01_rls']
with psycopg.connect(uri(base,'postgres'),autocommit=True) as c:
    for name in names:
        if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
            c.execute(psycopg.sql.SQL('CREATE DATABASE {} OWNER air_agent').format(psycopg.sql.Identifier(name)))
for name in names:
    with psycopg.connect(uri(base,name),autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS tool_artifact_storage (artifact_id TEXT PRIMARY KEY, tool_call_id TEXT NOT NULL,tool_name TEXT NOT NULL,char_length INTEGER NOT NULL,raw_payload TEXT NOT NULL,summary TEXT,created_at TEXT NOT NULL,user_id TEXT,thread_id TEXT,run_id TEXT,parent_artifact_id TEXT,kind TEXT DEFAULT 'raw',status TEXT DEFAULT 'stored',expires_at TEXT,processing_metadata TEXT DEFAULT '{}')")
    env=dict(os.environ,DATABASE_URI=uri(base,name),ADMIN_RUNTIME_DATABASE_URI=uri(runtime,name))
    subprocess.run(['.venv/bin/alembic','upgrade','head'],cwd='admin/api',env=env,check=True)
os.environ['DATABASE_URI']=uri(base,names[0]);os.environ['ADMIN_RUNTIME_DATABASE_URI']=uri(runtime,names[0])
sys.path.insert(0,str(Path('admin/api').resolve()))
from app.db import OwnerSessionLocal
from scripts.seed import seed_permissions,seed_platform_roles
from scripts.import_regions import load_dataset
from sqlalchemy import text
async def init():
    async with OwnerSessionLocal() as s:
        permissions=await seed_permissions(s);await seed_platform_roles(s,permissions)
        rid=await s.scalar(text("INSERT INTO roles(code,name,scope,is_system) VALUES ('viewer','测试只读角色','platform',true) ON CONFLICT (code) WHERE scope='platform' DO UPDATE SET name=EXCLUDED.name RETURNING id"))
        for code,p in permissions.items():
            if code.endswith(':read') or code in {'system','business','system:user','system:role','system:permission','system:audit','business:region','business:followed'}:
                await s.execute(text('INSERT INTO role_permissions(role_id,permission_id) VALUES (:r,:p) ON CONFLICT DO NOTHING'),{'r':rid,'p':p.id})
        for code,name,level,parent,sort in load_dataset()[0]:
            await s.execute(text('INSERT INTO regions(code,name,level,parent_code,sort) VALUES (:c,:n,:l,:p,:s) ON CONFLICT (code) DO NOTHING'),dict(c=code,n=name,l=level,p=parent,s=sort))
        await s.commit()
asyncio.run(init())
print('独立 API/RLS 验收库与测试目录数据已就绪')
