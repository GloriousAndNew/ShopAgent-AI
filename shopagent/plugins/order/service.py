from pydantic import Field
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.storage import connect, encode, now


class OrderQuery(StrictModel):
    order_id: str = Field(pattern=r"^O\d{4}$")


class ListOrders(StrictModel):
    pass


def initialize():
    with connect("order") as db:
        db.executescript("CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id TEXT,status TEXT,amount_fen INTEGER,product_id TEXT,product_name TEXT,quantity INTEGER,delivered_at TEXT,created_at TEXT); PRAGMA user_version=1;")
        from shopagent.core.environment import production
        if production() or db.execute("SELECT count(*) FROM orders").fetchone()[0]:
            return
        for owner, start in (("alice", 1001), ("bob", 2001)):
            for i, status in enumerate(("运输中", "已签收", "待发货", "已取消", "已签收")):
                db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)", (f"O{start+i}", owner, status, 18900, "P001", "轻音降噪耳机", 1, now() if status == "已签收" else None, now()))


async def get_order(params, req):
    with connect("order") as db:
        row = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (params.order_id, req.identity.user_id)).fetchone()
        if not row:
            raise BusinessError("NOT_FOUND", "订单不存在或不属于当前用户，无法查看。")
        return dict(row)


async def list_orders(params, req):
    with connect("order") as db:
        return {"orders": [dict(r) for r in db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id", (req.identity.user_id,))]}


TOOLS = {"get_order_status": (OrderQuery, get_order, "查询属于当前用户的订单"), "list_orders": (ListOrders, list_orders, "列出当前用户订单")}
