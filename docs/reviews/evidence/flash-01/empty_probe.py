# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,json,subprocess,uuid
from urllib.parse import urlparse,urlunparse
from dotenv import load_dotenv
import psycopg
load_dotenv('.env',override=True)
raw=os.environ['TENANT_TEST_OWNER_URI'];assert urlparse(raw).path=='/air_agent_tenant_test'
def replace(db):return urlunparse(urlparse(raw)._replace(path='/'+db))
name='air_agent_review01_empty_'+uuid.uuid4().hex[:8]
with psycopg.connect(replace('postgres'),autocommit=True) as c:
    assert not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone(),'仅用于首次空库诊断'
    c.execute(psycopg.sql.SQL('CREATE DATABASE {} OWNER air_agent').format(psycopg.sql.Identifier(name)))
env=dict(os.environ,DATABASE_URI=replace(name))
r=subprocess.run(['.venv/bin/alembic','upgrade','head'],cwd='admin/api',env=env,capture_output=True,text=True)
print(json.dumps({'empty_upgrade_exit_code':r.returncode,'missing_space_sql_observed':'ONtool_artifact_storage' in r.stderr},indent=2))
