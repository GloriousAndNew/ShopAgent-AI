import json
from dataclasses import replace
import pytest
from conftest import invoke
from shopagent.agents.main import MainAgent
from shopagent.agents.transport import MCPToolGateway
from shopagent.core.agent_config import AgentConfig
from shopagent.core.llm_provider import ModelTurn
from shopagent.contracts.models import InvokeRequest, Identity, ToolResult


def test_original_tools_and_discount(clients):
    buyer = clients["alice"]
    compared = invoke(buyer, "product.compare_prices", {"product_ids": ["P001", "P002"]})
    assert compared["status"] == "ok" and compared["data"]["cheapest_id"] == "P002"
    value = invoke(buyer, "recommend.calculate_discount", {"product_id": "P001", "quantity": 2})
    assert value["data"]["payable_fen"] == 34020
    assert invoke(buyer, "recommend.calculate_discount", {"product_id": "P006"})["data"]["discount_fen"] == 0
    assert invoke(buyer, "recommend.calculate_discount", {"product_id": "P001", "promotion_id": "fake"})["error_code"] == "NOT_FOUND"


def test_recommendation_records_are_owned(clients):
    assert invoke(clients["alice"], "recommend.save_shopping_record", {"product_id": "P001", "note": "喜欢黑色"})["status"] == "ok"
    alice = invoke(clients["alice"], "recommend.get_user_profile", {})["data"]
    bob = invoke(clients["bob"], "recommend.get_user_profile", {})["data"]
    assert alice["records"] and not bob["records"]
    assert alice["user_id"] == "alice" and bob["user_id"] == "bob"
    assert invoke(clients["bob"], "recommend.get_user_profile", {"user_id": "alice"})["error_code"] == "VALIDATION_ERROR"


def test_recommend_hotplug_dependencies(clients):
    admin = clients["admin"]
    assert admin.post("/admin/plugins/product/disable", json={}).status_code == 409
    assert admin.post("/admin/plugins/recommend/disable", json={}).status_code == 200
    assert invoke(clients["alice"], "recommend.query_promotions", {})["error_code"] == "PLUGIN_UNAVAILABLE"
    assert invoke(clients["alice"], "product.get_product_detail", {"product_id": "P001"})["status"] == "ok"
    assert admin.post("/admin/plugins/recommend/enable", json={}).status_code == 200
    roles = clients["alice"].get("/agents").json()["roles"]
    assert {r["id"] for r in roles} == {"ProductAgent", "OrderAgent", "RecommendAgent"}


class Manager:
    def __init__(self):
        self.received = []
        self.names = {"recommend.get_recommendations", "product.check_inventory", "recommend.calculate_discount"}

    def snapshot(self):
        return {"generation": 1, "tools": {n: {"id": n.split('.')[0], "version": "1.0.0"} for n in self.names}}

    def tool_catalog(self, snapshot):
        return {n: {"description": n, "input": {"type": "object", "additionalProperties": True}} for n in snapshot["tools"]}

    async def invoke(self, req, snapshot=None):
        self.received.append(req)
        data = {"product_id": "P001", "stock": 12, "color": "黑色"}
        if req.tool.startswith("recommend"):
            data = {"products": [{"id": "P001", "name": "耳机", "price_fen": 18900}]} if "recommendations" in req.tool else {"product_id": "P001", "payable_fen": 17010}
        return ToolResult(data=data)


@pytest.mark.asyncio
async def test_three_step_collaboration_uses_mcp_and_restricted_tools(monkeypatch):
    from shopagent.core import sessions
    monkeypatch.setattr(sessions, "history", lambda *a: [])
    saved = []
    monkeypatch.setattr(sessions, "save", lambda *a: saved.append(a))
    manager = Manager()
    class Provider:
        async def complete(self, messages, tools):
            system = messages[0]["content"]
            if not tools:
                plan = {"intent": "recommend_query", "tasks": [{"agent": "RecommendAgent", "task": "按预算推荐"}, {"agent": "ProductAgent", "task": "查黑色库存"}, {"agent": "RecommendAgent", "task": "算有货商品优惠"}]}
                return ModelTurn(text=json.dumps(plan))
            if messages[-1]["role"] == "tool":
                text = json.dumps({"kind": "answer", "reply": "根据示例工具查证完成。"})
                return ModelTurn(text=text, continuation={"role": "assistant", "content": text})
            names = {t["name"] for t in tools}
            if "你是 ProductAgent" in system:
                assert "recommend__calculate_discount" not in names
                name, args = "product__check_inventory", {"product_id": "P001", "color": "黑色"}
                assert "前序 Agent 已核实的数据" in messages[-1]["content"]
            else:
                assert "product__check_inventory" not in names
                name, args = ("recommend__calculate_discount", {"product_id": "P001"}) if "算有货" in messages[-1]["content"] else ("recommend__get_recommendations", {})
            return ModelTurn(calls=[{"id": name, "name": name, "arguments": json.dumps(args)}], continuation={"role": "assistant", "content": ""})
    main = MainAgent(manager, Provider(), replace(AgentConfig(), timeout_seconds=30))
    value = await main.chat("推荐并查库存优惠", Identity(user_id="alice", role="buyer"), "sid", {}, "req")
    assert "error_code" not in value
    assert [c["agent"] for c in value["calls"]] == ["RecommendAgent", "ProductAgent", "RecommendAgent"]
    assert all(r.identity.user_id == "alice" for r in manager.received)
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_mcp_gateway_rejects_cross_role_tool():
    manager = Manager()
    gateway = MCPToolGateway(manager, {"product.check_inventory"})
    response = await gateway.invoke(InvokeRequest(tool="recommend.calculate_discount", params={}, identity=Identity(user_id="bob", role="buyer"), request_id="r", deadline=9999999999))
    assert response.error_code == "TOOL_NOT_ALLOWED" and not manager.received
