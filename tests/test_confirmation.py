import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from conftest import prepare


def test_no_submit_before_confirm(clients, server):
    d=prepare(clients["alice"])
    with sqlite3.connect(server.directory/"aftersale.db") as db:
        assert db.execute("SELECT count(*) FROM returns WHERE draft_id=?",(d["draft_id"],)).fetchone()[0] == 0


def test_wrong_token(clients):
    d=prepare(clients["alice"])
    assert clients["alice"].post(f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":"wrong"}).status_code == 403


def test_idempotent_confirm(clients):
    d=prepare(clients["alice"]); c=clients["alice"]
    a=c.post(f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":d["confirmation_token"]}).json()
    b=c.post(f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":d["confirmation_token"]}).json()
    assert a["data"]["id"] == b["data"]["id"]
    assert b["data"]["idempotent"] is True


def test_concurrent_confirm(clients, server):
    d=prepare(clients["alice"]); c=clients["alice"]
    with ThreadPoolExecutor(2) as executor:
        futures=[executor.submit(c.post,f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":d["confirmation_token"]}) for _ in range(2)]
        values=[f.result().json() for f in futures]
    assert values[0]["data"]["id"] == values[1]["data"]["id"]
    with sqlite3.connect(server.directory/"aftersale.db") as db:
        assert db.execute("SELECT count(*) FROM returns WHERE draft_id=?",(d["draft_id"],)).fetchone()[0] == 1


def test_cancelled_draft(clients):
    d=prepare(clients["alice"]); c=clients["alice"]
    assert c.post(f"/returns/{d['draft_id']}/cancel",json={"confirmation_token":d["confirmation_token"]}).status_code == 200
    assert c.post(f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":d["confirmation_token"]}).status_code == 409


def test_expired_draft(clients, server):
    d=prepare(clients["alice"])
    with sqlite3.connect(server.directory/"aftersale.db") as db:
        db.execute("UPDATE drafts SET expires_at=? WHERE id=?",(time.time()-1,d["draft_id"]))
    assert clients["alice"].post(f"/returns/{d['draft_id']}/confirm",json={"confirmation_token":d["confirmation_token"]}).status_code == 409
