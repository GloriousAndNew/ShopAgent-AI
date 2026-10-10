import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from conftest import invoke


def states(client):
    return {p["id"]:p for p in client.get("/admin/plugins").json()}


def test_stop_logistics_keeps_other_modules(clients):
    admin=clients["admin"]
    before=admin.get("/health").json()
    assert admin.post("/admin/plugins/logistics/disable",json={}).status_code == 200
    assert invoke(clients["alice"],"logistics.track_logistics",{"order_id":"O1001"})["error_code"] == "PLUGIN_UNAVAILABLE"
    assert invoke(clients["alice"],"order.get_order_status",{"order_id":"O1001"})["status"] == "ok"
    assert invoke(clients["alice"],"product.search_products",{"keyword":"耳机"})["status"] == "ok"
    assert admin.get("/health").json()["generation"] > before["generation"]
    assert admin.post("/admin/plugins/logistics/enable",json={}).status_code == 200


def test_restore_logistics(clients):
    assert invoke(clients["alice"],"logistics.track_logistics",{"order_id":"O1001"})["status"] == "ok"


def test_dependencies_prevent_stop(clients):
    r=clients["admin"].post("/admin/plugins/order/disable",json={})
    assert r.status_code == 409
    assert r.json()["error_code"] == "DEPENDENTS_ACTIVE"


def test_cascade_and_restore(clients):
    admin=clients["admin"]
    assert admin.post("/admin/plugins/knowledge/disable",json={"cascade":True}).status_code == 200
    assert states(admin)["aftersale"]["state"] == "stopped"
    assert admin.post("/admin/plugins/aftersale/enable",json={}).status_code == 409
    assert admin.post("/admin/plugins/knowledge/enable",json={}).status_code == 200
    assert admin.post("/admin/plugins/aftersale/enable",json={}).status_code == 200


def test_invalid_version_preserves_old(clients):
    admin=clients["admin"]; old=states(admin)["product"]
    assert admin.post("/admin/plugins/product/switch-version",json={"version":"2.0.0"}).status_code == 409
    current=states(admin)["product"]
    assert current["pid"] == old["pid"] and current["version"] == old["version"]
    assert invoke(clients["alice"],"product.search_products",{})["status"] == "ok"


def test_version_switch_new_process_and_rollback(clients):
    admin=clients["admin"]; old=states(admin)["product"]
    assert admin.post("/admin/plugins/product/switch-version",json={"version":"1.1.0"}).status_code == 200
    new=states(admin)["product"]
    assert new["pid"] != old["pid"]
    result=invoke(clients["alice"],"product.search_products",{})
    assert result["plugin_version"] == "1.1.0" and result["data"]["catalog_build"] == "1.1.0"
    assert admin.post("/admin/plugins/product/switch-version",json={"version":"1.0.0"}).status_code == 200


def test_dynamic_echo_registration(clients):
    admin=clients["admin"]
    assert admin.post("/admin/plugins/demo/enable",json={}).status_code == 200
    result=invoke(admin,"demo.echo",{"text":"新插件无需改宿主路由"})
    assert result["data"]["text"] == "新插件无需改宿主路由"


def test_drain_inflight_request(clients):
    admin=clients["admin"]
    with ThreadPoolExecutor(2) as executor:
        pending=executor.submit(invoke,admin,"demo.echo",{"text":"旧请求","delay":1.5})
        for _ in range(30):
            if states(admin)["demo"]["inflight"]:
                break
            time.sleep(.05)
        disable=executor.submit(admin.post,"/admin/plugins/demo/disable",json={})
        for _ in range(30):
            if states(admin)["demo"]["state"] == "draining":
                break
            time.sleep(.05)
        assert invoke(admin,"demo.echo",{"text":"新请求"})["error_code"] == "PLUGIN_UNAVAILABLE"
        assert pending.result()["data"]["text"] == "旧请求"
        assert disable.result().status_code == 200


def test_crash_isolation_and_recovery(clients):
    admin=clients["admin"]; pid=states(admin)["logistics"]["pid"]
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if states(admin)["logistics"]["state"] == "failed":
            break
        time.sleep(.1)
    assert invoke(clients["alice"],"logistics.track_logistics",{"order_id":"O1001"})["error_code"] == "PLUGIN_UNAVAILABLE"
    assert invoke(clients["alice"],"product.search_products",{})["status"] == "ok"
    assert admin.post("/admin/plugins/logistics/enable",json={}).status_code == 200


def test_restart_respects_disabled_state(clients, server):
    admin=clients["admin"]
    assert admin.post("/admin/plugins/logistics/disable",json={}).status_code == 200
    server.stop(); server.start()
    assert states(admin)["logistics"]["state"] == "stopped"
    assert invoke(clients["alice"],"order.get_order_status",{"order_id":"O1001"})["status"] == "ok"
    assert admin.post("/admin/plugins/logistics/enable",json={}).status_code == 200
