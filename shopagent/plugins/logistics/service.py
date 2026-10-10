import asyncio
from pydantic import Field
from shopagent.contracts.models import StrictModel
from shopagent.plugins.client import call
from shopagent.storage import connect, now


class Track(StrictModel):
    order_id: str = Field(pattern=r"^O\d{4}$")


def initialize():
    with connect("logistics") as db:
        db.executescript("CREATE TABLE IF NOT EXISTS tracks(order_id TEXT PRIMARY KEY,location TEXT,updated_at TEXT); PRAGMA user_version=1;")
        from shopagent.core.environment import production
        if production():
            return
        for start in (1001, 2001):
            for i in range(5):
                db.execute("INSERT OR IGNORE INTO tracks VALUES(?,?,?)", (f"O{start+i}", "上海转运中心" if i == 0 else "目的地服务站", now()))


async def track(params, req):
    order = await call(req, "order.get_order_status", {"order_id": params.order_id})
    with connect("logistics") as db:
        row = db.execute("SELECT * FROM tracks WHERE order_id=?", (params.order_id,)).fetchone()
    if order["status"] in ("待发货", "已取消"):
        return {"order_id": params.order_id, "status": order["status"], "events": [], "eta": None}
    return {"order_id": params.order_id, "status": order["status"], "events": [{"location": row["location"], "time": row["updated_at"]}] if row else [], "eta": None, "simulation": True}


TOOLS = {"track_logistics": (Track, track, "核验归属后查询模拟物流，未知到达时间不猜测")}
