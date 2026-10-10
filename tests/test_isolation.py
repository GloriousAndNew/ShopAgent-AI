import httpx
from conftest import invoke, prepare


def test_login_required(server):
    r=httpx.post(server.url+"/chat",json={"message":"我的订单"},trust_env=False)
    assert r.status_code == 401


def test_bad_password(server):
    r=httpx.post(server.url+"/auth/login",json={"username":"alice","password":"wrong"},trust_env=False)
    assert r.status_code == 401


def test_alice_cannot_read_bob(clients):
    assert invoke(clients["alice"],"order.get_order_status",{"order_id":"O2001"})["error_code"] == "NOT_FOUND"


def test_bob_cannot_read_alice(clients):
    assert invoke(clients["bob"],"logistics.track_logistics",{"order_id":"O1001"})["error_code"] == "NOT_FOUND"


def test_identity_cannot_be_spoofed(clients):
    result=invoke(clients["alice"],"order.get_order_status",{"order_id":"O2001","user_id":"bob"})
    assert result["error_code"] == "VALIDATION_ERROR"


def test_session_isolation(clients):
    sid=clients["alice"].post("/chat",json={"message":"我的订单"}).json()["session_id"]
    assert clients["bob"].get(f"/sessions/{sid}/messages").status_code == 404
    assert clients["bob"].post("/chat",json={"message":"我的订单","session_id":sid}).status_code == 404


def test_draft_isolation(clients):
    d=prepare(clients["alice"])
    r=clients["bob"].post(f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":d["confirmation_token"]})
    assert r.status_code == 403


def test_buyer_cannot_admin(clients):
    assert clients["alice"].get("/admin/plugins").status_code == 403
    assert clients["alice"].post("/admin/plugins/logistics/disable",json={}).status_code == 403


def test_internal_call_requires_signature(server):
    r=httpx.post(server.url+"/internal/invoke",json={"tool":"order.get_order_status","params":{"order_id":"O2001"},"identity":{"user_id":"bob","role":"buyer"},"request_id":"spoof"},trust_env=False)
    assert r.status_code == 401


def test_shutdown_requires_local_secret(server):
    assert httpx.post(server.url+"/internal/shutdown",trust_env=False).status_code == 401
    assert httpx.get(server.url+"/health",trust_env=False).status_code == 200
