"""导购数据工具。示例优惠明确标注；用户画像只取当前账户。"""
from pydantic import Field
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.plugins.client import call
from shopagent.plugins.product.service import Search, Detail
from shopagent.storage import connect, now
from shopagent.plugins.recommend.admin import initialize_store, active_promotions, ADMIN_TOOLS


async def invoke(tool, params, req):
    return await call(req, tool, params)


class Empty(StrictModel):
    pass


class Discount(Detail):
    quantity: int = Field(default=1, ge=1, le=20)
    promotion_id: str = Field(default="DEMO-EAR-10", max_length=40)


class Recommendations(Search):
    limit: int = Field(default=2, ge=1, le=3)


class Record(Detail):
    note: str = Field(default="", max_length=200)


def initialize():
    with connect("recommend") as db:
        db.executescript("CREATE TABLE IF NOT EXISTS shopping_records(id INTEGER PRIMARY KEY,user_id TEXT,product_id TEXT,note TEXT,created_at TEXT);")
    initialize_store()


async def profile(params, req):
    with connect("recommend") as db:
        rows = db.execute("SELECT product_id,note,created_at FROM shopping_records WHERE user_id=? ORDER BY id DESC LIMIT 10", (req.identity.user_id,)).fetchall()
    return {"user_id": req.identity.user_id, "membership": "普通会员（示例）", "preferences": [], "records": [dict(r) for r in rows], "notice": "未收集偏好时不推断用户兴趣。"}


async def recommendations(params, req):
    data = await invoke("product.search_products", params.model_dump(exclude={"limit"}), req)
    return {**data, "products": data["products"][:params.limit], "method": "预算与商品条件筛选，非训练的个性化推荐", "simulation": True}


async def promotions(params, req):
    values=active_promotions()
    return {"promotions": values, "simulation": any(d.get('simulation',True) for d in values), "notice": "仅列出当前已发布且在有效期内的活动；试算不用于订单结算。"}


async def discount(params, req):
    product = await invoke("product.get_product_detail", {"product_id": params.product_id}, req)
    with connect("recommend") as db:
        row = db.execute("SELECT live_json FROM promotions WHERE id=?", (params.promotion_id,)).fetchone()
    if not row or not row[0]:
        raise BusinessError("NOT_FOUND", "未查询到该优惠，不能计算。")
    import json
    from shopagent.plugins.knowledge.admin import effective
    activity = json.loads(row[0])
    from shopagent.core.environment import production
    if production() and activity.get('simulation') is not False:
        raise BusinessError('NOT_FOUND','正式入口不提供示例优惠。')
    if not effective(activity):
        raise BusinessError("PROMOTION_INACTIVE", "活动尚未开始或已经过期，不能使用。")
    total = product["price_fen"] * params.quantity
    eligible = (not activity["category"] or product["category"] == activity["category"]) and (not activity["product_ids"] or params.product_id in activity["product_ids"]) and total >= activity["min_amount_fen"]
    payable = total
    if eligible:
        payable = total * activity["rate_percent"] // 100 if activity["kind"] == "percentage" else max(0, total-activity["discount_fen"])
    return {"product_id": params.product_id, "quantity": params.quantity, "promotion_id": params.promotion_id, "promotion_version": activity["version"], "original_fen": total, "payable_fen": payable, "discount_fen": total-payable, "eligible": eligible, "simulation": activity["simulation"] or product.get("simulation", True), "notice": "查询当前活动规则计算的试算金额，不是订单成交价，不含运费。"}


async def record(params, req):
    await invoke("product.get_product_detail", {"product_id": params.product_id}, req)
    with connect("recommend") as db:
        db.execute("INSERT INTO shopping_records(user_id,product_id,note,created_at) VALUES(?,?,?,?)", (req.identity.user_id, params.product_id, params.note, now()))
    return {"saved": True, "product_id": params.product_id}


TOOLS = {
    "get_user_profile": (Empty, profile, "读取当前登录顾客的会员资料和已保存选购记录"),
    "get_recommendations": (Recommendations, recommendations, "按预算和条件筛选最多3款推荐候选；库存交给商品 Agent 核实"),
    "query_promotions": (Empty, promotions, "查询示例优惠活动及适用范围"),
    "calculate_discount": (Discount, discount, "从商品服务获取真实示例标价，以整数分计算优惠，不接受模型自报价格"),
    "save_shopping_record": (Record, record, "保存当前用户选购记录，不产生订单或支付"),
}
TOOLS.update(ADMIN_TOOLS)
