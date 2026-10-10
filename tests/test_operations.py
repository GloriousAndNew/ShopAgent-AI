import asyncio
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from shopagent.core import operations
from shopagent.contracts.models import BusinessError


@pytest.fixture
def ops(tmp_path,monkeypatch):
    from shopagent import storage,settings
    monkeypatch.setattr(storage,'DATA',tmp_path)
    monkeypatch.setattr(settings,'RUNTIME_ENV','development')
    operations.initialize()
    return operations


def change(ops,**changes):
    value=ops.read()
    body={k:value[k] for k in ['shop_name','greeting','service_hours','ai_enabled','daily_model_calls']}
    return ops.save(ops.SettingsWrite(expected_revision=value['revision'],**(body|changes)),'owner')


def test_persistent_quota_and_initialization(ops):
    change(ops,daily_model_calls=2)
    ops.reserve();ops.initialize();ops.reserve()
    with pytest.raises(BusinessError) as error:ops.reserve()
    assert error.value.code=='MODEL_DAILY_LIMIT'
    assert ops.read()['model_calls_used']==2


def test_last_slot_atomic(ops):
    change(ops,daily_model_calls=1)
    def request(_):
        try:ops.reserve();return True
        except BusinessError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(request,range(8)))==1


def test_rollover_keeps_history(ops,monkeypatch):
    change(ops,daily_model_calls=1)
    monkeypatch.setattr(ops,'day_key',lambda:'2026-10-09');ops.reserve()
    monkeypatch.setattr(ops,'day_key',lambda:'2026-10-10');assert ops.read()['model_calls_used']==0
    ops.reserve()
    monkeypatch.setattr(ops,'day_key',lambda:'2026-10-09')
    with pytest.raises(BusinessError):ops.reserve()


@pytest.mark.parametrize('changes',[{'daily_model_calls':0},{'ai_enabled':False}])
def test_block_before_network(ops,changes):
    change(ops,**changes)
    class Provider:
        async def complete(self,*args):pytest.fail('不应访问模型')
    with pytest.raises(BusinessError):asyncio.run(ops.GuardedProvider(Provider()).complete([],[]))
    assert ops.read()['model_calls_used']==0


def test_failure_counts_attempt(ops):
    class Provider:
        async def complete(self,*args):raise TimeoutError('测试超时')
    with pytest.raises(TimeoutError):asyncio.run(ops.GuardedProvider(Provider()).complete([],[]))
    assert ops.read()['model_calls_used']==1
    from shopagent.storage import connect
    with connect('core') as db:assert db.execute('SELECT status FROM model_attempts').fetchone()[0]=='failed_or_cancelled'


def test_optimistic_revision(ops):
    before=ops.read();change(ops,shop_name='测试店')
    with pytest.raises(BusinessError):ops.save(ops.SettingsWrite(expected_revision=before['revision'],shop_name='旧稿',greeting='你好',service_hours='9点',ai_enabled=True,daily_model_calls=1),'owner')
    assert ops.read()['shop_name']=='测试店'


def test_public_settings_and_admin_guards(server,clients):
    admin=clients['admin'];previous=admin.get('/admin/operations').json()
    keys=['shop_name','greeting','service_hours','ai_enabled','daily_model_calls']
    payload={k:previous[k] for k in keys}
    try:
        assert clients['alice'].get('/admin/operations').status_code==403
        assert clients['alice'].post('/admin/operations',json=payload|{'expected_revision':previous['revision']}).status_code==403
        assert httpx.get(server.url+'/admin/operations',trust_env=False).status_code==401
        result=admin.post('/admin/operations',json=payload|{'expected_revision':previous['revision'],'shop_name':'独立测试店','ai_enabled':False,'daily_model_calls':0})
        assert result.status_code==200,result.text
        assert result.json()['model_calls_remaining']==0
        public=clients['alice'].get('/runtime').json()['storefront']
        assert set(public)=={'shop_name','greeting','service_hours'} and public['shop_name']=='独立测试店'
        assert admin.post('/admin/operations',json=payload|{'expected_revision':previous['revision']}).status_code==409
        assert clients['alice'].post('/customer/handoff',json={'message':'AI关闭后仍可人工咨询'}).status_code==200
        assert admin.post('/admin/operations',json=payload|{'expected_revision':result.json()['revision'],'daily_model_calls':-1}).status_code==422
    finally:
        current=admin.get('/admin/operations').json()
        admin.post('/admin/operations',json=payload|{'expected_revision':current['revision']}).raise_for_status()
