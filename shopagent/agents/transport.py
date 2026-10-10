"""MVP 本地传输：MCP 完整会话；python-a2a 消息编解码与本地派发。

没有开放外部 A2A 网络接口，身份作为可信宿主参数传递，不能由消息覆盖。
"""
import json
from datetime import timedelta
from mcp.server import Server
from mcp.types import Tool, TextContent as MCPText
from mcp.shared.memory import create_connected_server_and_client_session
from python_a2a import Message, TextContent, MessageRole
from shopagent.contracts.models import ToolResult, failure


class MCPToolGateway:
    def __init__(self, manager, allowed):
        self.manager, self.allowed = manager, allowed

    def snapshot(self):
        snapshot = self.manager.snapshot()
        return {**snapshot, "tools": {k: v for k, v in snapshot["tools"].items() if k in self.allowed}}

    def tool_catalog(self, snapshot):
        return {k: v for k, v in self.manager.tool_catalog(snapshot).items() if k in self.allowed}

    async def invoke(self, req, snapshot=None):
        if req.tool not in self.allowed:
            return failure("TOOL_NOT_ALLOWED", "该 Agent 无权调用此工具。")
        snapshot = snapshot or self.snapshot()
        catalog = self.tool_catalog(snapshot)
        aliases = {name.replace(".", "__"): name for name in catalog}
        server = Server("ShopAgent-business-tools")

        @server.list_tools()
        async def list_tools():
            return [Tool(name=alias, description=catalog[name]["description"], inputSchema=catalog[name]["input"]) for alias, name in aliases.items()]

        @server.call_tool()
        async def call_tool(name, arguments):
            original = aliases.get(name)
            if not original:
                value = failure("TOOL_NOT_ALLOWED", "工具已下线或不在本轮授权目录中。")
            else:
                trusted = req.model_copy(update={"tool": original, "params": arguments})
                value = await self.manager.invoke(trusted, snapshot=snapshot)
            return [MCPText(type="text", text=value.model_dump_json())]

        async with create_connected_server_and_client_session(server, read_timeout_seconds=timedelta(seconds=12)) as client:
            await client.list_tools()
            response = await client.call_tool(req.tool.replace(".", "__"), req.params)
            if response.isError:
                return failure("MCP_TOOL_ERROR", "MCP 工具调用失败，请核对参数或重试。")
            return ToolResult.model_validate_json(response.content[0].text)


class AgentMessageBus:
    async def dispatch(self, name, agent, task, identity, sid, context, request_id):
        request = Message(content=TextContent(text=task), role=MessageRole.USER, conversation_id=sid)
        # 序列化边界确保任务仅携带文本，身份不能由模型构造。
        received = Message.from_dict(json.loads(json.dumps(request.to_dict(), ensure_ascii=False)))
        result = await agent.chat(received.content.text, identity, sid, context, request_id)
        reply = Message(content=TextContent(text=json.dumps(result, ensure_ascii=False)), role=MessageRole.AGENT,
                        parent_message_id=request.message_id, conversation_id=sid)
        received_reply = Message.from_dict(json.loads(json.dumps(reply.to_dict(), ensure_ascii=False)))
        return json.loads(received_reply.content.text)
