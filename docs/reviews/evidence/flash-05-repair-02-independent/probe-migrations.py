import os,subprocess,pathlib,json
import psycopg
r=pathlib.Path('/Users/acerola/Dev/Python/air_agent');out=r/'docs/reviews/evidence/flash-05-repair-02-independent'
base='postgresql://air_agent:air_agent_test@127.0.0.1:15434/'
name='aa05r2_review_empty'
with psycopg.connect(base+'postgres',autocommit=True) as c:
 c.execute('CREATE DATABASE '+name)
try:
 env=os.environ.copy();env.update(DATABASE_URI=base+name,ADMIN_OWNER_DATABASE_URI=base+name,PYTHONPATH=str(r/'admin/api'))
 for target,label in [('0012o_delivery','empty-to-old-head'),('head','old-head-to-new-head')]:
  p=subprocess.run(['/tmp/aa-takeover35-py313/bin/python','-m','alembic','upgrade',target],cwd=r/'admin/api',env=env,capture_output=True,text=True)
  (out/(label+'.txt')).write_text(p.stdout+p.stderr);print(label,p.returncode,(p.stdout+p.stderr)[-1200:])
  if p.returncode:break
 with psycopg.connect(base+name) as c:
  rows=c.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='agent_capability_selections'").fetchall();print('selection RLS',rows)
finally:
 with psycopg.connect(base+'postgres',autocommit=True) as c:c.execute('DROP DATABASE '+name+' WITH (FORCE)')
