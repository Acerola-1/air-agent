import json,os,sys,subprocess,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from dotenv import dotenv_values
import psycopg
from psycopg import sql
wt=Path('/Users/acerola/.codex/worktrees/feedback-03/air_agent')
v=dict(dotenv_values(wt.parent/'secrets/feedback03.env'))
def uri_db(uri,db):
 u=urlsplit(uri.replace('postgresql+psycopg://','postgresql://'));return urlunsplit((u.scheme,u.netloc,'/'+db,u.query,u.fragment))
suffix=uuid.uuid4().hex[:8];dbname='aa_feedback03_review_'+suffix
owner=uri_db(v['DATABASE_URI'],dbname)
runtime=uri_db(v['ADMIN_RUNTIME_DATABASE_URI'],dbname)
with psycopg.connect(uri_db(v['DATABASE_URI'],'postgres'),autocommit=True) as c:
 c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
v.update({'DATABASE_URI':owner,'ADMIN_RUNTIME_DATABASE_URI':runtime,'RUNTIME_DATABASE_URI':runtime,'PYTHONDONTWRITEBYTECODE':'1'})
env=os.environ.copy();env.update({k:val for k,val in v.items() if val is not None});env['PYTHONPATH']=str(wt/'admin/api')+':'+str(wt/'admin/api/tests')
priv=Path('/tmp/aa-review03-private.json');priv.write_text(json.dumps({'env':v,'database':dbname}));priv.chmod(0o600)
r=subprocess.run([str(wt/'admin/api/.venv/bin/python'),'-m','alembic','upgrade','head'],cwd=wt/'admin/api',env=env,capture_output=True,text=True)
Path('/Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-03-independent/migration.txt').write_text(r.stdout+r.stderr)
if r.returncode:print('migration failed; see migration.txt');raise SystemExit(r.returncode)
with psycopg.connect(owner) as c:
 head=c.execute('SELECT version_num FROM alembic_version').fetchone()[0]
 policies=c.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname LIKE 'agent_feedback_%' AND relkind='r' ORDER BY relname").fetchall()
result={'database':dbname,'head':head,'feedback_rls_catalog':[{'table':a,'enabled':b,'forced':c} for a,b,c in policies]}
Path('/Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-03-independent/environment.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));
