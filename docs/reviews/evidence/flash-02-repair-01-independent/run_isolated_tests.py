import os,sys,json,subprocess
from pathlib import Path
from dotenv import load_dotenv,dotenv_values
load_dotenv('.env',override=True);live=os.environ['DATABASE_URI'];os.environ.update(json.load(open('/tmp/air-agent-ivrp01-env.json'))['env']);os.environ.update(LANGFUSE_ENABLED='false',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
mode=sys.argv[1]
if mode=='admin':cmd=['.venv/bin/python','-m','pytest','tests','-q'];cwd='admin/api'
elif mode=='ledger':cmd=['.venv/bin/python','-m','pytest','tests/unit_tests/test_event_ledger_contract.py','tests/unit_tests/test_event_ledger_lifecycle.py','tests/unit_tests/test_event_ledger_store.py','tests/unit_tests/test_event_ledger_review02_regressions.py','-q'];cwd='.'
else:
 for f in ['.env.langgraph','frontend/.env.local']:
  key=dotenv_values(f).get('ARCHIVE_SCOPE_KEYS')
  if key:os.environ.setdefault('ARCHIVE_SCOPE_KEYS',key)
 os.environ.update(RUN_TENANT_LIVE_E2E='1',TENANT_E2E_OWNER_URI=live,REVIEW_FIXTURE_PASSWORD=Path('/tmp/aa_review_pw').read_text().strip())
 cmd=['.venv/bin/python','-m','pytest','tests/integration_tests','-q'];cwd='.'
raise SystemExit(subprocess.call(cmd,cwd=cwd,env=os.environ))
