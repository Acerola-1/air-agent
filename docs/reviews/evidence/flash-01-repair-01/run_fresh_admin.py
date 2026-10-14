from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,subprocess,json,uuid
from urllib.parse import urlparse,urlunparse
from pathlib import Path
from dotenv import load_dotenv
import psycopg
load_dotenv('.env',override=True)
owner=os.environ['TENANT_TEST_OWNER_URI']; runtime=os.environ['TENANT_TEST_RUNTIME_URI']
assert urlparse(owner).path=='/air_agent_tenant_test'; assert urlparse(runtime).path=='/air_agent_tenant_test'
def uri(raw,db): return urlunparse(urlparse(raw)._replace(path='/'+db))
suffix=uuid.uuid4().hex[:8]; names=['air_agent_review02_api_'+suffix,'air_agent_review02_rls_'+suffix]
result={'databases':names,'migrations':[]}
for name in names:
 with psycopg.connect(uri(owner,'postgres'),autocommit=True) as c:
  assert not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone()
  c.execute(psycopg.sql.SQL('CREATE DATABASE {} OWNER air_agent').format(psycopg.sql.Identifier(name)))
 env=dict(os.environ,DATABASE_URI=uri(owner,name))
 r=subprocess.run(['.venv/bin/alembic','upgrade','head'],cwd='admin/api',env=env,capture_output=True,text=True)
 result['migrations'].append({'db':name,'exit':r.returncode,'revisions':r.stderr.splitlines()[-5:] if r.returncode==0 else ['failed: '+type(r).__name__]})
 if r.returncode: print(json.dumps(result)); raise SystemExit(r.returncode)
print(json.dumps(result,ensure_ascii=False),flush=True)
Path(str(EVIDENCE_ROOT / 'fresh-migrations.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
env=dict(os.environ,DATABASE_URI=uri(owner,names[0]),ADMIN_RUNTIME_DATABASE_URI=uri(runtime,names[0]),TENANT_TEST_OWNER_URI=uri(owner,names[1]),TENANT_TEST_RUNTIME_URI=uri(runtime,names[1]),LANGFUSE_ENABLED='false',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
with open(str(EVIDENCE_ROOT / 'admin-tests.txt'),'w') as f:
 r=subprocess.run(['.venv/bin/python','-m','pytest','tests','-q'],cwd='admin/api',env=env,stdout=f,stderr=subprocess.STDOUT,text=True)
print(Path(str(EVIDENCE_ROOT / 'admin-tests.txt')).read_text()[-7000:]); raise SystemExit(r.returncode)
