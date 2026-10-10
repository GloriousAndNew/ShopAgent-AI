import asyncio
import logging
import json
import hmac
import os
import secrets
import time
from typing import Literal
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from shopagent.settings import ROOT, DATA, RUNTIME_ENV
from shopagent.contracts.models import StrictModel, Identity, InvokeRequest, BusinessError
from shopagent.security import verify_internal
from shopagent.core import sessions
from shopagent.core.registry import PluginManager
from shopagent.core.orchestrator import Orchestrator
from shopagent.plugins.product.admin import ProductWrite, ImportBatch, StatusWrite
from shopagent.plugins.knowledge.admin import PolicyWrite, PolicyAction
from shopagent.plugins.recommend.admin import PromotionWrite, PromotionAction
from shopagent.core import operations, maintenance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=100)


class Chat(StrictModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=100)


class UserInvoke(StrictModel):
    tool: str = Field(min_length=1, max_length=100)
    params: dict = Field(default_factory=dict)


class Confirmation(StrictModel):
    confirmation_token: str = Field(min_length=1, max_length=100)
    session_id: str | None = Field(default=None, max_length=100)


class Disable(StrictModel):
    cascade: bool = False


class Switch(StrictModel):
    version: str = Field(min_length=1, max_length=30)


class TicketReply(StrictModel):
    message: str = Field(min_length=1, max_length=3000)
    request_key: str = Field(min_length=16, max_length=100)


class AgentMode(StrictModel):
    mode: Literal["mock", "llm"]


class Handoff(StrictModel):
    message: str = Field(min_length=2,max_length=1000)
    session_id: str | None = Field(default=None,max_length=100)


class ReadReceipt(StrictModel):
    message_id: int = Field(ge=0,strict=True)


class TicketStatus(StrictModel):
    expected_status: str = Field(min_length=1,max_length=40)
    status: Literal['已关闭','待处理']


@asynccontextmanager
async def lifespan(app):
    os.environ.setdefault("SHOPAGENT_INTERNAL_KEY", secrets.token_urlsafe(48))
    sessions.initialize()
    operations.initialize()
    maintenance.initialize()
    from shopagent.core.environment import production
    if production():
        sessions.validate_production_admin()
    manager = PluginManager()
    app.state.manager = manager
    app.state.orchestrator = Orchestrator(manager)
    app.state.session_locks = {}
    from shopagent.core.limits import RateLimiter
    app.state.limiter = RateLimiter()
    app.state.chat_capacity = asyncio.Semaphore(8)
    try:
        await manager.start()
        if getattr(app.state, "stop_key", None):
            (DATA / "host.json").write_text(json.dumps({"pid": os.getpid(), "url": os.environ["SHOPAGENT_HOST_URL"], "stop_key": app.state.stop_key}), encoding="utf-8")
        async def housekeeping():
            from datetime import datetime,timezone
            while True:
                await asyncio.sleep(60)
                try:
                    value=maintenance.settings()
                    last=value['last_run']
                    due=not last or (datetime.now(timezone.utc)-datetime.fromisoformat(last['created_at'])).total_seconds()>=3600
                    if value['enabled'] and due:
                        busy=[sid for sid,lock in app.state.session_locks.items() if lock.locked()]
                        await asyncio.to_thread(maintenance.cleanup,value['revision'],'scheduler',busy)
                except Exception:
                    try:maintenance.record('MAINTENANCE_FAILED')
                    except Exception:logging.warning('维护状态记录失败，请检查数据库。')
        task=asyncio.create_task(housekeeping())
        try:yield
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    finally:
        await manager.close()
        record = DATA / "host.json"
        if record.exists() and json.loads(record.read_text(encoding="utf-8")).get("pid") == os.getpid():
            record.unlink()


app = FastAPI(title="ShopAgent AI", version="1.0.0", lifespan=lifespan, docs_url=None if RUNTIME_ENV=="production" else "/docs", redoc_url=None if RUNTIME_ENV=="production" else "/redoc", openapi_url=None if RUNTIME_ENV=="production" else "/openapi.json")
app.mount("/static", StaticFiles(directory=ROOT / "shopagent" / "web"), name="static")


@app.middleware('http')
async def response_security(request:Request,call_next):
    try:response=await call_next(request)
    except Exception:
        try:maintenance.record('HTTP_UNEXPECTED')
        except Exception:logging.warning('运行状态记录失败，请检查数据库。')
        raise
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    if not request.url.path.startswith('/static/'):
        response.headers['Cache-Control']='no-store'
    if request.url.path in ('/','/customer','/merchant'):
        response.headers['Content-Security-Policy']="default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.exception_handler(BusinessError)
async def business_error(request, error):
    status = {"UNAUTHORIZED": 401, "LOGIN_FAILED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404, 'RATE_LIMITED':429}.get(error.code, 409)
    return JSONResponse(status_code=status, content={"error_code": error.code, "message": error.message})


async def identity(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "请先登录。")
    return sessions.authenticate(authorization[7:])


async def admin(user: Identity = Depends(identity)):
    if user.role != "admin":
        raise BusinessError("FORBIDDEN", "只有管理员可以操作插件或查看工单。")
    return user


@app.get("/")
@app.get('/customer')
@app.get('/merchant')
async def index(request:Request):
    from shopagent.core.environment import production
    if production() and request.url.path=='/':
        return RedirectResponse('/customer')
    return FileResponse(ROOT / "shopagent/web/index.html")


@app.get("/health")
async def health():
    from shopagent.core.environment import production
    return {"status": "ok", "mode": app.state.orchestrator.mode, "simulation": not production(), "generation": app.state.manager.generation}


@app.get("/runtime")
async def runtime():
    from shopagent.core.environment import production
    return {"mode": app.state.orchestrator.mode, "environment":'production' if production() else 'development', "business_data": 'merchant' if production() else 'sample', 'customer_entry':'/customer', 'merchant_entry':'/merchant','guest_scope':'商品、库存、优惠与政策咨询；订单和售后转人工','guest_expires_in':28800,'storefront':operations.public_info()}


@app.get('/admin/operations')
async def operating_settings(user:Identity=Depends(admin)):
    return operations.read()


@app.post('/admin/operations')
async def operating_save(body:operations.SettingsWrite,user:Identity=Depends(admin)):
    return operations.save(body,user.user_id)


@app.get("/agents")
async def agents(user: Identity = Depends(identity)):
    from shopagent.agents.registry import overview
    return {"mode": app.state.orchestrator.mode, "roles": overview(app.state.manager),
            "main": {"id": "MainAgent", "label": "主客服 Agent", "job": "理解问题 → 拆分任务 → 分派给专业 Agent → 汇总已查证的回答"}}


@app.post("/auth/login")
async def login(body: Login, request: Request):
    app.state.limiter.check(('login',request.client.host),30,60)
    return await asyncio.to_thread(sessions.login, body.username, body.password)


@app.post('/auth/guest')
async def guest(request: Request, body:maintenance.GuestNotice):
    app.state.limiter.check(('guest',request.client.host),10,3600)
    if RUNTIME_ENV=='production' and (not body.acknowledged or body.notice_version!=maintenance.current_version()):
        raise BusinessError('PRIVACY_REQUIRED','请先阅读当前咨询数据说明，再开始咨询。')
    return await asyncio.to_thread(sessions.guest_login,body.notice_version if body.acknowledged else None)


@app.get("/auth/me")
async def me(user: Identity = Depends(identity)):
    return {"user": user, "mode": app.state.orchestrator.mode}


@app.post("/auth/logout")
async def logout(authorization: str = Header(), user: Identity = Depends(identity)):
    from shopagent.storage import connect
    from shopagent.security import digest
    with connect("core") as db:
        db.execute("DELETE FROM tokens WHERE hash=?", (digest(authorization[7:]),))
    return {"ok": True}


@app.get("/sessions")
async def list_sessions(user: Identity = Depends(identity)):
    from shopagent.storage import connect
    with connect("core") as db:
        rows = db.execute("SELECT id,created_at FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 30", (user.user_id,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/sessions/{sid}/messages")
async def messages(sid: str, user: Identity = Depends(identity)):
    return sessions.history(sid, user)


@app.post('/sessions/{sid}/read')
async def read_messages(sid: str, body: ReadReceipt, user: Identity=Depends(identity)):
    return sessions.mark_read(sid,user,body.message_id)


@app.get('/customer/tickets')
async def customer_tickets(user: Identity=Depends(identity)):
    return sessions.customer_tickets(user)


@app.post('/customer/handoff')
async def customer_handoff(body:Handoff,user:Identity=Depends(identity)):
    if len(body.message.strip())<2:
        raise HTTPException(422,'请描述需要人工处理的问题。')
    app.state.limiter.check(('handoff',user.user_id),10,3600)
    sid,context=sessions.session(user,body.session_id)
    lock=app.state.session_locks.setdefault(sid,asyncio.Lock())
    async with lock:
        ticket=sessions.ticket(user,sid,body.message.strip())
        result={'reply':f"已登记人工咨询 {ticket['id']}，当前状态：{ticket['status']}。在‘我的人工咨询’查看回复，不保证即时回复；关闭网页后没有短信或邮件通知。",'ticket':ticket,'session_id':sid,'source':'system','mode':'human','cards':[]}
        sessions.save(sid,user,context,body.message.strip(),result)
        return result


@app.post("/chat")
async def chat(body: Chat, user: Identity = Depends(identity)):
    if not body.message.strip():
        raise HTTPException(422, "请输入问题。")
    from shopagent.core.customer_access import is_guest
    app.state.limiter.check(('chat',user.user_id),60 if is_guest(user) else 300,3600)
    sid, _ = sessions.session(user, body.session_id)
    locks = app.state.session_locks
    lock = locks.setdefault(sid, asyncio.Lock())
    try:
        await asyncio.wait_for(app.state.chat_capacity.acquire(),timeout=.05)
    except TimeoutError:
        raise BusinessError('RATE_LIMITED','当前咨询较多，请稍后重试或登记人工咨询。')
    try:
        async with lock:
            sid, context = sessions.session(user, sid)
            result=await app.state.orchestrator.chat(body.message.strip(), user, sid, context, secrets.token_hex(12))
            if result.get('error_code'):
                try:maintenance.record(result['error_code'])
                except Exception:logging.warning('运行状态记录失败，请检查数据库。')
            return result
    finally:
        app.state.chat_capacity.release()


@app.post("/tools/invoke")
async def invoke(body: UserInvoke, user: Identity = Depends(identity)):
    # 通用能力接口不信任浏览器提供的身份；写入仍由插件确认凭据保护。
    return await app.state.manager.invoke(InvokeRequest(tool=body.tool, params=body.params, identity=user, request_id=secrets.token_hex(12)))


@app.post("/internal/invoke")
async def internal(request: Request):
    raw = await verify_internal(request)
    return await app.state.manager.invoke(InvokeRequest.model_validate(raw))


@app.post("/internal/shutdown")
async def shutdown(x_stop_key: str = Header(default="")):
    expected = getattr(app.state, "stop_key", "")
    if not expected or not hmac.compare_digest(expected, x_stop_key):
        raise HTTPException(401, "本机停止凭据无效。")
    app.state.stop_server()
    return {"ok": True, "message": "正在停止宿主及其管理的插件。"}


async def handle_confirmation(draft_id, body, user, tool):
    result = await app.state.manager.invoke(InvokeRequest(tool=tool, params={"draft_id": draft_id, "confirmation_token": body.confirmation_token}, identity=user, request_id=secrets.token_hex(12)))
    if result.status == "error":
        raise BusinessError(result.error_code, result.message)
    if body.session_id:
        lock = app.state.session_locks.setdefault(body.session_id, asyncio.Lock())
        async with lock:
            sid, context = sessions.session(user, body.session_id)
            if tool.endswith("confirm_return"):
                context["return_id"] = result.data["id"]
                reply = f"模拟申请已提交，售后单 {result.data['id']}，状态：{result.data['status']}。没有执行退款或真实取件。"
            else:
                reply = "售后草稿已取消。"
            sessions.save(sid, user, context, "确认提交模拟申请" if tool.endswith("confirm_return") else "取消售后草稿", {"reply": reply, "cards": [{"type": "return", **result.data}], "mode": "mock"})
    return result


@app.post("/returns/{draft_id}/confirm")
async def confirm(draft_id: str, body: Confirmation, user: Identity = Depends(identity)):
    if body.session_id:
        sessions.session(user, body.session_id)  # 写入之前校验会话归属。
    return await handle_confirmation(draft_id, body, user, "aftersale.confirm_return")


@app.post("/returns/{draft_id}/cancel")
async def cancel(draft_id: str, body: Confirmation, user: Identity = Depends(identity)):
    if body.session_id:
        sessions.session(user, body.session_id)
    return await handle_confirmation(draft_id, body, user, "aftersale.cancel_return")


@app.get("/admin/plugins")
async def plugins(user: Identity = Depends(admin)):
    return app.state.manager.overview()


async def merchant_call(tool, params, user):
    result = await app.state.manager.invoke(InvokeRequest(tool=tool, params=params, identity=user, request_id=secrets.token_hex(12)))
    if result.status == "error":
        raise BusinessError(result.error_code, result.message)
    return result.data


@app.get("/admin/policies")
async def policy_catalog(user: Identity = Depends(admin)):
    return await merchant_call("knowledge.admin_catalog", {}, user)


@app.post("/admin/policies/save")
async def policy_save(body: PolicyWrite, user: Identity = Depends(admin)):
    return await merchant_call("knowledge.admin_save", body.model_dump(mode="json"), user)


@app.post("/admin/policies/action")
async def policy_action(body: PolicyAction, user: Identity = Depends(admin)):
    return await merchant_call("knowledge.admin_action", body.model_dump(), user)


@app.get("/admin/policies/{policy_id}/history")
async def policy_history(policy_id: str, user: Identity = Depends(admin)):
    return await merchant_call("knowledge.admin_history", {"policy_id": policy_id}, user)


@app.get("/admin/policy-starter")
async def policy_starter(user: Identity = Depends(admin)):
    from shopagent.core.environment import production
    if production():
        raise BusinessError('PRODUCTION_SCOPE','正式环境不提供示例政策资料包。')
    drafts = []
    for path in sorted((ROOT / "docs/店铺资料包/政策草案").glob("*.md")):
        header, text = path.read_text(encoding="utf-8").split("\n\n", 1)
        meta = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
        pid = meta["id"].removeprefix("starter_")
        drafts.append({"policy_id": pid, "title": meta["title"].removesuffix("（未发布草案）"), "tags": meta["tags"], "text": text.strip(), "kind": "return" if pid == "return" else "general", "return_rule": {"days": 7, "start_next_day": True, "opened_requires_review": True, "quality_manual_review": True} if pid == "return" else None, "simulation": True})
    return {"drafts": drafts}


@app.get("/admin/promotions")
async def promotions_catalog(user: Identity = Depends(admin)):
    return await merchant_call("recommend.admin_promotions", {}, user)


@app.post("/admin/promotions/save")
async def promotion_save(body: PromotionWrite, user: Identity = Depends(admin)):
    return await merchant_call("recommend.admin_save_promotion", body.model_dump(mode="json"), user)


@app.post("/admin/promotions/action")
async def promotion_action(body: PromotionAction, user: Identity = Depends(admin)):
    return await merchant_call("recommend.admin_promotion_action", body.model_dump(), user)


async def product_admin_call(tool, params, user):
    result = await app.state.manager.invoke(InvokeRequest(tool="product."+tool, params=params, identity=user, request_id=secrets.token_hex(12)))
    if result.status == "error":
        raise BusinessError(result.error_code, result.message)
    return result.data


@app.get("/admin/products")
async def product_catalog(keyword: str = "", status: str = "all", user: Identity = Depends(admin)):
    return await product_admin_call("admin_catalog", {"keyword": keyword, "status": status}, user)


@app.post("/admin/products/save")
async def save_product(body: ProductWrite, user: Identity = Depends(admin)):
    return await product_admin_call("admin_save_product", body.model_dump(), user)


@app.post("/admin/products/status")
async def product_status(body: StatusWrite, user: Identity = Depends(admin)):
    return await product_admin_call("admin_set_status", body.model_dump(), user)


@app.post("/admin/products/import/preview")
async def product_import_preview(body: ImportBatch, user: Identity = Depends(admin)):
    return await product_admin_call("admin_preview_import", body.model_dump(), user)


@app.post("/admin/products/import/commit")
async def product_import_commit(body: ImportBatch, user: Identity = Depends(admin)):
    return await product_admin_call("admin_commit_import", body.model_dump(), user)


@app.get("/admin/products/starter")
async def product_starter(user: Identity = Depends(admin)):
    from shopagent.core.environment import production
    if production():
        raise BusinessError('PRODUCTION_SCOPE','正式环境不提供示例商品资料包。')
    # 固定本地资料路径；客户端不能指定文件路径，更不能读取配置或密钥。
    path = ROOT / "docs/店铺资料包/商品资料.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = set(ProductWrite.model_fields) - {"product_id", "expected_revision", "skus"}
    products = [{**{k: p[k] for k in fields if k in p and p[k] is not None}, "product_id": p["id"], "expected_revision": 0,
                 "status": "draft", "skus": [{"color": s["color"], "stock": s["stock"]} for s in p["skus"]]} for p in data["products"]]
    return {"products": products, "notice": "10款虚构开发商品，只导入草稿。政策不会导入或发布。"}


@app.post("/admin/plugins/{name}/enable")
async def enable(name: str, user: Identity = Depends(admin)):
    return await app.state.manager.enable(name)


@app.post("/admin/plugins/{name}/disable")
async def disable(name: str, body: Disable, user: Identity = Depends(admin)):
    return await app.state.manager.disable(name, body.cascade)


@app.post("/admin/plugins/{name}/switch-version")
async def switch(name: str, body: Switch, user: Identity = Depends(admin)):
    return await app.state.manager.switch(name, body.version)


@app.get("/admin/tickets")
async def tickets(status:str='all',user: Identity = Depends(admin)):
    from shopagent.storage import connect
    with connect("core") as db:
        if status not in ('all','pending','replied','closed'):
            raise HTTPException(422,'咨询状态筛选无效。')
        where={'all':'1=1','pending':"status LIKE '待处理%'",'replied':"status='已回复'",'closed':"status='已关闭'"}[status]
        return [dict(r) for r in db.execute(f"SELECT id,user_id,content,status,created_at FROM tickets WHERE {where} ORDER BY created_at DESC LIMIT 100")]


@app.get('/admin/tickets/{ticket_id}')
async def ticket_detail(ticket_id:str,user:Identity=Depends(admin)):
    return sessions.ticket_detail(user,ticket_id)


@app.post('/admin/tickets/{ticket_id}/status')
async def ticket_state(ticket_id:str,body:TicketStatus,user:Identity=Depends(admin)):
    return sessions.ticket_status(user,ticket_id,body.expected_status,body.status)


@app.get('/admin/launch-readiness')
async def launch_readiness(user:Identity=Depends(admin)):
    from shopagent.core.environment import production
    if production():
        return {'ready':False,'customer_entry':'/customer','merchant_entry':'/merchant','completed':['正式管理员初始化','独立生产目录，禁止示例账户和自动业务种子','示例资料禁止发布与公开查询','订单、物流、售后、演示模块禁止启用','顾客咨询与人工回复闭环','店铺信息维护与持久化每日模型调用额度','停机备份、校验与恢复到新目录工具','宿主请求边界与显式同机代理信任','本机验证的Nginx HTTPS模板；Linux服务模板待服务器验收','咨询数据告知与正式访客版本校验','默认关闭的过期访客清理、运行事件与模块故障查看'],
                'blockers':['仍需人工审核真实资料、店铺信息和服务政策','需配置真实模型并设定每日调用额度' if app.state.orchestrator.mode!='llm' else '需配置费用预算和可信网关限流','尚未完成公网HTTPS与外网验收','需在正式环境演练备份恢复，确认商家告知和保留期限，安排运行监控值守'], 'limits':'生产模式不等于已经部署；访客凭据8小时，限流为单进程内存状态。'}
    return {'ready':False,'customer_entry':'/customer','merchant_entry':'/merchant',
            'completed':['生产模式与独立管理员初始化代码','生产库不生成示例资料，示例内容禁止发布与公开查询','商品咨询访客入口（8小时凭据）','人工咨询上下文、回复、关闭与重新打开','用户归属和未读回复','单进程登录/访客/咨询限流与并发保护','店铺信息维护与持久化每日模型调用额度','停机备份校验与本机开发数据恢复演练','宿主请求边界与显式同机代理信任','本机验证的Nginx HTTPS模板；Linux服务模板待服务器验收','咨询数据告知与正式访客版本校验','默认关闭的过期访客清理、运行事件与模块故障查看'],
            'blockers':['当前仍为开发演示模式，请初始化独立生产库的管理员并使用正式启动脚本','正式资料仍需商家核实录入，不自动迁移演示库','尚未配置公网HTTPS、域名和可信反向代理','需在正式环境演练备份恢复，确认商家告知和保留期限、密钥管理及运行监控值守'],
            'limits':'限流为单宿主进程内存状态，重启会清空；反向代理前不能当成多实例防滥用方案。'}


@app.post("/admin/tickets/{ticket_id}/reply")
async def reply_ticket(ticket_id: str, body: TicketReply, user: Identity = Depends(admin)):
    return await asyncio.to_thread(sessions.reply_ticket, user, ticket_id, body.message, body.request_key)


@app.get("/admin/agent")
async def agent_status(user: Identity = Depends(admin)):
    return app.state.orchestrator.agent_status()


@app.post("/admin/agent/mode")
async def agent_mode(body: AgentMode, user: Identity = Depends(admin)):
    return app.state.orchestrator.set_mode(body.mode)


# 最外层先验证请求边界，再进入解析、鉴权和业务模块。
from shopagent.core.gateway import GatewayBoundary
app.add_middleware(GatewayBoundary)


@app.get('/privacy')
async def privacy_notice():
    value=maintenance.notice()
    if app.state.orchestrator.mode=='llm':
        from urllib.parse import urlsplit
        host=urlsplit(app.state.orchestrator.agent.config.base_url).hostname
        provider='DeepSeek' if host=='api.deepseek.com' else '商家配置的模型服务（具体名称请向商家确认）'
        value['paragraphs'][1]='当前启用真实 AI，模型服务为 '+provider+'。你的问题、相关聊天上下文和必要商品或政策资料会发送给该服务处理。'
    else:value['paragraphs'][1]='当前使用规则测试模式，不向模型服务发送问题；以后启用真实 AI 时，问题、相关上下文及必要业务资料会发送到商家配置的模型服务。'
    return value

@app.get('/admin/maintenance')
async def maintenance_status(user:Identity=Depends(admin)):
    busy=[sid for sid,lock in app.state.session_locks.items() if lock.locked()]
    return {'retention':maintenance.preview(busy),'monitor':maintenance.report(),'modules':[{k:p[k] for k in ('id','state')} for p in app.state.manager.overview()]}

@app.post('/admin/retention')
async def retention_save(body:maintenance.RetentionWrite,user:Identity=Depends(admin)):
    return maintenance.save(body,user.user_id)

@app.post('/admin/retention/cleanup')
async def retention_cleanup(body:maintenance.CleanupRequest,user:Identity=Depends(admin)):
    busy=[sid for sid,lock in app.state.session_locks.items() if lock.locked()]
    return await asyncio.to_thread(maintenance.cleanup,body.expected_revision,user.user_id,busy)
