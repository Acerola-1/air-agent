"""Record the actual public graph factories' topology without network I/O."""

import json
import os
import socket
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
snapshot = (
    Path(sys.argv[2]).resolve()
    if len(sys.argv) > 2
    else root / ".local/graph-module-ownership/20261029-apply/preimplementation/src"
)
sys.path.insert(0, str(snapshot))
for key in (
    "MAIN_LLM_API_KEY",
    "SILICONFLOW_API_KEY",
    "SERPER_API_KEY",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
):
    os.environ[key] = "synthetic"
requests = []
original = socket.socket.connect


def _blocked(self, address):
    requests.append(str(address))
    raise RuntimeError("network blocked during graph import")


socket.socket.connect = _blocked
try:
    from basic_qa.graph import graph as basic
    from data_analysis.graph import graph as analysis

    result = {}
    for name, factory in [("basic-qa", basic), ("data-analysis", analysis)]:
        compiled = factory({})
        graph = compiled.get_graph()
        result[name] = {
            "nodes": sorted(graph.nodes),
            "edges": sorted(
                [
                    {
                        "source": e.source,
                        "target": e.target,
                        "conditional": e.conditional,
                    }
                    for e in graph.edges
                ],
                key=lambda e: (e["source"], e["target"]),
            ),
            "checkpointer": str(compiled.checkpointer),
        }
    result["import_socket_connect_attempts"] = requests
    Path(sys.argv[1]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    assert not requests
finally:
    socket.socket.connect = original
