import os,subprocess,sys
from pathlib import Path
r=Path(os.environ.get('AA05_TEST_ROOT','/Users/acerola/Dev/Python/air_agent'));os.chdir(r)
os.environ.update({'DATABASE_URI':'postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05','ADMIN_OWNER_DATABASE_URI':'postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05','ADMIN_RUNTIME_DATABASE_URI':'postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05','RUNTIME_DATABASE_URI':'postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05','ADMIN_AUTH_SECRET':'review-sel05-local-ephemeral-secret','AUTH_SECRET':'review-sel05-local-ephemeral-secret','ADMIN_INTERNAL_TOKEN':'review-sel05-internal-test','INTERNAL_API_TOKEN':'review-sel05-internal-test','PYTHONPATH':str(r/'src')+':'+str(r/'tests')+':'+str(r/'admin/api')+':'+str(r/'admin/api/tests'),'TENANT_MCP_FIXTURE_MODE':'true','AGENT_CAPABILITY_SELECTION_ENABLED':'true','AGENT_INTERACTIONS_ENABLED':'true','AGENT_INTERACTIONS_ENABLED_AT':'2020-01-01T00:00:00Z'})
args=sys.argv[1:];log=r/'docs/reviews/evidence/takeover-05-final'/args[0]
log.parent.mkdir(parents=True,exist_ok=True)
with log.open('w') as f:
 p=subprocess.run(['/tmp/aa-takeover35-py313/bin/python','-m','pytest',*args[1:]],stdout=f,stderr=subprocess.STDOUT)
print('exit',p.returncode,'log',log.relative_to(r));print(log.read_text()[-8000:]);sys.exit(p.returncode)
