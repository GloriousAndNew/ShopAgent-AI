import uuid
import httpx
import pytest
from conftest import invoke


@pytest.fixture(scope='module')
def visitors(server):
    values=[]
    for _ in range(2):
        c=httpx.Client(base_url=server.url,timeout=20,trust_env=False)
        value=c.post('/auth/guest',json={}).json()
        c.headers['Authorization']='Bearer '+value['token']
        values.append(c)
    yield values
    for c in values:c.close()


def handoff(c,**changes):
    response=c.post('/customer/handoff',json={'message':'希望人工确认商品规格',**changes})
    assert response.status_code==200,response.text
    return response.json()


def test_customer_and_merchant_pages_and_guest_identity(server,visitors):
    for path in ['/customer','/merchant']:
        response=httpx.get(server.url+path,trust_env=False)
        assert response.status_code==200
        assert response.headers['Cache-Control']=='no-store'
        assert response.headers['X-Frame-Options']=='DENY'
    a,b=[v.get('/auth/me').json()['user'] for v in visitors]
    assert a['user_id']!=b['user_id'] and a['role']==b['role']=='buyer'
    assert a['user_id'].startswith('guest_')
    assert visitors[0].get('/admin/tickets').status_code==403


@pytest.mark.parametrize('tool,params',[
    ('order.list_orders',{}),('order.get_order_status',{'order_id':'O1001'}),
    ('logistics.track_logistics',{'order_id':'O1001'}),('knowledge.get_return_rule',{}),
    ('aftersale.prepare_return',{'order_id':'O1002','reason':'不合适','opened':False,'quality_issue':False}),
    ('recommend.get_user_profile',{}),('product.admin_catalog',{}),
])
def test_guests_cannot_bypass_consultation_scope(visitors,tool,params):
    assert invoke(visitors[0],tool,params)['error_code']=='CONSULTATION_ONLY'


def test_guests_can_query_products_and_policies_but_order_chat_is_blocked(visitors):
    c=visitors[0]
    assert invoke(c,'product.get_product_detail',{'product_id':'P001'})['status']=='ok'
    assert invoke(c,'knowledge.search_policy',{'question':'发货'})['status']=='ok'
    value=c.post('/chat',json={'message':'我的订单'}).json()
    assert value['error_code']=='CONSULTATION_ONLY' and not value['cards']


def test_handoff_context_reply_unread_read_receipts_and_isolation(visitors,clients):
    c,other=visitors;admin=clients['admin']
    chat=c.post('/chat',json={'message':'200元以内的降噪耳机'}).json()
    value=handoff(c,session_id=chat['session_id']);tid=value['ticket']['id'];sid=value['session_id']
    detail=admin.get('/admin/tickets/'+tid).json()
    assert any('200元' in m['content'] for m in detail['messages'])
    body={'message':'人工回复验收：规格需进一步核实。','request_key':str(uuid.uuid4())}
    path='/admin/tickets/'+tid+'/reply'
    assert admin.post(path,json=body).status_code==200
    assert admin.post(path,json=body).json()['duplicate']
    ticket=next(t for t in c.get('/customer/tickets').json() if t['id']==tid)
    assert ticket['unread']==1 and ticket['status']=='已回复'
    assert tid not in [t['id'] for t in other.get('/customer/tickets').json()]
    assert other.get('/sessions/'+sid+'/messages').status_code==404
    assert other.post('/sessions/'+sid+'/read',json={'message_id':0}).status_code==404
    assert other.post('/customer/handoff',json={'message':'越权会话','session_id':sid}).status_code==404
    assert other.get('/admin/tickets/'+tid).status_code==403
    messages=c.get('/sessions/'+sid+'/messages').json()
    assert next(t for t in c.get('/customer/tickets').json() if t['id']==tid)['unread']==1 # GET不隐式已读。
    mid=messages[-1]['id']
    assert c.post('/sessions/'+sid+'/read',json={'message_id':mid}).status_code==200
    assert c.post('/sessions/'+sid+'/read',json={'message_id':mid+100000}).status_code==404
    assert next(t for t in c.get('/customer/tickets').json() if t['id']==tid)['unread']==0
    another=handoff(other)
    foreign=other.get('/sessions/'+another['session_id']+'/messages').json()[-1]['id']
    assert c.post('/sessions/'+sid+'/read',json={'message_id':foreign}).status_code==404


def test_ticket_close_reopen_conflict_and_reply(clients,visitors):
    admin=clients['admin'];value=handoff(visitors[0],message='关闭与重新打开验收');tid=value['ticket']['id']
    path='/admin/tickets/'+tid
    current=value['ticket']['status']
    assert admin.post(path+'/status',json={'expected_status':current,'status':'已关闭'}).status_code==200
    assert admin.post(path+'/status',json={'expected_status':current,'status':'待处理'}).status_code==409
    body={'message':'已关闭不允许新回复','request_key':str(uuid.uuid4())}
    assert admin.post(path+'/reply',json=body).json()['error_code']=='TICKET_CLOSED'
    assert visitors[0].post(path+'/status',json={'expected_status':'已关闭','status':'待处理'}).status_code==403
    assert admin.post(path+'/status',json={'expected_status':'已关闭','status':'待处理'}).status_code==200
    assert admin.post(path+'/reply',json=body).status_code==200
    events=admin.get(path).json()['events']
    assert [e['new_status'] for e in events]==['已关闭','待处理']


def test_handoff_validation_dedup_and_status_filter(visitors,clients):
    c=visitors[1];a=handoff(c,message='去重测试')
    b=handoff(c,message='去重测试',session_id=a['session_id'])
    assert a['ticket']['id']==b['ticket']['id']
    assert c.post('/customer/handoff',json={'message':'   '}).status_code==422
    assert c.post('/customer/handoff',json={'message':'身份伪造','identity':{'role':'admin'}}).status_code==422
    assert all(t['status'].startswith('待处理') for t in clients['admin'].get('/admin/tickets?status=pending').json())
    assert clients['admin'].get('/admin/tickets?status=bad').status_code==422


def test_launch_readiness_is_admin_only_and_does_not_claim_deployed(visitors,clients):
    assert visitors[0].get('/admin/launch-readiness').status_code==403
    value=clients['admin'].get('/admin/launch-readiness').json()
    assert not value['ready'] and value['blockers']


def test_rate_limit_expiry_and_capacity(monkeypatch):
    from shopagent.core import limits
    from shopagent.contracts.models import BusinessError
    instant=[0]
    monkeypatch.setattr(limits.time,'monotonic',lambda:instant[0])
    limiter=limits.RateLimiter(max_keys=1)
    limiter.check('a',2,60);limiter.check('a',2,60)
    with pytest.raises(BusinessError) as error:limiter.check('a',2,60)
    assert error.value.code=='RATE_LIMITED'
    with pytest.raises(BusinessError):limiter.check('b',2,60)
    instant[0]=61
    limiter.check('b',2,60)


def test_core_migration_preserves_messages_and_read_cursor(tmp_path,monkeypatch):
    import sqlite3
    from shopagent import storage
    from shopagent.core import sessions
    monkeypatch.setattr(storage,'DATA',tmp_path)
    with sqlite3.connect(tmp_path/'core.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT,context TEXT,created_at TEXT)')
        db.execute("INSERT INTO sessions VALUES('old','alice','{}','old-time')")
    sessions.initialize();sessions.initialize()
    with storage.connect('core') as db:
        row=dict(db.execute("SELECT * FROM sessions WHERE id='old'").fetchone())
        assert row['context']=='{}' and row['last_read_id']==0
