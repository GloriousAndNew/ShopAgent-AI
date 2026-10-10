import json
from dataclasses import replace
import httpx
import pytest
from shopagent.contracts.models import Identity, ToolResult, BusinessError
from shopagent.core.agent_config import AgentConfig, validate_connection
from shopagent.core.llm_provider import HTTPModelProvider, ModelTurn
from shopagent.core.llm_agent import CustomerAgent
from shopagent.core import sessions


class ScriptedProvider:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        return next(self.turns)


class Manager:
    def __init__(self, values):
        self.values, self.requests = values, []

    def snapshot(self):
        return {"generation": 1, "tools": {k: {"id": k.split(".")[0], "version": "1.0.0"} for k in self.values}}

    def tool_catalog(self, snapshot):
        return {k: {"description": k, "input": {"type": "object", "properties": {}, "additionalProperties": False}} for k in self.values}

    async def invoke(self, request, snapshot=None):
        self.requests.append(request)
        return self.values[request.tool]


@pytest.fixture
def customer(tmp_path, monkeypatch):
    import shopagent.storage as storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    sessions.initialize()
    identity = Identity(user_id="alice")
    sid, context = sessions.session(identity)
    return identity, sid, context


def call(name, args=None, cid="call-1"):
    calls = [{"id": cid, "name": name, "arguments": json.dumps(args or {})}]
    return ModelTurn(calls=calls, continuation={"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": calls[0]["arguments"]}}]})


def answer(reply="这些是示例商品。", kind="answer"):
    return ModelTurn(text=json.dumps({"kind": kind, "reply": reply}), continuation={"role": "assistant", "content": reply})


async def run(customer, manager, provider, config=None):
    identity, sid, context = customer
    return await CustomerAgent(manager, provider, config or AgentConfig()).chat("帮我选一款，再看看售后", identity, sid, context, "unit-request")


@pytest.mark.asyncio
async def test_multistep_agent_uses_tools_and_collects_evidence(customer):
    product = {"id": "P001", "name": "示例耳机", "price_fen": 19900}
    policy = {"id": "return", "title": "示例售后", "version": "1.0", "source": "policies/return.md", "text": "示例政策"}
    manager = Manager({"product.search_products": ToolResult(data={"products": [product]}), "knowledge.search_policy": ToolResult(data={"evidence": [policy]})})
    provider = ScriptedProvider([call("product__search_products"), call("knowledge__search_policy", cid="call-2"), answer()])
    value = await run(customer, manager, provider)
    assert value["mode"] == "llm" and value["model_used"]
    assert value["cards"][0]["id"] == "P001" and value["evidence"] == [policy]
    assert len(manager.requests) == 2
    assert all(r.identity.user_id == "alice" for r in manager.requests)
    assert provider.requests[1]["messages"][-1]["role"] == "tool"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["aftersale__confirm_return", "admin__disable_plugin", "shell__execute"])
async def test_unauthorized_tools_never_execute(customer, name):
    manager = Manager({})
    provider = ScriptedProvider([call(name), answer()])
    value = await run(customer, manager, provider)
    assert manager.requests == []
    assert value["error_code"] == "NO_EVIDENCE"
    assert "TOOL_NOT_ALLOWED" in provider.requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_no_evidence_blocks_factual_answer(customer):
    value = await run(customer, Manager({}), ScriptedProvider([answer("订单已退款")]))
    assert value["error_code"] == "NO_EVIDENCE" and "已退款" not in value["reply"]


@pytest.mark.asyncio
async def test_clarification_requires_no_tool(customer):
    value = await run(customer, Manager({}), ScriptedProvider([answer("请提供商品编号。", "clarify")]))
    assert value["reply"] == "请提供商品编号。" and "error_code" not in value


@pytest.mark.asyncio
async def test_unavailable_tools_not_advertised(customer):
    provider = ScriptedProvider([answer("你好", "greeting")])
    await run(customer, Manager({}), provider)
    assert [t["name"] for t in provider.requests[0]["tools"]] == ["customer_handoff"]


@pytest.mark.asyncio
async def test_confirmation_secret_not_sent_to_model_or_history(customer):
    draft = {"draft_id": "D001", "confirmation_token": "secret-test-confirmation", "order": {"id": "O1002"}, "policy": {"id": "return", "text": "示例"}}
    manager = Manager({"aftersale.prepare_return": ToolResult(data=draft)})
    provider = ScriptedProvider([call("aftersale__prepare_return"), answer("请核对示例草稿，再点击确认。")])
    value = await run(customer, manager, provider)
    assert value["draft"]["confirmation_token"] == "secret-test-confirmation"
    assert "secret-test-confirmation" not in json.dumps(provider.requests)
    identity, sid, _ = customer
    assert "secret-test-confirmation" not in json.dumps(sessions.history(sid, identity))


@pytest.mark.asyncio
async def test_repeated_tool_loop_is_bounded(customer):
    provider = ScriptedProvider([call("unknown", cid=f"call-{i}") for i in range(4)])
    value = await run(customer, Manager({}), provider, replace(AgentConfig(), max_steps=2))
    assert value["error_code"] == "AGENT_STEP_LIMIT" and len(provider.requests) == 2


@pytest.mark.asyncio
async def test_explicit_handoff_works_without_model(customer):
    identity, sid, context = customer
    provider = ScriptedProvider([])
    value = await CustomerAgent(Manager({}), provider, AgentConfig()).chat("转人工", identity, sid, context, "handoff-test")
    assert value["ticket"]["user_id"] == "alice" and not value["model_used"]
    assert provider.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_provider_http_tool_roundtrip(protocol):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer test-only-key"
        if len(requests) == 1:
            if protocol == "chat_completions":
                return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}}]}}]})
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "reasoning", "id": "r1", "summary": []}, {"type": "function_call", "call_id": "c1", "name": "search", "arguments": "{}", "id": "fc1"}]})
        if protocol == "chat_completions":
            assert body["messages"][-1]["tool_call_id"] == "c1"
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        assert body["input"][-1]["type"] == "function_call_output" and body["input"][-1]["call_id"] == "c1"
        assert any(i.get("type") == "reasoning" for i in body["input"])
        assert body["store"] is False
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})
    config = replace(AgentConfig(), protocol=protocol, base_url="https://model.example/v1", model="test-model")
    provider = HTTPModelProvider(config, "test-only-key", httpx.MockTransport(handler))
    messages = [{"role": "system", "content": "instruction"}, {"role": "user", "content": "question"}]
    tools = [{"name": "search", "description": "search", "parameters": {"type": "object", "properties": {}}}]
    turn = await provider.complete(messages, tools)
    assert turn.calls[0]["name"] == "search"
    messages += [turn.continuation, {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
    assert (await provider.complete(messages, tools)).text == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "MODEL_AUTH_FAILED"), (429, "MODEL_RATE_LIMIT"), (500, "MODEL_UNAVAILABLE")])
async def test_provider_errors_are_redacted(status, code):
    provider = HTTPModelProvider(replace(AgentConfig(), base_url="https://model.example/v1"), "secret-key", httpx.MockTransport(lambda req: httpx.Response(status, text="secret-key")))
    with pytest.raises(BusinessError) as error:
        await provider.complete([{"role": "system", "content": "test"}], [])
    assert error.value.code == code and "secret-key" not in str(error.value)


def test_agent_mode_requires_admin_and_preserves_mock_on_missing_config(clients):
    assert clients["alice"].get("/admin/agent").status_code == 403
    assert clients["bob"].post("/admin/agent/mode", json={"mode": "llm"}).status_code == 403
    value = clients["admin"].get("/admin/agent").json()
    assert value["mode"] == "mock" and "api_key" not in value
    assert clients["admin"].post("/admin/agent/mode", json={"mode": "llm"}).status_code == 409
    assert clients["alice"].get("/runtime").json()["mode"] == "mock"


def test_http_agent_with_real_plugin_processes(server, clients):
    """模型端点为测试桩；应用、身份与商品/知识插件为真实运行实例。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            system = body["messages"][0]["content"]
            foreign = "O2001" in body["messages"][-1].get("content", "")
            if "主客服 Agent，只负责" in system:
                tasks = [{"agent": "OrderAgent", "task": "查订单 O2001"}] if foreign else [{"agent": "ProductAgent", "task": "200元内降噪耳机"}, {"agent": "OrderAgent", "task": "查询拆封退货政策"}]
                message = {"content": json.dumps({"intent": "order_query" if foreign else "product_query", "tasks": tasks})}
            elif body["messages"][-1]["role"] == "tool":
                message = {"content": json.dumps({"kind": "answer", "reply": "已查询示例业务资料，请核对卡片或政策引用。"})}
            elif "你是 ProductAgent" in system:
                names = {v["function"]["name"] for v in body["tools"]}
                assert "order__get_order_status" not in names
                message = {"tool_calls": [{"id": "product-1", "type": "function", "function": {"name": "product__search_products", "arguments": json.dumps({"keyword": "耳机", "max_price_fen": 20000, "noise_cancel": True})}}]}
            elif foreign:
                message = {"tool_calls": [{"id": "foreign-order", "type": "function", "function": {"name": "order__get_order_status", "arguments": json.dumps({"order_id": "O2001"})}}]}
            else:
                message = {"tool_calls": [{"id": "policy-1", "type": "function", "function": {"name": "knowledge__search_policy", "arguments": json.dumps({"question": "拆封 退货 质量"})}}]}
            output = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    original = server.agent_config.read_text(encoding="utf-8")
    try:
        server.agent_config.write_text(f'[agent]\nmode = "mock"\nbase_url = "http://127.0.0.1:{endpoint.server_port}/v1"\nmodel = "test-only-model"\n', encoding="utf-8")
        response = clients["admin"].post("/admin/agent/mode", json={"mode": "llm"})
        assert response.status_code == 200 and response.json()["mode"] == "llm"
        response = clients["alice"].post("/chat", json={"message": "给我看看两百元内能降噪的耳机，顺便查下拆封退货政策"})
        response.raise_for_status()
        value = response.json()
        assert value["mode"] == "llm" and value["model_used"] and value["simulation"]
        assert value["cards"] and all(p["price_fen"] <= 20000 for p in value["cards"])
        assert value["evidence"] and len(value["calls"]) == 2
        assert len(requests) == 5 and requests[2]["messages"][-1]["role"] == "tool"
        assert [t["agent"] for t in value["agent_trace"]] == ["MainAgent", "ProductAgent", "OrderAgent", "MainAgent"]
        assert clients["bob"].get(f"/sessions/{value['session_id']}/messages").status_code == 404
        value = clients["alice"].post("/chat", json={"message": "帮我查询订单 O2001"}).json()
        assert value["calls"][0]["error_code"] == "NOT_FOUND"
        assert value["error_code"] == "NO_EVIDENCE" and not value["cards"]
    finally:
        clients["admin"].post("/admin/agent/mode", json={"mode": "mock"}).raise_for_status()
        server.agent_config.write_text(original, encoding="utf-8")
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("url", ["http://public.example/v1", "https://user:password@example.com/v1", "https://example.com/v1?key=secret", "https://[broken", "https://example.com:abc/v1"])
def test_invalid_or_unsafe_connection_urls(url):
    with pytest.raises(BusinessError) as error:
        validate_connection(replace(AgentConfig(), base_url=url, model="test"))
    assert error.value.code == "AGENT_CONFIG_INVALID"


@pytest.mark.asyncio
async def test_provider_timeout_is_explicit_and_redacted():
    def handler(request):
        raise httpx.ReadTimeout("secret-key", request=request)
    provider = HTTPModelProvider(replace(AgentConfig(), base_url="https://model.example/v1"), "secret-key", httpx.MockTransport(handler))
    with pytest.raises(BusinessError) as error:
        await provider.complete([{"role": "system", "content": "test"}], [])
    assert error.value.code == "MODEL_TIMEOUT" and "secret-key" not in str(error.value)


@pytest.mark.asyncio
async def test_chat_thinking_options_and_context_are_preserved():
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        if len(requests) == 1:
            message = {"content": "", "reasoning_content": "test-only-reasoning", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}}]}
        else:
            assert body["messages"][-2]["reasoning_content"] == "test-only-reasoning"
            message = {"content": "ok"}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    config = replace(AgentConfig(), base_url="https://model.example", thinking_mode="disabled")
    provider = HTTPModelProvider(config, "test-only-key", httpx.MockTransport(handler))
    messages = [{"role": "system", "content": "test"}]
    turn = await provider.complete(messages, [])
    assert turn.calls
    messages += [turn.continuation, {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
    assert (await provider.complete(messages, [])).text == "ok"
