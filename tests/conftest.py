import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest


class Server:
    def __init__(self, directory):
        self.directory = directory
        self.agent_config = directory / "agent.toml"
        self.agent_config.write_text('[agent]\nmode = "mock"\n', encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None
        self.logs = []

    def start(self):
        env = {**os.environ, "SHOPAGENT_DATA_DIR": str(self.directory), "SHOPAGENT_AGENT_CONFIG": str(self.agent_config), "SHOPAGENT_MODEL_API_KEY": "test-only-key"}
        env.pop("SHOPAGENT_INTERNAL_KEY", None)
        log = (self.directory / f"host-{len(self.logs)}.log").open("a", encoding="utf-8")
        self.logs.append(log)
        self.process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "shopagent.run", "--port", str(self.port)], cwd=Path(__file__).resolve().parents[1], env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        for _ in range(150):
            if self.process.poll() is not None:
                raise RuntimeError("测试服务启动失败，检查临时 host 日志")
            try:
                if httpx.get(self.url+"/health", timeout=.5, trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        raise RuntimeError("服务启动超时")

    def stop(self):
        if self.process and self.process.poll() is None:
            import json
            record=json.loads((self.directory / "host.json").read_text(encoding="utf-8"))
            httpx.post(self.url+"/internal/shutdown",headers={"X-Stop-Key":record["stop_key"]},trust_env=False,timeout=5).raise_for_status()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    instance = Server(tmp_path_factory.mktemp("shopagent"))
    instance.start()
    yield instance
    instance.stop()
    for log in instance.logs:
        log.close()


@pytest.fixture(scope="session")
def clients(server):
    values = {}
    for name in ("alice", "bob", "admin"):
        client = httpx.Client(base_url=server.url, timeout=20, trust_env=False)
        response = client.post("/auth/login", json={"username": name, "password": "demo123"})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["token"]
        values[name] = client
    yield values
    for client in values.values():
        client.close()


def invoke(client, tool, params):
    response = client.post("/tools/invoke", json={"tool": tool, "params": params})
    response.raise_for_status()
    return response.json()


def prepare(client):
    result = invoke(client, "aftersale.prepare_return", {"order_id": "O1002", "reason": "耳机质量问题", "opened": True, "quality_issue": True})
    assert result["status"] == "ok", result
    return result["data"]
