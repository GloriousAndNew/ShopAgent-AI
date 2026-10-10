import json
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, model_validator, field_validator
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.storage import connect, now


class ReturnRule(StrictModel):
    days: int = Field(default=7, ge=1, le=30, strict=True)
    start_next_day: bool = True
    opened_requires_review: bool = True
    quality_manual_review: bool = True


class PolicyWrite(StrictModel):
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    expected_revision: int = Field(default=0, ge=0, strict=True)
    title: str = Field(min_length=1, max_length=100)
    tags: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=10000)
    kind: Literal["general", "return"] = "general"
    return_rule: ReturnRule | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    simulation: bool = True

    @field_validator("title", "tags", "text")
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @model_validator(mode="after")
    def consistency(self):
        if (self.policy_id == "return") != (self.kind == "return"):
            raise ValueError("退货规则只能使用编号return和退货类型")
        if self.kind == "return" and not self.return_rule:
            raise ValueError("退货政策必须填写结构化规则")
        if self.kind == "general" and self.return_rule:
            raise ValueError("普通政策不能附加退货规则")
        for dt in (self.starts_at, self.ends_at):
            if dt and dt.tzinfo is None:
                raise ValueError("时间必须带时区")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class PolicyID(StrictModel):
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")


class PolicyAction(PolicyID):
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["publish", "disable"]


class Empty(StrictModel):
    pass


def initialize_store(legacy):
    with connect("knowledge") as db:
        db.executescript("CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY,draft_json TEXT NOT NULL,live_json TEXT,revision INTEGER NOT NULL,updated_at TEXT); CREATE TABLE IF NOT EXISTS policy_versions(id INTEGER PRIMARY KEY,policy_id TEXT,version TEXT,document_json TEXT,actor TEXT,created_at TEXT); CREATE TABLE IF NOT EXISTS policy_audit(id INTEGER PRIMARY KEY,policy_id TEXT,actor TEXT,action TEXT,created_at TEXT);")
        if db.execute("SELECT count(*) FROM policies").fetchone()[0]:
            return
        for doc in legacy:
            if doc['id'] == 'promotion' and doc['text'] == '模拟店铺未配置促销，页面价格为演示商品价，不自动使用不存在的优惠券。':
                doc = {**doc, 'version': 'migration-2', 'source': 'knowledge/promotion/migration-2',
                       'text': '优惠以当前已发布且在有效期内的活动查询结果为准；需核实商品范围和金额门槛，不能把活动试算当作订单成交价，不自动使用不存在的优惠券。'}
            draft = {"policy_id": doc["id"], "expected_revision": 0, "title": doc["title"], "tags": doc["tags"], "text": doc["text"], "kind": "return" if doc["id"] == "return" else "general", "return_rule": ReturnRule().model_dump() if doc["id"] == "return" else None, "starts_at": None, "ends_at": None, "simulation": True}
            if doc["id"] == "return":
                draft["text"] = "退货申请需核实本人订单和商品情况。必要的拆封查验不能一概作为拒退理由；拆封商品的完好情况和质量问题由人工分别核验。这里只生成待确认草稿，不执行真实退款。"
            # 旧规则仅作为已有示例迁移；管理页保存的新规则不再一概拆封拒退。
            live = {**doc, "starts_at": None, "ends_at": None, "simulation": True}
            if doc["id"] == "return":
                live["rule"] = {"days": 7, "start_next_day": False, "unopened_for_non_quality": True, "opened_requires_review": True, "quality_manual_review": False, "legacy": True}
            db.execute("INSERT INTO policies VALUES(?,?,?,?,?)", (doc["id"], json.dumps(draft, ensure_ascii=False), json.dumps(live, ensure_ascii=False) if doc.get("active", True) else None, 1, now()))
            if doc.get("active", True):
                db.execute("INSERT INTO policy_versions(policy_id,version,document_json,actor,created_at) VALUES(?,?,?,?,?)", (doc["id"], doc["version"], json.dumps(live, ensure_ascii=False), "migration", now()))


def require_admin(req):
    if req.identity.role != "admin":
        raise BusinessError("FORBIDDEN", "只有商家管理员可以管理政策。")


def effective(doc, instant=None):
    instant = instant or datetime.now(timezone.utc)
    return (not doc.get("starts_at") or datetime.fromisoformat(doc["starts_at"]) <= instant) and (not doc.get("ends_at") or instant < datetime.fromisoformat(doc["ends_at"]))


def get(db, pid):
    row = db.execute("SELECT * FROM policies WHERE id=?", (pid,)).fetchone()
    if not row:
        raise BusinessError("NOT_FOUND", "政策不存在。")
    live = json.loads(row["live_json"]) if row["live_json"] else None
    return {"id": row["id"], "revision": row["revision"], "draft": json.loads(row["draft_json"]), "published": live,
            "effective": bool(live and effective(live)), "updated_at": row["updated_at"]}


async def catalog(params, req):
    require_admin(req)
    with connect("knowledge") as db:
        return {"policies": [get(db, r["id"]) for r in db.execute("SELECT id FROM policies ORDER BY id")]}


async def save(params, req):
    require_admin(req)
    with connect("knowledge") as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT revision FROM policies WHERE id=?", (params.policy_id,)).fetchone()
        revision = row["revision"] if row else 0
        if revision != params.expected_revision:
            raise BusinessError("REVISION_CONFLICT", "政策已修改或编号已存在，请刷新后再编辑。")
        body = params.model_dump(mode="json")
        db.execute("INSERT INTO policies VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET draft_json=excluded.draft_json,revision=excluded.revision,updated_at=excluded.updated_at", (params.policy_id, json.dumps(body, ensure_ascii=False), None, revision+1, now()))
        db.execute("INSERT INTO policy_audit(policy_id,actor,action,created_at) VALUES(?,?,?,?)", (params.policy_id, req.identity.user_id, "save_draft", now()))
        return get(db, params.policy_id)


async def action(params, req):
    require_admin(req)
    with connect("knowledge") as db:
        db.execute("BEGIN IMMEDIATE")
        value = get(db, params.policy_id)
        if value["revision"] != params.expected_revision:
            raise BusinessError("REVISION_CONFLICT", "政策已修改，请刷新后再操作。")
        doc = None
        if params.action == "publish":
            draft = PolicyWrite.model_validate(value["draft"])
            from shopagent.core.environment import production
            if production() and draft.simulation:
                raise BusinessError('SAMPLE_NOT_PUBLISHABLE','示例政策不能在生产模式发布，请核实真实政策并取消示例标记。')
            if draft.ends_at and draft.ends_at <= datetime.now(timezone.utc):
                raise BusinessError("EXPIRED", "不能发布已经过期的政策。")
            version = f"v{value['revision']+1}"
            text = draft.text
            rule = None
            if draft.kind == "return":
                rule = {**draft.return_rule.model_dump(), "unopened_for_non_quality": False, "legacy": False}
                text += f"\n\n系统执行规则：非质量申请窗口{rule['days']}天，{'签收次日起算' if rule['start_next_day'] else '签收时刻起算'}；"
                text += "拆封查验不一概拒退，" + ("拆封申请转人工核实完好情况；" if rule['opened_requires_review'] else "生成草稿后由商家核实完好情况；")
                text += "质量问题" + ("转人工单独核验，不套用无理由期限。" if rule['quality_manual_review'] else "可生成待审核草稿，不套用无理由期限。")
            doc = {"id": draft.policy_id, "version": version, "title": draft.title, "tags": draft.tags, "text": text,
                   "source": f"knowledge/{draft.policy_id}/{version}", "simulation": draft.simulation,
                   "starts_at": draft.starts_at.isoformat() if draft.starts_at else None, "ends_at": draft.ends_at.isoformat() if draft.ends_at else None, "rule": rule}
            db.execute("INSERT INTO policy_versions(policy_id,version,document_json,actor,created_at) VALUES(?,?,?,?,?)", (params.policy_id, version, json.dumps(doc, ensure_ascii=False), req.identity.user_id, now()))
        db.execute("UPDATE policies SET live_json=?,revision=revision+1,updated_at=? WHERE id=?", (json.dumps(doc, ensure_ascii=False) if doc else None, now(), params.policy_id))
        db.execute("INSERT INTO policy_audit(policy_id,actor,action,created_at) VALUES(?,?,?,?)", (params.policy_id, req.identity.user_id, params.action, now()))
        return get(db, params.policy_id)


async def history(params, req):
    require_admin(req)
    with connect("knowledge") as db:
        return {"versions": [{"version": r["version"], "created_at": r["created_at"], "document": json.loads(r["document_json"])} for r in db.execute("SELECT * FROM policy_versions WHERE policy_id=? ORDER BY id DESC LIMIT 50", (params.policy_id,))]}


ADMIN_TOOLS = {"admin_catalog": (Empty, catalog, "管理员查看政策草稿与发布版本"), "admin_save": (PolicyWrite, save, "管理员保存政策草稿，不影响已发布内容"), "admin_action": (PolicyAction, action, "管理员发布或停用政策"), "admin_history": (PolicyID, history, "管理员查看不可变的发布版本记录")}
