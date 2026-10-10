import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from conftest import invoke


def product(pid="P901", **changes):
    return {"product_id": pid, "name": "后台验收商品", "category": "测试配件", "price_fen": 12345,
            "status": "draft", "simulation": True, "skus": [{"color": "黑色", "stock": 4}, {"color": "白色", "stock": 0}], **changes}


def test_admin_create_edit_publish_and_archive(clients, server):
    admin, buyer = clients["admin"], clients["alice"]
    value = admin.post("/admin/products/save", json=product()).json()
    assert value["revision"] == 1 and value["status"] == "draft"
    assert invoke(buyer, "product.get_product_detail", {"product_id": "P901"})["error_code"] == "NOT_FOUND"
    assert invoke(buyer, "product.check_inventory", {"product_id": "P901"})["error_code"] == "NOT_FOUND"
    assert not invoke(buyer, "product.search_products", {"keyword": "后台验收"})["data"]["products"]
    edited = admin.post("/admin/products/save", json=product(expected_revision=1, price_fen=9999, specification="USB有线", unknown_fields=["防水等级"], skus=[{"color": "黑色", "stock": 8}, {"color": "白色", "stock": 0}])).json()
    assert edited["revision"] == 2
    r = admin.post("/admin/products/status", json={"product_id": "P901", "expected_revision": 2, "status": "published"})
    assert r.status_code == 200
    visible = invoke(buyer, "product.get_product_detail", {"product_id": "P901"})["data"]
    assert visible["price_fen"] == 9999 and visible["unknown_fields"] == ["防水等级"]
    assert invoke(buyer, "product.check_inventory", {"product_id": "P901", "color": "黑色"})["data"]["stock"] == 8
    assert invoke(buyer, "product.check_inventory", {"product_id": "P901", "color": "白色"})["data"]["stock"] == 0
    assert admin.post("/admin/products/status", json={"product_id": "P901", "expected_revision": 3, "status": "archived"}).status_code == 200
    assert invoke(buyer, "product.get_product_detail", {"product_id": "P901"})["error_code"] == "NOT_FOUND"
    assert invoke(buyer, "product.compare_prices", {"product_ids": ["P901", "P001"]})["error_code"] == "NOT_FOUND"
    with sqlite3.connect(server.directory / "product.db") as db:
        records = db.execute("SELECT actor,action FROM product_audit WHERE product_id='P901' ORDER BY id").fetchall()
        assert records == [("admin", "create"), ("admin", "edit"), ("admin", "status"), ("admin", "status")]


def test_buyer_cannot_bypass_admin_checks_with_generic_tool(clients):
    buyer = clients["alice"]
    assert buyer.get("/admin/products").status_code == 403
    assert buyer.get("/admin/products/starter").status_code == 403
    assert buyer.post("/admin/products/save", json=product("P902")).status_code == 403
    for tool, params in [("admin_catalog", {}), ("admin_save_product", product("P902")), ("admin_preview_import", {"products": [product("P902")]}), ("admin_commit_import", {"products": [product("P902")]}), ("admin_set_status", {"product_id":"P001","expected_revision":1,"status":"archived"})]:
        assert invoke(buyer, "product."+tool, params)["error_code"] == "FORBIDDEN"


@pytest.mark.parametrize("changes", [{"price_fen": -1}, {"price_fen": 1.2}, {"price_fen": True}, {"name": "   "}, {"skus": []}, {"skus": [{"color":"黑色","stock":-1}]}, {"skus": [{"color":"黑色","stock":1}, {"color":" 黑色 ","stock":2}]}])
def test_invalid_product_not_written(clients, changes):
    assert clients["admin"].post("/admin/products/save", json=product("P902", **changes)).status_code == 422
    assert not clients["admin"].get("/admin/products?keyword=P902").json()["products"]


def test_concurrent_edits_do_not_overwrite_each_other(clients):
    admin = clients["admin"]
    assert admin.post("/admin/products/save", json=product("P903")).status_code == 200
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda price: admin.post("/admin/products/save", json=product("P903", expected_revision=1,price_fen=price)), [11100,22200]))
    assert sorted(r.status_code for r in results)==[200,409]
    current=admin.get("/admin/products?keyword=P903").json()["products"][0]
    assert current["revision"] == 2 and current["price_fen"] in (11100,22200)


def test_import_preview_atomic_commit_and_duplicates(clients):
    admin=clients["admin"]
    invalid={"products":[product("P910"),product("P911",price_fen=-1)]}
    preview=admin.post("/admin/products/import/preview",json=invalid).json()
    assert not preview["valid"] and preview["errors"][0]["row"] == 2
    assert not admin.get("/admin/products?keyword=P910").json()["products"]
    assert admin.post("/admin/products/import/commit",json=invalid).status_code==409
    assert not admin.get("/admin/products?keyword=P910").json()["products"]
    duplicate={"products":[product("P910"),product("P910")]}
    assert not admin.post("/admin/products/import/preview",json=duplicate).json()["valid"]
    batch={"products":[product("P910",status="published"),product("P911")]}
    assert admin.post("/admin/products/import/preview",json=batch).json()["valid"]
    result=admin.post("/admin/products/import/commit",json=batch).json()
    assert result["imported"]==2
    assert clients["admin"].get("/admin/products?keyword=P910").json()["products"][0]["status"]=="draft"
    assert admin.post("/admin/products/import/commit",json=batch).status_code==409
    assert not admin.post("/admin/products/import/preview",json=batch).json()["valid"]


def test_starter_pack_import_is_draft_and_does_not_replace_existing(clients):
    admin=clients["admin"]
    before=invoke(clients["alice"],"product.get_product_detail",{"product_id":"P001"})["data"]
    payload=admin.get("/admin/products/starter").json()
    body={"products":payload["products"]}
    assert len(body["products"])==10
    assert admin.post("/admin/products/import/preview",json=body).json()["valid"]
    assert admin.post("/admin/products/import/commit",json=body).json()["imported"]==10
    assert invoke(clients["alice"],"product.get_product_detail",{"product_id":"P101"})["error_code"]=="NOT_FOUND"
    after=invoke(clients["alice"],"product.get_product_detail",{"product_id":"P001"})["data"]
    assert before==after


def test_product_schema_upgrade_preserves_legacy_data(tmp_path,monkeypatch):
    from shopagent import storage
    from shopagent.plugins.product import service
    monkeypatch.setattr(storage,"DATA",tmp_path)
    with sqlite3.connect(tmp_path/'product.db') as db:
        db.executescript("CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,category TEXT,price_fen INTEGER,noise_cancel INTEGER,description TEXT); CREATE TABLE skus(id TEXT PRIMARY KEY,product_id TEXT REFERENCES products(id),color TEXT,stock INTEGER,updated_at TEXT);")
        db.execute("INSERT INTO products VALUES(?,?,?,?,?,?)",('P001','原商品','耳机',12300,1,'保留说明'))
        db.execute("INSERT INTO skus VALUES(?,?,?,?,?)",('P001-黑色','P001','黑色',7,'old-time'))
    service.initialize()
    service.initialize()
    with storage.connect('product') as db:
        value=dict(db.execute("SELECT * FROM products WHERE id='P001'").fetchone())
        assert value['name']=='原商品' and value['price_fen']==12300 and value['status']=='published'
        assert db.execute("SELECT stock FROM skus WHERE id='P001-黑色'").fetchone()[0]==7
        assert db.execute('SELECT count(*) FROM products').fetchone()[0]==1


def test_admin_works_after_hotplug_and_preserves_data(clients):
    admin=clients['admin']
    before=admin.get('/admin/products?keyword=P903').json()['products'][0]
    states={v['id']:v for v in admin.get('/admin/plugins').json()}
    original=states['product']['version']
    candidate='1.1.0' if original=='1.0.0' else '1.0.0'
    try:
        assert admin.post('/admin/plugins/product/switch-version',json={'version':candidate}).status_code==200
        after=admin.get('/admin/products?keyword=P903').json()['products'][0]
        assert after==before
        assert admin.post('/admin/plugins/recommend/disable',json={}).status_code==200
        assert admin.post('/admin/plugins/product/disable',json={}).status_code==200
        assert admin.get('/admin/products').status_code==409
        assert invoke(clients['alice'],'order.get_order_status',{'order_id':'O1001'})['status']=='ok'
    finally:
        admin.post('/admin/plugins/product/enable',json={}).raise_for_status()
        admin.post('/admin/plugins/recommend/enable',json={}).raise_for_status()
        admin.post('/admin/plugins/product/switch-version',json={'version':original}).raise_for_status()
    assert admin.get('/admin/products?keyword=P903').json()['products'][0]==before
