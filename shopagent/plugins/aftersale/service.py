import hmac
import json
import secrets
import time
from datetime import datetime, timezone, timedelta
from pydantic import Field
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.plugins.client import call
from shopagent.security import digest
from shopagent.storage import connect, encode, now


class Prepare(StrictModel):
    order_id: str = Field(pattern=r"^O\d{4}$")
    reason: str = Field(min_length=2, max_length=300)
    opened: bool
    quality_issue: bool


class Confirm(StrictModel):
    draft_id: str = Field(min_length=1, max_length=80)
    confirmation_token: str = Field(min_length=1, max_length=100)


class Cancel(Confirm):
    pass


class Status(StrictModel):
    return_id: str = Field(min_length=1, max_length=80)


def initialize():
    with connect("aftersale") as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,user_id TEXT,payload TEXT,token_hash TEXT,expires_at REAL,state TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS returns(id TEXT PRIMARY KEY,draft_id TEXT UNIQUE,user_id TEXT,payload TEXT,status TEXT,created_at TEXT);
        PRAGMA user_version=1;""")
        from shopagent.core.environment import production
        if production():
            return
        for i in range(5):
            db.execute("INSERT OR IGNORE INTO returns VALUES(?,?,?,?,?,?)", (f"RT-DEMO{i+1}", f"seed-{i}", "alice" if i < 3 else "bob", encode({"order_id": "O1002" if i < 3 else "O2002", "simulation": True, "seed": True}), "演示历史：待审核", now()))


def eligible(order, rule, params):
    if order["status"] != "已签收" or not order.get("delivered_at"):
        raise BusinessError("INELIGIBLE", "演示规则要求订单已签收；未签收或已取消订单暂不能申请。")
    delivered = datetime.fromisoformat(order["delivered_at"])
    instant = datetime.now(timezone.utc)
    if delivered > instant:
        raise BusinessError("INELIGIBLE", "签收时间异常，请转人工核实。")
    if params.quality_issue:
        if rule.get("quality_manual_review"):
            raise BusinessError("MANUAL_REVIEW", "质量问题需人工单独核验，不套用无理由退货期限。请转人工。")
        return
    if rule.get("start_next_day"):
        local = delivered.astimezone(timezone(timedelta(hours=8)))
        start = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        deadline = start + timedelta(days=rule["days"])
    else:
        deadline = delivered + timedelta(days=rule["days"])
    if instant >= deadline:
        raise BusinessError("INELIGIBLE", f"当前非质量申请不在已发布规则的{rule['days']}天窗口内，请转人工核实。")
    if params.opened and rule.get("opened_requires_review", rule.get("unopened_for_non_quality", False)):
        code = "INELIGIBLE" if rule.get("legacy") else "MANUAL_REVIEW"
        raise BusinessError(code, "拆封查验不能一概视为不能退；当前需人工核验商品完好情况，请转人工。")


async def prepare(params, req):
    order = await call(req, "order.get_order_status", {"order_id": params.order_id})
    rule = await call(req, "knowledge.get_return_rule", {})
    eligible(order, rule, params)
    draft_id, token = "D-" + secrets.token_hex(8), secrets.token_urlsafe(32)
    payload = {"order": order, "reason": params.reason, "opened": params.opened, "quality_issue": params.quality_issue, "policy": rule["policy"], "simulation": True}
    with connect("aftersale") as db:
        db.execute("INSERT INTO drafts VALUES(?,?,?,?,?,?,?)", (draft_id, req.identity.user_id, encode(payload), digest(token), time.time() + 900, "awaiting_confirmation", now()))
    return {"draft_id": draft_id, "confirmation_token": token, "expires_in": 900, "state": "awaiting_confirmation", **payload}


def own_draft(params, identity):
    with connect("aftersale") as db:
        row = db.execute("SELECT * FROM drafts WHERE id=? AND user_id=?", (params.draft_id, identity.user_id)).fetchone()
    if not row or not hmac.compare_digest(row["token_hash"], digest(params.confirmation_token)):
        raise BusinessError("FORBIDDEN", "草稿确认凭据无效或不属于当前用户。")
    return dict(row)


async def confirm(params, req):
    draft = own_draft(params, req.identity)
    with connect("aftersale") as db:
        existing = db.execute("SELECT * FROM returns WHERE draft_id=? AND user_id=?", (draft["id"], req.identity.user_id)).fetchone()
        if existing:
            return {**dict(existing), "payload": json.loads(existing["payload"]), "idempotent": True, "simulation": True}
    if draft["state"] != "awaiting_confirmation" or draft["expires_at"] < time.time():
        raise BusinessError("CONFIRMATION_REQUIRED", "草稿已取消或过期，请重新生成并确认。")
    payload = json.loads(draft["payload"])
    order = await call(req, "order.get_order_status", {"order_id": payload["order"]["id"]})
    rule = await call(req, "knowledge.get_return_rule", {})
    if order != payload["order"] or rule["policy"] != payload["policy"]:
        raise BusinessError("STALE_DRAFT", "订单或政策已经变化，请重新生成草稿并确认。")
    eligible(order, rule, Prepare(order_id=order["id"], reason=payload["reason"], opened=payload["opened"], quality_issue=payload["quality_issue"]))
    with connect("aftersale") as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM drafts WHERE id=?", (draft["id"],)).fetchone()
        existing = db.execute("SELECT * FROM returns WHERE draft_id=?", (draft["id"],)).fetchone()
        if existing:
            return {**dict(existing), "payload": json.loads(existing["payload"]), "idempotent": True, "simulation": True}
        if current["state"] != "awaiting_confirmation" or current["expires_at"] < time.time():
            raise BusinessError("CONFIRMATION_REQUIRED", "确认状态已变化，请刷新。")
        return_id = "RT-" + secrets.token_hex(6).upper()
        db.execute("INSERT INTO returns VALUES(?,?,?,?,?,?)", (return_id, draft["id"], req.identity.user_id, encode(payload), "待审核（模拟）", now()))
        db.execute("UPDATE drafts SET state='submitted' WHERE id=?", (draft["id"],))
    return {"id": return_id, "draft_id": draft["id"], "status": "待审核（模拟）", "payload": payload, "idempotent": False, "simulation": True}


async def cancel(params, req):
    draft = own_draft(params, req.identity)
    with connect("aftersale") as db:
        changed = db.execute("UPDATE drafts SET state='cancelled' WHERE id=? AND state='awaiting_confirmation'", (draft["id"],)).rowcount
    if not changed:
        raise BusinessError("INVALID_STATE", "草稿已处理，无法取消。")
    return {"draft_id": draft["id"], "state": "cancelled"}


async def status(params, req):
    with connect("aftersale") as db:
        row = db.execute("SELECT * FROM returns WHERE id=? AND user_id=?", (params.return_id, req.identity.user_id)).fetchone()
    if not row:
        raise BusinessError("NOT_FOUND", "售后单不存在或不属于当前用户。")
    return {**dict(row), "payload": json.loads(row["payload"]), "simulation": True}


TOOLS = {"prepare_return": (Prepare, prepare, "核验条件并创建待确认草稿"), "confirm_return": (Confirm, confirm, "凭绑定用户的凭据幂等提交模拟申请"), "cancel_return": (Cancel, cancel, "取消未提交草稿"), "get_return_status": (Status, status, "查询当前用户的售后进度")}
