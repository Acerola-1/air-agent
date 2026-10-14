import os,json,uuid,secrets,subprocess,importlib.util
from pathlib import Path
from urllib.parse import urlparse,urlunparse
from dotenv import load_dotenv
import psycopg
from psycopg import sql
load_dotenv('.env',override=True)
owner=os.environ['TENANT_TEST_OWNER_URI'];runtime=os.environ['TENANT_TEST_RUNTIME_URI'];u=urlparse(owner);assert u.hostname in {'localhost','127.0.0.1'} and u.path!='/air_agent'
suffix=uuid.uuid4().hex[:8];names=['air_agent_review02_ledger_'+suffix,'air_agent_review02_api_'+suffix,'air_agent_review02_rls_'+suffix];worker='review_ledger_worker_'+suffix;password=secrets.token_hex(20)
def db(uri,name): return urlunparse(urlparse(uri)._replace(path='/'+name))
with psycopg.connect(db(owner,'postgres'),autocommit=True) as c:
 c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(worker),sql.Literal(password)))
 for name in names:c.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(u.username)))
for name in names:
 env=dict(os.environ,DATABASE_URI=db(owner,name));r=subprocess.run(['.venv/bin/python','-m','alembic','upgrade','head'],cwd='admin/api',env=env,capture_output=True)
 if r.returncode:raise RuntimeError('fresh migration failed '+r.stderr.decode()[-1500:])
s=importlib.util.spec_from_file_location('rev','admin/api/migrations/versions/0007_agent_event_ledger.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.WORKER_ROLE=worker
with psycopg.connect(db(owner,names[0])) as c:
 for stmt in m.ledger_grant_statements(lambda _:True):c.execute(stmt)
worker_uri=urlunparse(u._replace(netloc=worker+':'+password+'@'+u.hostname+':'+str(u.port),path='/'+names[0]))
x={'names':names,'worker_role':worker,'env':{'LEDGER_TEST_OWNER_URI':db(owner,names[0]),'LEDGER_TEST_TENANT_ID':str(uuid.uuid4()),'LEDGER_TEST_USER_ID':str(uuid.uuid4()),'RUNTIME_DATABASE_URI':db(runtime,names[0]),'LEDGER_WORKER_DATABASE_URI':worker_uri,'DATABASE_URI':db(owner,names[1]),'ADMIN_RUNTIME_DATABASE_URI':db(runtime,names[1]),'TENANT_TEST_OWNER_URI':db(owner,names[2]),'TENANT_TEST_RUNTIME_URI':db(runtime,names[2])}}
p=Path('/tmp/air-agent-f02-env.json');p.write_text(json.dumps(x));p.chmod(0o600)
Path('docs/reviews/evidence/flash-02-independent/fresh-migrations.json').write_text(json.dumps({'databases':names,'head':'0007','worker':'random_nonowner_test_role','migration':'0001_to_0008'},indent=2)+'\n');print('3套新空PG迁移到0007；专用非owner worker已配置，未使用业务库')
