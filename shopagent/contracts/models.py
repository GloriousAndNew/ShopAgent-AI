import time
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(StrictModel):
    user_id: str = Field(min_length=1, max_length=64)
    role: Literal["buyer", "admin"] = "buyer"


class InvokeRequest(StrictModel):
    tool: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)
    identity: Identity
    request_id: str = Field(min_length=1, max_length=100)
    deadline: float = Field(default_factory=lambda: time.time() + 30)


class ToolResult(StrictModel):
    status: Literal["ok", "error"] = "ok"
    data: Any = None
    evidence: list[dict] = Field(default_factory=list)
    error_code: str | None = None
    message: str = ""
    retryable: bool = False
    request_id: str = ""
    plugin_version: str = ""


class Manifest(StrictModel):
    id: str
    label: str
    version: str
    contract_version: str
    dependencies: list[str]
    tools: dict[str, dict]


class BusinessError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def failure(code: str, message: str, request_id: str = "") -> ToolResult:
    return ToolResult(status="error", error_code=code, message=message, request_id=request_id)
