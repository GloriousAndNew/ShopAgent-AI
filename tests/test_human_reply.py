import uuid


def create_ticket(client):
    response = client.post("/chat", json={"message": "转人工"})
    assert response.status_code == 200
    return response.json()


def test_reply_reaches_original_customer_and_is_idempotent(clients):
    value = create_ticket(clients["alice"])
    path = f"/admin/tickets/{value['ticket']['id']}/reply"
    body = {"message": "我是商家客服，请描述您遇到的问题。", "request_key": str(uuid.uuid4())}
    assert clients["admin"].post(path, json=body).json()["duplicate"] is False
    assert clients["admin"].post(path, json=body).json()["duplicate"] is True
    history = clients["alice"].get(f"/sessions/{value['session_id']}/messages").json()
    replies = [m for m in history if m["result"].get("source") == "human"]
    assert len(replies) == 1
    assert replies[0]["content"] == body["message"]
    assert clients["bob"].get(f"/sessions/{value['session_id']}/messages").status_code == 404
    changed = {**body, "message": "另一条回复"}
    assert clients["admin"].post(path, json=changed).status_code == 409


def test_only_admin_can_reply(clients):
    value = create_ticket(clients["alice"])
    body = {"message": "冒充商家", "request_key": str(uuid.uuid4())}
    response = clients["alice"].post(f"/admin/tickets/{value['ticket']['id']}/reply", json=body)
    assert response.status_code == 403


def test_reply_requires_existing_ticket_and_nonblank_content(clients):
    body = {"message": "你好", "request_key": str(uuid.uuid4())}
    assert clients["admin"].post("/admin/tickets/T-NOTFOUND/reply", json=body).status_code == 404
    value = create_ticket(clients["alice"])
    body["message"] = "   "
    assert clients["admin"].post(f"/admin/tickets/{value['ticket']['id']}/reply", json=body).status_code == 409
