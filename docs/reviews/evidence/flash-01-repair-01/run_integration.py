from pathlib import Path
import os, shutil
EVIDENCE_ROOT = Path(os.environ.get("REVIEW_REPAIR_OUTPUT_DIR", "/tmp/air-agent-review02-replay")).resolve()
ORIGINAL_ROOT = Path("docs/reviews/evidence/flash-01-repair-01").resolve()
assert EVIDENCE_ROOT != ORIGINAL_ROOT and EVIDENCE_ROOT != ORIGINAL_ROOT.parent / "flash-01", "复跑不得覆盖评审原始观察"
EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
if not (EVIDENCE_ROOT / "fresh-migrations.json").exists():
    shutil.copyfile(ORIGINAL_ROOT / "fresh-migrations.json", EVIDENCE_ROOT / "fresh-migrations.json")
# 独立复验输入；仅本地合成资源或专用验收库。凭据从受保护环境读取。
import os,json,subprocess
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env',override=True)
db=json.load(open(str(EVIDENCE_ROOT / 'fresh-migrations.json')))['databases'][1]
os.environ['TENANT_TEST_OWNER_URI']=os.environ['TENANT_TEST_OWNER_URI'].rsplit('/',1)[0]+'/'+db
os.environ['TENANT_TEST_RUNTIME_URI']=os.environ['TENANT_TEST_RUNTIME_URI'].rsplit('/',1)[0]+'/'+db
os.environ.update(LANGFUSE_ENABLED='false',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',RUN_TENANT_LIVE_E2E='1',REVIEW_FIXTURE_PASSWORD=os.environ['REVIEW_FIXTURE_PASSWORD'])
with open(str(EVIDENCE_ROOT / 'root-integration.txt'),'w') as f:
 r=subprocess.run(['.venv/bin/python','-m','pytest','tests/integration_tests','-q'],env=os.environ,stdout=f,stderr=subprocess.STDOUT)
print(Path(str(EVIDENCE_ROOT / 'root-integration.txt')).read_text()[-5000:]); raise SystemExit(r.returncode)
