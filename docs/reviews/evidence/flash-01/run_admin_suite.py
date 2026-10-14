# 本轮独立评审诊断输入；只在明确的本地测试环境使用，非产品实现。
import os,subprocess
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv(".env",override=True)
owner=os.getenv("TENANT_TEST_OWNER_URI", "")
runtime=os.getenv("TENANT_TEST_RUNTIME_URI", "")
assert urlparse(owner).path=="/air_agent_tenant_test"
assert urlparse(runtime).path=="/air_agent_tenant_test"
os.environ["DATABASE_URI"]=owner.rsplit("/",1)[0]+"/air_agent_review01_api"
os.environ["ADMIN_RUNTIME_DATABASE_URI"]=runtime.rsplit("/",1)[0]+"/air_agent_review01_api"
os.environ["LANGFUSE_ENABLED"]="false"
os.environ["LANGSMITH_TRACING"]="false"
os.environ["LANGCHAIN_TRACING_V2"]="false"
os.environ["TENANT_TEST_OWNER_URI"]=owner.rsplit("/",1)[0]+"/air_agent_review01_rls"
os.environ["TENANT_TEST_RUNTIME_URI"]=runtime.rsplit("/",1)[0]+"/air_agent_review01_rls"
print("独立 API/RLS 专用验收库已确认",flush=True)
raise SystemExit(subprocess.call([".venv/bin/python","-m","pytest","tests","-q"],cwd="admin/api"))
