import asyncio
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
import httpx
from shopagent.settings import DATA, ROOT, PLUGINS, DEFAULT_VERSION, CONTRACT_VERSION
from shopagent.contracts.models import Manifest, InvokeRequest, ToolResult, BusinessError, failure
from shopagent.security import internal_headers

logger = logging.getLogger("shopagent.registry")


@dataclass
class Runtime:
    id: str
    version: str
    process: subprocess.Popen
    url: str
    log: object
    manifest: Manifest | None = None
    state: str = "starting"
    inflight: int = 0


class PluginManager:
    def __init__(self):
        from shopagent.core.environment import production, PRODUCTION_MODULES
        self.current = {}
        self.retired = []
        self.states = {name: "stopped" for name in PLUGINS}
        self.desired = {name: {"enabled": name != "demo", "version": DEFAULT_VERSION} for name in PLUGINS}
        if production():
            for name in self.desired:
                self.desired[name]['enabled']=name in PRODUCTION_MODULES
        self.mutations = asyncio.Lock()
        self.client = httpx.AsyncClient(trust_env=False, timeout=5)
        self.generation = 0
        self.monitor_task = None

    async def start(self):
        DATA.mkdir(parents=True, exist_ok=True)
        state = DATA / "plugins.json"
        if state.exists():
            stored = json.loads(state.read_text(encoding="utf-8"))
            for name in PLUGINS:
                if name in stored:
                    self.desired[name] = stored[name]
        # 初始化期间已经持久化的停用状态不被自动覆盖。
        from shopagent.core.environment import production, PRODUCTION_MODULES
        if production():
            for name in self.desired:
                if name not in PRODUCTION_MODULES:
                    self.desired[name]['enabled']=False
        for name in PLUGINS:
            if self.desired[name]["enabled"]:
                try:
                    await self.enable(name, persist=False)
                except BusinessError as error:
                    self.states[name] = "failed"
                    logger.error("plugin=%s startup=%s", name, error.code)
        self.monitor_task = asyncio.create_task(self.monitor())

    def save(self):
        temporary = DATA / "plugins.json.tmp"
        temporary.write_text(json.dumps(self.desired, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(DATA / "plugins.json")

    def require_name(self, name):
        if name not in PLUGINS:
            raise BusinessError("NOT_FOUND", "插件不在本地配置白名单中。")

    def snapshot(self):
        return {"generation": self.generation, "tools": {tool: {"id": rt.id, "version": rt.version} for rt in self.current.values() if rt.state == "ready" and rt.process.poll() is None for tool in rt.manifest.tools}}

    def tool_catalog(self, snapshot):
        """只发布该轮快照里健康插件的工具契约，不暴露进程地址。"""
        return {name: self.current[value["id"]].manifest.tools[name] for name, value in snapshot["tools"].items()
                if value["id"] in self.current and self.current[value["id"]].version == value["version"]}

    def overview(self):
        return [{"id": name, "label": definition["label"], "dependencies": definition["dependencies"],
                 "state": self.current[name].state if name in self.current else self.states[name],
                 "version": self.current[name].version if name in self.current else self.desired[name]["version"],
                 "inflight": sum(rt.inflight for rt in [*self.current.values(), *self.retired] if rt.id == name),
                 "pid": self.current[name].process.pid if name in self.current else None,
                 "tools": list(self.current[name].manifest.tools) if name in self.current else [],
                 "enabled": self.desired[name]["enabled"]} for name, definition in PLUGINS.items()]

    async def launch(self, name, version):
        if version not in ("1.0.0", "1.1.0"):
            raise BusinessError("VERSION_UNSUPPORTED", "仅支持已配置的 1.0.0 和 1.1.0 兼容版本。")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        logs = DATA / "logs"
        logs.mkdir(exist_ok=True)
        logfile = (logs / f"{name}-{version}-{secrets.token_hex(3)}.log").open("a", encoding="utf-8")
        worker_env = {k: v for k, v in os.environ.items() if k != "SHOPAGENT_MODEL_API_KEY"}
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "shopagent.plugins.worker", "--plugin", name, "--port", str(port), "--version", version], cwd=ROOT, env=worker_env, stdout=logfile, stderr=logfile,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        rt = Runtime(name, version, process, f"http://127.0.0.1:{port}", logfile)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise BusinessError("STARTUP_FAILED", "插件启动失败，请查看 data/logs。")
                try:
                    health = await self.client.get(rt.url + "/health", timeout=0.5)
                    if health.status_code == 200 and health.json().get("status") == "ready":
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise BusinessError("STARTUP_FAILED", "插件健康检查超时，旧版本继续使用。")
            response = await self.client.get(rt.url + "/manifest")
            response.raise_for_status()
            rt.manifest = Manifest.model_validate(response.json())
            if rt.manifest.id != name or rt.manifest.version != version or rt.manifest.contract_version != CONTRACT_VERSION or rt.manifest.dependencies != PLUGINS[name]["dependencies"]:
                raise BusinessError("CONTRACT_INCOMPATIBLE", "插件契约不兼容，拒绝接入。")
            if not rt.manifest.tools or any(not tool.startswith(name + ".") or "input" not in spec or "output" not in spec for tool, spec in rt.manifest.tools.items()):
                raise BusinessError("CONTRACT_INCOMPATIBLE", "插件工具命名或输入输出契约不合法。")
            rt.state = "ready"
            return rt
        except BaseException:
            await self.stop_process(rt)
            raise

    async def enable(self, name, persist=True):
        from shopagent.core.environment import production, PRODUCTION_MODULES
        if production() and name not in PRODUCTION_MODULES:
            raise BusinessError('PRODUCTION_SCOPE','正式首发仅开放商品、政策与导购模块，订单、物流、售后和演示模块未接真实业务。')
        self.require_name(name)
        async with self.mutations:
            current = self.current.get(name)
            if current and current.state == "ready" and current.process.poll() is None:
                return self.overview()
            for dep in PLUGINS[name]["dependencies"]:
                if dep not in self.current or self.current[dep].state != "ready" or self.current[dep].process.poll() is not None:
                    raise BusinessError("DEPENDENCY_UNAVAILABLE", f"请先启用依赖模块：{dep}。")
            if current:
                await self.stop_process(current)
                self.current.pop(name, None)
            self.states[name] = "starting"
            try:
                rt = await self.launch(name, self.desired[name]["version"])
            except Exception:
                self.states[name] = "failed"
                raise
            self.current[name] = rt
            self.states[name] = "ready"
            self.generation += 1
            self.desired[name]["enabled"] = True
            if persist:
                self.save()
            return self.overview()

    async def stop_process(self, rt):
        if rt.process.poll() is None:
            rt.process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(rt.process.wait), 3)
            except asyncio.TimeoutError:
                rt.process.kill()
                await asyncio.to_thread(rt.process.wait)
        if not rt.log.closed:
            rt.log.close()
        rt.state = "stopped"

    async def drain(self, rt, timeout=7):
        rt.state = "draining"
        self.retired.append(rt)
        deadline = time.monotonic() + timeout
        while rt.inflight and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await self.stop_process(rt)
        self.retired.remove(rt)

    async def disable(self, name, cascade=False):
        self.require_name(name)
        async with self.mutations:
            dependents = [pid for pid in self.current if name in PLUGINS[pid]["dependencies"]]
            if dependents and not cascade:
                raise BusinessError("DEPENDENTS_ACTIVE", f"请先停用依赖方 {', '.join(dependents)}，或选择级联停用。")
            order = dependents + [name]
            # 先排空依赖方，确保其在途调用仍能使用下层模块。
            for pid in order:
                rt = self.current.pop(pid, None)
                self.states[pid] = "draining"
                self.generation += 1
                if rt:
                    await self.drain(rt)
                self.states[pid] = "stopped"
                self.desired[pid]["enabled"] = False
            self.save()
            return self.overview()

    async def switch(self, name, version):
        self.require_name(name)
        async with self.mutations:
            old = self.current.get(name)
            if not old or old.state != "ready":
                raise BusinessError("PLUGIN_UNAVAILABLE", "请先启用插件，再切换版本。")
            new = await self.launch(name, version)
            if new.manifest.tools != old.manifest.tools:
                await self.stop_process(new)
                raise BusinessError("CONTRACT_INCOMPATIBLE", "新版工具结构发生不兼容变化，旧版本继续服务。")
            self.current[name] = new
            self.desired[name]["version"] = version
            self.generation += 1
            self.save()
            await self.drain(old)
            return self.overview()

    async def invoke(self, req: InvokeRequest, snapshot=None):
        from shopagent.core.environment import production, PRODUCTION_MODULES
        if production() and req.tool.split('.',1)[0] not in PRODUCTION_MODULES:
            return failure('PRODUCTION_SCOPE','正式首发未开放订单、物流或售后操作，请登记人工咨询。',req.request_id)
        from shopagent.core.customer_access import is_guest, PUBLIC_TOOLS
        if is_guest(req.identity) and req.tool not in PUBLIC_TOOLS:
            return failure('CONSULTATION_ONLY', '此入口只提供商品、库存、优惠和政策咨询。真实订单或售后处理请转人工，不提供示例订单。', req.request_id)
        name = req.tool.split(".", 1)[0]
        rt = self.current.get(name)
        if not rt or rt.state != "ready" or rt.process.poll() is not None or req.tool not in rt.manifest.tools:
            return failure("PLUGIN_UNAVAILABLE", f"{PLUGINS.get(name, {}).get('label', name)}暂不可用，请稍后重试或转人工。", req.request_id)
        if snapshot is not None:
            pinned = snapshot["tools"].get(req.tool)
            if not pinned or not rt or pinned["version"] != rt.version:
                return failure("PLUGIN_UNAVAILABLE", "可用模块已经变化，请重新发送请求。", req.request_id)
        for dep in PLUGINS[name]["dependencies"]:
            dependency = self.current.get(dep)
            if not dependency or dependency.state != "ready" or dependency.process.poll() is not None:
                return failure("DEPENDENCY_UNAVAILABLE", f"所需模块 {dep} 暂不可用。", req.request_id)
        if req.deadline < time.time():
            return failure("TIMEOUT", "请求已超时。", req.request_id)
        # 无 await 的检查与计数区间确保停用操作不会漏掉已接纳请求。
        rt.inflight += 1
        started = time.monotonic()
        try:
            body = req.model_dump()
            response = await self.client.post(rt.url + "/invoke", json=body, headers=internal_headers(body), timeout=min(5, max(0.1, req.deadline - time.time())))
            response.raise_for_status()
            return ToolResult.model_validate(response.json())
        except httpx.TimeoutException:
            return failure("TIMEOUT", "模块响应超时；提交类操作请核实状态后再重试。", req.request_id)
        except (httpx.HTTPError, ValueError):
            return failure("PLUGIN_UNAVAILABLE", "插件连接失败，其他模块仍可继续使用。", req.request_id)
        finally:
            rt.inflight -= 1
            logger.info("request_id=%s tool=%s version=%s elapsed_ms=%.1f", req.request_id, req.tool, rt.version, (time.monotonic()-started)*1000)

    async def monitor(self):
        try:
            while True:
                await asyncio.sleep(1)
                for name, rt in list(self.current.items()):
                    if rt.process.poll() is not None:
                        rt.state = "failed"
                        self.states[name] = "failed"
                        self.generation += 1
        except asyncio.CancelledError:
            pass

    async def close(self):
        if self.monitor_task:
            self.monitor_task.cancel()
            await self.monitor_task
        for rt in [*self.current.values(), *self.retired]:
            await self.stop_process(rt)
        self.current.clear()
        await self.client.aclose()
