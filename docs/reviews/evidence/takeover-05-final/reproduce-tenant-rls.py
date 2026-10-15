import os,subprocess,sys,json,uuid
from pathlib import Path
import psycopg
r=Path('/Users/acerola/Dev/Python/air_agent');env=json.loads(Path('/tmp/aa05f-stack-env.json').read_text());base='postgresql://air_agent:air_agent_test@127.0.0.1:15434/';name='aa05f_rls_'+uuid.uuid4().hex[:8]
with psycopg.connect(base+'postgres',autocommit=True) as c:c.execute('CREATE DATABASE '+name)
try:
 migrate={**env,'DATABASE_URI':base+name,'ADMIN_OWNER_DATABASE_URI':base+name,'PYTHONPATH':str(r/'admin/api')}
 p=subprocess.run(['/tmp/aa-takeover35-py313/bin/python','-m','alembic','upgrade','head'],cwd=r/'admin/api',env=migrate,capture_output=True,text=True);assert p.returncode==0,p.stderr[-800:]
 env.update(TENANT_TEST_OWNER_URI=base+name,TENANT_TEST_RUNTIME_URI='postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/'+name,AGENT_INTERACTIONS_ENABLED='true',AGENT_INTERACTIONS_ENABLED_AT='2020-01-01T00:00:00Z')
 with (r/'docs/reviews/evidence/takeover-05-final/tenant-rls-regression.txt').open('w') as f:
  p=subprocess.run(['/tmp/aa-takeover35-py313/bin/python',str(r/'docs/reviews/evidence/takeover-05-final/run-tests.py'),'tenant-rls-pytest.txt','admin/api/tests/test_tenant_schema.py','admin/api/tests/test_tenant_migration.py','-q'],cwd=r,env=env,stdout=f,stderr=subprocess.STDOUT)
 print('tenant RLS/migration exit',p.returncode)
finally:
 with psycopg.connect(base+'postgres',autocommit=True) as c:c.execute('DROP DATABASE '+name+' WITH(FORCE)')
