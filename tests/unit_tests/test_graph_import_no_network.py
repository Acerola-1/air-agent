"""冷进程公开图导入护栏：网络调用尝试即失败，避免以缓存或服务可用代替零 I/O 证明。"""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_public_graphs_import_without_any_network_attempt(tmp_path):
    root = Path(__file__).resolve().parents[2]
    script = r"""
import importlib,inspect,json,socket,time,traceback
attempts=[]
def deny(*args,**kwargs):
    attempts.append([f'{frame.filename}:{frame.name}' for frame in traceback.extract_stack(limit=40)])
    raise RuntimeError('graph import attempted network')
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.getaddrinfo=deny
modules=['basic_qa','data_analysis','intelligent_tracing']
loaded=[]
for name in modules:
    graph=importlib.import_module(name+'.graph').graph
    if inspect.isfunction(graph):
        graph=graph({})
    assert callable(getattr(graph,'ainvoke',None)),name
    loaded.append(name)
# 给任何误启动的后台线程机会执行；不是仅检查导入主线程。
time.sleep(.2)
assert not attempts,attempts
print('IMPORT_PROOF='+json.dumps({'graphs':loaded,'network_attempts':len(attempts),'langfuse_enabled':True}))
"""
    env = {
        **os.environ,
        "MCP_SERVER_URL": "http://127.0.0.1:1/controlled-import",
        "SILICONFLOW_API_KEY": "controlled-import-not-a-key",
        "SERPER_API_KEY": "controlled-import-not-a-key",
        "MAIN_LLM_API_KEY": "controlled-import-not-a-key",
        "LANGFUSE_ENABLED": "true",
        "TENANT_ENFORCE_RUNTIME_IDENTITY": "true",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-controlled-import",
        "LANGFUSE_SECRET_KEY": "sk-lf-controlled-import",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:1",
        "PYTHONPATH": str(root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr[-5000:]
    proof = next(
        line.removeprefix("IMPORT_PROOF=")
        for line in result.stdout.splitlines()
        if line.startswith("IMPORT_PROOF=")
    )
    parsed = json.loads(proof)
    assert len(parsed["graphs"]) == 3 and parsed["network_attempts"] == 0
    evidence_dir = tmp_path / "three-graph-import"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "import-network-guard.json").write_text(
        json.dumps(parsed, indent=2) + "\n"
    )
