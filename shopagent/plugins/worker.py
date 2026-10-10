import argparse
import importlib
import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from pydantic import ValidationError
from shopagent.settings import PLUGINS, CONTRACT_VERSION
from shopagent.contracts.models import InvokeRequest, Manifest, ToolResult, BusinessError, failure
from shopagent.security import verify_internal

logger = logging.getLogger("shopagent.worker")


def create_worker(plugin_id, version):
    definition = PLUGINS[plugin_id]
    module = definition["module"]
    if plugin_id == "product" and version == "1.1.0":
        module = "shopagent.plugins.product.service_v2"
    service = importlib.import_module(module)

    @asynccontextmanager
    async def lifespan(app):
        service.initialize()
        yield

    app = FastAPI(title=definition["label"], lifespan=lifespan)
    manifest = Manifest(id=plugin_id, label=definition["label"], version=version,
                        contract_version=CONTRACT_VERSION, dependencies=definition["dependencies"],
                        tools={f"{plugin_id}.{name}": {"input": spec[0].model_json_schema(), "description": spec[2], "output": ToolResult.model_json_schema()} for name, spec in service.TOOLS.items()})

    @app.get("/health")
    async def health():
        return {"status": "ready", "id": plugin_id, "version": version}

    @app.get("/manifest")
    async def describe():
        return manifest

    @app.post("/invoke")
    async def invoke(request: Request):
        raw = await verify_internal(request)
        req = InvokeRequest.model_validate(raw)
        result = None
        try:
            if req.deadline < time.time():
                raise BusinessError("TIMEOUT", "请求已超时，请重新查询。")
            if req.tool not in manifest.tools:
                raise BusinessError("NOT_FOUND", "插件不提供这个工具。")
            spec = service.TOOLS[req.tool.split(".", 1)[1]]
            params = spec[0].model_validate(req.params)
            data = await spec[1](params, req)
            result = ToolResult(data=data)
            if isinstance(data, dict) and "evidence" in data:
                result.evidence = data["evidence"]
        except BusinessError as error:
            result = failure(error.code, error.message)
        except ValidationError:
            result = failure("VALIDATION_ERROR", "工具参数不完整或类型错误，请重新填写。")
        except Exception:
            logger.exception("request_id=%s tool=%s", req.request_id, req.tool)
            result = failure("INTERNAL_ERROR", "模块处理失败，请查看运行日志或转人工。")
        result.request_id, result.plugin_version = req.request_id, version
        return result

    return app


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", choices=list(PLUGINS), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    uvicorn.run(create_worker(args.plugin, args.version), host="127.0.0.1", port=args.port, log_level="warning")
