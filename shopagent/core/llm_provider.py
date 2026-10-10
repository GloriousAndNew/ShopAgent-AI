"""可替换模型适配器：统一消息/工具接口，支持两种 HTTP 协议。"""
import json
from dataclasses import dataclass, field
import httpx
from shopagent.contracts.models import BusinessError


@dataclass
class ModelTurn:
    text: str = ""
    calls: list[dict] = field(default_factory=list)
    continuation: dict = field(default_factory=dict)


class HTTPModelProvider:
    mode = "llm"

    def __init__(self, config, api_key, transport=None):
        self.config, self.api_key, self.transport = config, api_key, transport

    async def complete(self, messages, tools):
        config = self.config
        if config.protocol == "chat_completions":
            path = "/chat/completions"
            payload = {"model": config.model, "messages": messages, "max_tokens": config.max_output_tokens, "stream": False}
            if config.thinking_mode != "default":
                payload["thinking"] = {"type": config.thinking_mode}
            if tools:
                payload.update(tools=[{"type": "function", "function": t} for t in tools], tool_choice="auto")
        else:
            path = "/responses"
            inputs = []
            for m in messages[1:]:
                if m["role"] == "_responses":
                    inputs.extend(m["items"])
                elif m["role"] == "tool":
                    inputs.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
                else:
                    inputs.append({"role": m["role"], "content": m["content"]})
            payload = {"model": config.model, "instructions": messages[0]["content"], "input": inputs, "max_output_tokens": config.max_output_tokens, "store": False}
            if tools:
                payload.update(tools=[{"type": "function", **t, "strict": False} for t in tools], tool_choice="auto")
        # No automatic retry: it can multiply costs or repeat side effects.
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False, transport=self.transport) as client:
                response = await client.post(config.base_url.rstrip("/") + path, headers={"Authorization": "Bearer " + self.api_key}, json=payload)
            if response.status_code in (401, 403):
                raise BusinessError("MODEL_AUTH_FAILED", "模型服务拒绝认证，请核对本地密钥与服务权限。")
            if response.status_code == 429:
                raise BusinessError("MODEL_RATE_LIMIT", "模型额度不足或请求过多，请稍后重试或检查服务控制台。")
            response.raise_for_status()
            data = response.json()
            if config.protocol == "chat_completions":
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise BusinessError("MODEL_OUTPUT_LIMIT", "模型输出达到长度上限，请缩小问题范围。")
                message = choice["message"]
                calls = [{"id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in message.get("tool_calls", []) if c["type"] == "function"]
                continuation = {"role": "assistant", "content": message.get("content") or ""}
                if isinstance(message.get("reasoning_content"), str):
                    # 部分兼容服务要求工具往返时保留该字段；不放入网页或持久化历史。
                    continuation["reasoning_content"] = message["reasoning_content"]
                if calls:
                    continuation["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
                turn = ModelTurn(message.get("content") or "", calls, continuation)
            else:
                if data.get("status") != "completed":
                    raise BusinessError("MODEL_OUTPUT_LIMIT", "模型未完成回答，请缩小问题范围后重试。")
                output = data["output"]
                calls = [{"id": c["call_id"], "name": c["name"], "arguments": c["arguments"]} for c in output if c["type"] == "function_call"]
                answer = "".join(part["text"] for item in output if item["type"] == "message" for part in item.get("content", []) if part["type"] == "output_text")
                turn = ModelTurn(answer, calls, {"role": "_responses", "items": output})
            if not isinstance(turn.text, str) or len(turn.text) > 30000 or len(calls) > 20:
                raise ValueError()
            ids = [c["id"] for c in calls]
            if len(set(ids)) != len(ids) or any(not isinstance(c[k], str) or not c[k] for c in calls for k in ("id", "name", "arguments")):
                raise ValueError()
            return turn
        except httpx.TimeoutException:
            raise BusinessError("MODEL_TIMEOUT", "模型响应超时，请稍后重试或转人工。") from None
        except httpx.HTTPError:
            raise BusinessError("MODEL_UNAVAILABLE", "模型服务请求失败，请检查接口地址、协议和模型名称。") from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise BusinessError("MODEL_INVALID_RESPONSE", "模型返回格式不符合工具调用协议，请检查模型兼容性。") from None
