import time
from shopagent.contracts.models import InvokeRequest, BusinessError
from shopagent.core.model_provider import MockModelProvider
from shopagent.core import sessions
from shopagent.core.agent_config import load_config, validate_connection
from shopagent.core.llm_provider import HTTPModelProvider
from shopagent.agents.main import MainAgent
from shopagent.agents.registry import overview


class Orchestrator:
    def __init__(self, manager):
        self.manager = manager
        self.provider = MockModelProvider()
        self.agent = None
        config = load_config()
        if config.mode == "llm":
            self.set_mode("llm")

    @property
    def mode(self):
        return "llm" if self.agent else "mock"

    def agent_status(self):
        config = load_config()
        try:
            validate_connection(config)
            configured = True
        except BusinessError:
            configured = False
        active = self.agent.config if self.agent else config
        return {"mode": self.mode, "configured": configured, "model": active.model, "protocol": active.protocol,
                "business_data": "sample", "agent_count": 4 if self.agent else 0,
                "agents": overview(self.manager), "main_agent": "MainAgent", "planning": "主 Agent 内的串行任务规划，非第五个独立 Agent",
                "mcp_transport": "SDK in-memory", "a2a_transport": "python-a2a 消息 + 本地派发；尚未开放跨进程网络协议",
                "max_steps": active.max_steps, "max_tool_calls": active.max_tool_calls,
                "max_model_calls": active.max_steps + 4,
                "notice": "配置完整不代表已验证模型连接；实际问答成功后才算接通。"}

    def set_mode(self, mode):
        if mode == "llm":
            config = load_config()
            from shopagent.core.operations import GuardedProvider
            candidate = MainAgent(self.manager, GuardedProvider(HTTPModelProvider(config, validate_connection(config))), config)
            self.agent = candidate
        elif mode == "mock":
            self.agent = None
        else:
            raise BusinessError("AGENT_CONFIG_INVALID", "只支持 mock 或 llm 模式。")
        return self.agent_status()

    async def chat(self, question, identity, sid, context, request_id):
        agent = self.agent  # 已开始的请求继续使用本轮实例，切换只影响后续请求。
        if agent:
            return await agent.chat(question, identity, sid, context, request_id)
        decision = self.provider.classify(question, context)
        snapshot = self.manager.snapshot()
        result = {"reply": "", "intent": decision.intent, "mode": self.provider.mode, "simulation": True, "session_id": sid, "request_id": request_id, "cards": [], "evidence": [], "calls": [], "generation": snapshot["generation"]}

        async def invoke(tool, params):
            response = await self.manager.invoke(InvokeRequest(tool=tool, params=params, identity=identity, request_id=request_id, deadline=time.time()+30), snapshot=snapshot)
            result["calls"].append({"tool": tool, "status": response.status, "version": response.plugin_version, "error_code": response.error_code})
            if response.status == "error":
                result["error_code"] = response.error_code
                raise BusinessError(response.error_code, response.message)
            return response.data

        intent, params = decision.intent, decision.params
        try:
            if intent == "clarify":
                result["reply"] = params["reply"]
            elif intent == "return_cancel_collect":
                context.pop("pending_return", None)
                result["reply"] = "已取消信息收集，尚未创建申请。"
            elif intent == "return_collect":
                context["pending_return"] = params["values"]
                result["reply"] = params["reply"]
            elif intent == "handoff":
                context.pop("pending_return", None)
                ticket = sessions.ticket(identity, sid, question)
                result["reply"] = f"已创建咨询 {ticket['id']}。商家可以在后台查看并回复；回复后请点击“查看客服回复”。目前没有短信或邮件通知，也不保证即时回复。"
                result["ticket"] = ticket
            elif intent == "product_query":
                data = await invoke("product.search_products", params)
                products = data["products"]
                context["product_ids"] = [p["id"] for p in products]
                context["product_id"] = products[0]["id"] if len(products) == 1 else None
                result["cards"] = [{"type": "product", **p} for p in products]
                result["reply"] = f"找到 {len(products)} 件符合条件的演示商品。可继续问“第二款黑色还有货吗”。" if products else "没有找到符合预算和条件的商品，可以调整预算或关键词。"
            elif intent in ("inventory_check", "product_detail"):
                if not params.get("product_id"):
                    result["reply"] = "请提供商品编号（例如 P001）或先查询商品；没有明确商品时我不会猜测。"
                else:
                    context["product_id"] = params["product_id"]
                    tool = "product.check_inventory" if intent == "inventory_check" else "product.get_product_detail"
                    data = await invoke(tool, params)
                    result["cards"] = [{"type": "inventory" if intent == "inventory_check" else "detail", **data}]
                    result["reply"] = (f"{data['product_id']} {data['color']} 库存 {data['stock']} 件，以本次 SKU 查询时间为准。" if intent == "inventory_check" else f"这是 {data['name']} 的演示商品详情。")
            elif intent == "order_list":
                data = await invoke("order.list_orders", {})
                result["cards"] = [{"type": "order", **p} for p in data["orders"]]
                result["reply"] = f"以下是 {identity.user_id} 的演示订单。可指定编号查物流或申请售后。"
            elif intent in ("order_query", "logistics_track"):
                if not params.get("order_id"):
                    result["reply"] = "请提供订单号（例如 O1001），或先问“我的订单”。"
                else:
                    tool = "order.get_order_status" if intent == "order_query" else "logistics.track_logistics"
                    data = await invoke(tool, params)
                    context["order_id"] = params["order_id"]
                    result["cards"] = [{"type": "order" if intent == "order_query" else "logistics", **data}]
                    result["reply"] = (f"订单 {data['id']} 当前状态：{data['status']}。" if intent == "order_query" else f"订单 {data['order_id']}：{data['status']}。当前没有预计送达时间，不能承诺明天到达。")
            elif intent == "policy_query":
                data = await invoke("knowledge.search_policy", params)
                result["evidence"] = data["evidence"]
                result["reply"] = "以下是模拟店铺的相关政策，请核对条件和引用版本。" if data["evidence"] else "当前政策库没有支持这条问题的依据，我无法确认；可以转人工。"
            elif intent == "return_apply":
                data = await invoke("aftersale.prepare_return", params)
                context.pop("pending_return", None)
                context["order_id"] = params["order_id"]
                result["draft"] = data
                result["evidence"] = [data["policy"]]
                result["reply"] = "已生成售后草稿，请核对下方资料。点击“确认提交模拟申请”后才创建申请单；这不会执行退款或真实取件。"
            elif intent == "return_status":
                data = await invoke("aftersale.get_return_status", params)
                result["cards"] = [{"type": "return", **data}]
                result["reply"] = f"售后单 {data['id']}：{data['status']}。"
        except BusinessError as error:
            result["reply"] = error.message
            result["error_code"] = error.code
            if intent == "return_apply":
                context.pop("pending_return", None)
        from shopagent.core.environment import production
        if production():
            result['simulation']=False
            result['reply']=result['reply'].replace('演示商品','已发布商品').replace('模拟店铺','店铺')
        sessions.save(sid, identity, context, question, result)
        return result
