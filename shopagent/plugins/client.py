"""插件通过宿主公开的内部接口查询其他模块，不读其他模块数据库。"""
import httpx
from shopagent.settings import HOST_URL
from shopagent.security import internal_headers
from shopagent.contracts.models import BusinessError, ToolResult


async def call(request, tool, params):
    body = request.model_dump()
    body.update(tool=tool, params=params)
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        response = await client.post(HOST_URL + "/internal/invoke", json=body, headers=internal_headers(body))
        response.raise_for_status()
        result = ToolResult.model_validate(response.json())
    if result.status == "error":
        raise BusinessError(result.error_code, result.message)
    return result.data
