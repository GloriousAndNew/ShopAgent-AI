import asyncio
from pydantic import Field
from shopagent.contracts.models import StrictModel


class Echo(StrictModel):
    text: str = Field(default="插件接入成功", max_length=500)
    delay: float = Field(default=0, ge=0, le=3)


def initialize():
    pass


async def echo(params, req):
    await asyncio.sleep(params.delay)
    return {"text": params.text}


TOOLS = {"echo": (Echo, echo, "独立扩展插件，支持短延迟用于验证请求排空")}
