import json
from pydantic import Field
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.storage import connect, now
from shopagent.core.environment import production


class Search(StrictModel):
    keyword: str = Field(default="", max_length=60)
    max_price_fen: int = Field(default=10000000, ge=0, le=10000000)
    noise_cancel: bool = False


class Detail(StrictModel):
    product_id: str = Field(pattern=r"^P\d{3}$")


class Inventory(Detail):
    color: str = Field(default="黑色", max_length=20)


class Compare(StrictModel):
    product_ids: list[str] = Field(min_length=2, max_length=5)


async def compare(params, req):
    products = [await detail(Detail(product_id=pid), req) for pid in params.product_ids]
    return {"products": products, "cheapest_id": min(products, key=lambda p: p["price_fen"])["id"], "notice": "只比较本店示例商品标价，不查询外部平台价格。"}


def initialize():
    names = [("轻音降噪耳机", "耳机", 18900), ("云声降噪耳机", "耳机", 15900), ("专业降噪耳机", "耳机", 39900),
             ("便携蓝牙耳机", "耳机", 8900), ("舒适头戴耳机", "耳机", 24900), ("无线鼠标", "鼠标", 9900),
             ("机械键盘", "键盘", 29900), ("耳机收纳包", "收纳包", 7900), ("保温杯", "水杯", 12900), ("羊毛围巾", "围巾", 39900)]
    with connect("product") as db:
        db.executescript("CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,name TEXT,category TEXT,price_fen INTEGER,noise_cancel INTEGER,description TEXT); CREATE TABLE IF NOT EXISTS skus(id TEXT PRIMARY KEY,product_id TEXT REFERENCES products(id),color TEXT,stock INTEGER,updated_at TEXT); PRAGMA user_version=1;")
        columns = {r[1] for r in db.execute("PRAGMA table_info(products)")}
        additions = {"status": "TEXT NOT NULL DEFAULT 'published'", "revision": "INTEGER NOT NULL DEFAULT 1",
                     "simulation": "INTEGER NOT NULL DEFAULT 1", "specification": "TEXT NOT NULL DEFAULT ''",
                     "brand": "TEXT NOT NULL DEFAULT ''", "unknown_fields": "TEXT NOT NULL DEFAULT '[]'",
                     "warranty_months": "INTEGER", "updated_at": "TEXT NOT NULL DEFAULT ''"}
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE products ADD COLUMN {name} {definition}")
        db.executescript("CREATE TABLE IF NOT EXISTS product_audit(id INTEGER PRIMARY KEY,product_id TEXT,actor TEXT,action TEXT,before_json TEXT,after_json TEXT,created_at TEXT); PRAGMA user_version=2;")
        if production() or db.execute("SELECT count(*) FROM products").fetchone()[0]:
            return
        for i in range(30):
            name, category, price = names[i % 10]
            pid = f"P{i+1:03}"
            if i >= 10:
                name += f" · {i // 10 + 1}代"
                price += (i // 10) * 2000
            db.execute("INSERT INTO products(id,name,category,price_fen,noise_cancel,description,updated_at) VALUES(?,?,?,?,?,?,?)", (pid, name, category, price, int(i % 10 < 3), "模拟店铺商品；参数仅用于演示。", now()))
            for color in ("黑色", "白色"):
                db.execute("INSERT INTO skus VALUES(?,?,?,?,?)", (f"{pid}-{color}", pid, color, 0 if color == "黑色" and i % 7 == 1 else 12 + i, now()))


async def search(params, req):
    with connect("product") as db:
        rows = db.execute("SELECT * FROM products WHERE status='published' AND price_fen<=? AND (name LIKE ? OR category LIKE ?) AND (?=0 OR noise_cancel=1) ORDER BY price_fen DESC,id", (params.max_price_fen, f"%{params.keyword}%", f"%{params.keyword}%", int(params.noise_cancel))).fetchall()
        products=[serialize(row) for row in rows if not production() or not row['simulation']]
        return {"products": products, "count": len(products), "updated_at": now()}


def serialize(row):
    value = dict(row)
    value["unknown_fields"] = json.loads(value["unknown_fields"])
    value["noise_cancel"] = bool(value["noise_cancel"])
    value["simulation"] = bool(value["simulation"])
    return value


async def detail(params, req):
    with connect("product") as db:
        row = db.execute("SELECT * FROM products WHERE id=? AND status='published'", (params.product_id,)).fetchone()
        if not row or (production() and row['simulation']):
            raise BusinessError("NOT_FOUND", "没有找到这个商品。")
        result = serialize(row)
        result["skus"] = [dict(r) for r in db.execute("SELECT * FROM skus WHERE product_id=?", (params.product_id,))]
        return result


async def inventory(params, req):
    with connect("product") as db:
        row = db.execute("SELECT s.*,p.simulation FROM skus s JOIN products p ON p.id=s.product_id WHERE p.status='published' AND s.product_id=? AND s.color=?", (params.product_id, params.color)).fetchone()
        if not row or (production() and row['simulation']):
            raise BusinessError("NOT_FOUND", "没有这个颜色的 SKU，请重新选择。")
        return dict(row)


TOOLS = {"search_products": (Search, search, "按预算、关键词、降噪要求搜索商品"), "get_product_detail": (Detail, detail, "查询商品及规格"), "check_inventory": (Inventory, inventory, "查询指定颜色 SKU 库存")}
TOOLS["compare_prices"] = (Compare, compare, "比较2至5个本店商品的标价与规格")
from shopagent.plugins.product.admin import ADMIN_TOOLS
TOOLS.update(ADMIN_TOOLS)
