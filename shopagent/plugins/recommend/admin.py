import json
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, model_validator, field_validator
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.storage import connect, now
from shopagent.plugins.knowledge.admin import effective
from shopagent.core.environment import production


class PromotionWrite(StrictModel):
    promotion_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    expected_revision: int = Field(default=0, ge=0, strict=True)
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=40)
    product_ids: list[str] = Field(default_factory=list, max_length=100)
    kind: Literal["percentage", "fixed"] = "percentage"
    rate_percent: int = Field(default=90, ge=1, le=100, strict=True)
    discount_fen: int = Field(default=0, ge=0, le=10000000, strict=True)
    min_amount_fen: int = Field(default=0, ge=0, le=10000000, strict=True)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    simulation: bool = True

    @field_validator("title", "category")
    @classmethod
    def trim(cls, value, info):
        value = value.strip()
        if info.field_name == "title" and not value:
            raise ValueError("活动标题不能为空")
        return value

    @model_validator(mode="after")
    def check(self):
        import re
        if any(not re.fullmatch(r"P\d{3}", pid) for pid in self.product_ids) or len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("商品编号需为P加三位数字且不能重复")
        if self.kind == "fixed" and self.discount_fen <= 0:
            raise ValueError("减免金额必须大于0")
        if self.kind == "percentage" and self.discount_fen:
            raise ValueError("折扣活动不能同时设置减免金额")
        for dt in (self.starts_at, self.ends_at):
            if dt and dt.tzinfo is None:
                raise ValueError("时间必须带时区")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class PromotionAction(StrictModel):
    promotion_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["publish", "disable"]


class Empty(StrictModel):
    pass


def initialize_store():
    with connect("recommend") as db:
        db.executescript("CREATE TABLE IF NOT EXISTS promotions(id TEXT PRIMARY KEY,draft_json TEXT NOT NULL,live_json TEXT,revision INTEGER NOT NULL,updated_at TEXT); CREATE TABLE IF NOT EXISTS promotion_audit(id INTEGER PRIMARY KEY,promotion_id TEXT,actor TEXT,action TEXT,created_at TEXT);")
        # 只在首次迁移时保存原有示例活动。以后停用/过期/重启都不复活。
        flag = db.execute("PRAGMA user_version").fetchone()[0]
        if flag < 2 and not production():
            seed = PromotionWrite(promotion_id="DEMO-EAR-10", title="示例耳机九折", category="耳机", starts_at="2000-01-01T00:00:00+00:00", ends_at="2100-01-01T00:00:00+00:00").model_dump(mode="json")
            db.execute("INSERT OR IGNORE INTO promotions VALUES(?,?,?,?,?)", (seed["promotion_id"], json.dumps(seed, ensure_ascii=False), json.dumps({**seed, "id": seed["promotion_id"], "version": "legacy-1", "stackable": False}, ensure_ascii=False), 1, now()))
            db.execute("PRAGMA user_version=2")


def require_admin(req):
    if req.identity.role != "admin":
        raise BusinessError("FORBIDDEN", "只有商家管理员可以管理优惠。")


def get(db, pid):
    row = db.execute("SELECT * FROM promotions WHERE id=?", (pid,)).fetchone()
    if not row:
        raise BusinessError("NOT_FOUND", "活动不存在。")
    live = json.loads(row["live_json"]) if row["live_json"] else None
    return {"id": pid, "revision": row["revision"], "draft": json.loads(row["draft_json"]), "published": live, "effective": bool(live and effective(live)), "updated_at": row["updated_at"]}


def describe(doc):
    scope = "全部上架商品"
    if doc["category"]:
        scope = "分类：" + doc["category"]
    if doc["product_ids"]:
        scope += "，且仅限编号：" + "、".join(doc["product_ids"])
    rule = f"标价金额的{doc['rate_percent']}%，整数分向下取整" if doc["kind"] == "percentage" else f"商品金额减{doc['discount_fen']/100:.2f}元，最低0元"
    return f"{scope}；商品原价小计满{doc['min_amount_fen']/100:.2f}元适用；{rule}；不叠加其他活动，不含运费。"


def active_promotions():
    with connect("recommend") as db:
        docs = [json.loads(r[0]) for r in db.execute("SELECT live_json FROM promotions WHERE live_json IS NOT NULL")]
    return [{**doc, "description": describe(doc)} for doc in docs if effective(doc) and (not production() or doc.get('simulation') is False)]


async def catalog(params, req):
    require_admin(req)
    with connect("recommend") as db:
        return {"promotions": [get(db, r[0]) for r in db.execute("SELECT id FROM promotions ORDER BY id")]}


async def save(params, req):
    require_admin(req)
    with connect("recommend") as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT revision FROM promotions WHERE id=?", (params.promotion_id,)).fetchone()
        revision = row[0] if row else 0
        if revision != params.expected_revision:
            raise BusinessError("REVISION_CONFLICT", "活动已修改或编号已存在，请刷新后编辑。")
        db.execute("INSERT INTO promotions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET draft_json=excluded.draft_json,revision=excluded.revision,updated_at=excluded.updated_at", (params.promotion_id, params.model_dump_json(), None, revision+1, now()))
        db.execute("INSERT INTO promotion_audit(promotion_id,actor,action,created_at) VALUES(?,?,?,?)", (params.promotion_id, req.identity.user_id, "save_draft", now()))
        return get(db, params.promotion_id)


async def action(params, req):
    require_admin(req)
    with connect("recommend") as db:
        db.execute("BEGIN IMMEDIATE")
        value = get(db, params.promotion_id)
        if value["revision"] != params.expected_revision:
            raise BusinessError("REVISION_CONFLICT", "活动已修改，请刷新后操作。")
        live = None
        if params.action == "publish":
            draft = PromotionWrite.model_validate(value["draft"])
            if production() and draft.simulation:
                raise BusinessError('SAMPLE_NOT_PUBLISHABLE','示例优惠不能在生产模式发布，请核实真实规则并取消示例标记。')
            if not draft.starts_at or not draft.ends_at:
                raise BusinessError("DATES_REQUIRED", "发布优惠必须填写开始和结束时间。")
            if draft.ends_at <= datetime.now(timezone.utc):
                raise BusinessError("EXPIRED", "不能发布已过期活动。")
            live = {**draft.model_dump(mode="json"), "id": draft.promotion_id, "version": f"v{value['revision']+1}", "stackable": False}
        db.execute("UPDATE promotions SET live_json=?,revision=revision+1,updated_at=? WHERE id=?", (json.dumps(live, ensure_ascii=False) if live else None, now(), params.promotion_id))
        db.execute("INSERT INTO promotion_audit(promotion_id,actor,action,created_at) VALUES(?,?,?,?)", (params.promotion_id, req.identity.user_id, params.action, now()))
        return get(db, params.promotion_id)


ADMIN_TOOLS = {"admin_promotions": (Empty, catalog, "管理员查看优惠草稿和有效版本"), "admin_save_promotion": (PromotionWrite, save, "管理员保存活动草稿，不影响已发布版本"), "admin_promotion_action": (PromotionAction, action, "管理员发布或停用活动，发布需要有效期")}
