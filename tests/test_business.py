import pytest
from conftest import invoke


@pytest.mark.parametrize("question,budget", [("200 元以内的降噪耳机",20000),("100元以内耳机",10000),("预算500耳机",50000),("200以下鼠标",20000),("50元以内商品",5000),("300 元以内键盘",30000)])
def test_product_chat(clients, question, budget):
    data = clients["alice"].post("/chat", json={"message": question}).json()
    assert data["intent"] == "product_query"
    assert not data.get("error_code")
    assert all(card["price_fen"] <= budget for card in data["cards"])
    if "降噪" in question:
        assert all(card["noise_cancel"] for card in data["cards"])


@pytest.mark.parametrize("pid,color", [("P001","黑色"),("P001","白色"),("P002","黑色"),("P002","白色"),("P003","黑色"),("P008","白色")])
def test_inventory_chat(clients, pid, color):
    data = clients["alice"].post("/chat", json={"message": f"{pid} {color}还有货吗"}).json()
    assert data["intent"] == "inventory_check"
    assert data["cards"][0]["product_id"] == pid
    assert data["cards"][0]["color"] == color
    if pid == "P002" and color == "黑色":
        assert data["cards"][0]["stock"] == 0


@pytest.mark.parametrize("question,expected", [("查订单 O1001","运输中"),("订单 O1002 签收了吗","已签收"),("订单 O1003 发货了吗","待发货"),("查订单 O1004","已取消"),("查订单 O1005","已签收"),("订单 O1001 什么状态","运输中")])
def test_order_chat(clients, question, expected):
    data = clients["alice"].post("/chat", json={"message": question}).json()
    assert data["intent"] == "order_query"
    assert data["cards"][0]["status"] == expected
    assert data["cards"][0]["user_id"] == "alice"


@pytest.mark.parametrize("question", ["订单 O1001 到哪了","查物流 O1002","快递 O1003 到哪了","物流 O1004","O1005 物流","O1001 什么时候到"])
def test_logistics_chat(clients, question):
    data = clients["alice"].post("/chat", json={"message": question}).json()
    assert data["intent"] == "logistics_track"
    assert data["cards"][0]["eta"] is None
    assert "未知" in data["reply"] or "没有预计" in data["reply"]


@pytest.mark.parametrize("question", ["拆封以后能退吗","售后政策","保修多久","退款政策","发票规则","优惠政策"])
def test_policy_chat(clients, question):
    data = clients["alice"].post("/chat", json={"message": question}).json()
    assert data["intent"] == "policy_query"
    assert data["evidence"]
    assert all(item["source"] and item["version"] and item["text"] for item in data["evidence"])


@pytest.mark.parametrize("oid,opened,quality", [("O1002",True,True),("O1002",False,True),("O1002",False,False),("O1005",True,True),("O1005",False,True),("O1005",False,False)])
def test_return_draft(clients, oid, opened, quality):
    result = invoke(clients["alice"], "aftersale.prepare_return", {"order_id":oid,"reason":"质量问题" if quality else "不喜欢","opened":opened,"quality_issue":quality})
    assert result["status"] == "ok"
    assert result["data"]["state"] == "awaiting_confirmation"
    assert result["data"]["confirmation_token"]


def test_multiturn_stock(clients):
    c=clients["alice"]
    first=c.post("/chat",json={"message":"200 元以内的降噪耳机"}).json()
    second=c.post("/chat",json={"message":"第二款黑色还有货吗","session_id":first["session_id"]}).json()
    assert second["cards"][0]["product_id"] == first["cards"][1]["id"]


def test_multiturn_return(clients):
    c=clients["alice"]
    first=c.post("/chat",json={"message":"申请退货 O1002"}).json()
    assert first["intent"] == "return_collect"
    second=c.post("/chat",json={"message":"已拆封，质量问题没声音","session_id":first["session_id"]}).json()
    assert second["draft"]["order"]["id"] == "O1002"


def test_missing_evidence(clients):
    result=invoke(clients["alice"],"knowledge.search_policy",{"question":"火星矿石的磁场参数"})
    assert result["data"]["evidence"] == []


def test_ineligible_return(clients):
    result=invoke(clients["alice"],"aftersale.prepare_return",{"order_id":"O1002","reason":"不喜欢","opened":True,"quality_issue":False})
    assert result["error_code"] == "INELIGIBLE"
