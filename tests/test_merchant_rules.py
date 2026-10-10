import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from conftest import invoke
from shopagent.contracts.models import BusinessError, Identity


def policy(pid='test_policy', **changes):
    return dict(policy_id=pid, title='测试政策', tags='验收关键字', text='第一版说明', **changes)


def promotion(pid='TEST-OFFER', **changes):
    value = dict(promotion_id=pid, title='测试优惠', category='耳机', product_ids=['P001'], kind='percentage',
                 rate_percent=90, starts_at='2000-01-01T00:00:00+00:00', ends_at='2100-01-01T00:00:00+00:00')
    value.update(changes)
    return value


def publish(admin, resource, key, pid, revision, action='publish'):
    return admin.post('/admin/'+resource+'/action', json={key:pid, 'expected_revision':revision, 'action':action})


def test_policy_draft_publish_history_and_disable(clients):
    admin, buyer=clients['admin'],clients['alice']
    assert admin.post('/admin/policies/save', json=policy()).json()['revision']==1
    assert not invoke(buyer,'knowledge.search_policy',{'question':'验收关键字'})['data']['evidence']
    assert publish(admin,'policies','policy_id','test_policy',1).status_code==200
    assert admin.post('/admin/policies/save',json={**policy(), 'text':'第二版说明','expected_revision':2}).status_code==200
    live=invoke(buyer,'knowledge.search_policy',{'question':'验收关键字'})['data']['evidence'][0]
    assert live['text']=='第一版说明' and live['version']=='v2'
    assert publish(admin,'policies','policy_id','test_policy',3).status_code==200
    history=admin.get('/admin/policies/test_policy/history').json()['versions']
    assert [v['document']['text'] for v in history]==['第二版说明','第一版说明']
    assert publish(admin,'policies','policy_id','test_policy',4,'disable').status_code==200
    assert not invoke(buyer,'knowledge.search_policy',{'question':'验收关键字'})['data']['evidence']
    assert len(admin.get('/admin/policies/test_policy/history').json()['versions'])==2


def test_policy_scheduled_expired_and_revision_conflict(clients):
    admin=clients['admin']
    assert admin.post('/admin/policies/save',json=policy('future_policy', starts_at='2099-01-01T00:00:00Z',ends_at='2100-01-01T00:00:00Z')).status_code==200
    assert publish(admin,'policies','policy_id','future_policy',1).json()['effective'] is False
    assert not invoke(clients['alice'],'knowledge.search_policy',{'question':'验收关键字'})['data']['evidence']
    assert admin.post('/admin/policies/save',json=policy('future_policy')).status_code==409
    assert admin.post('/admin/policies/save',json=policy('expired_policy', ends_at='2001-01-01T00:00:00Z')).status_code==200
    assert publish(admin,'policies','policy_id','expired_policy',1).status_code==409


@pytest.mark.parametrize('change', [{'text':' '}, {'starts_at':'2026-01-01T00:00:00'}, {'starts_at':'2100-01-01T00:00:00Z','ends_at':'2099-01-01T00:00:00Z'}, {'kind':'return'}, {'return_rule':{'days':7}}])
def test_invalid_policy_rejected(clients,change):
    assert clients['admin'].post('/admin/policies/save',json={**policy('invalid_policy'),**change}).status_code==422


def test_policy_starters_are_read_only(clients):
    admin=clients['admin']
    before=admin.get('/admin/policies').json()
    starters=admin.get('/admin/policy-starter').json()['drafts']
    assert len(starters)==11 and any(p['policy_id']=='return' and p['return_rule']['start_next_day'] for p in starters)
    assert admin.get('/admin/policies').json()==before


def test_buyers_cannot_manage_rules_via_http_or_generic_tools(clients):
    buyer=clients['alice']
    for url in ['/admin/policies','/admin/policy-starter','/admin/policies/return/history','/admin/promotions']:
        assert buyer.get(url).status_code==403
    for tool,params in [('knowledge.admin_catalog',{}),('knowledge.admin_save',policy()),('knowledge.admin_action',{'policy_id':'return','expected_revision':1,'action':'disable'}),('knowledge.admin_history',{'policy_id':'return'}),('recommend.admin_promotions',{}),('recommend.admin_save_promotion',promotion()),('recommend.admin_promotion_action',{'promotion_id':'DEMO-EAR-10','expected_revision':1,'action':'disable'})]:
        assert invoke(buyer,tool,params)['error_code']=='FORBIDDEN'


def test_promotion_publish_draft_scope_threshold_and_disable(clients):
    admin,buyer=clients['admin'],clients['alice']
    gross=invoke(buyer,'product.get_product_detail',{'product_id':'P001'})['data']['price_fen']
    payload=promotion(min_amount_fen=gross+1)
    assert admin.post('/admin/promotions/save',json=payload).status_code==200
    assert invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','promotion_id':'TEST-OFFER'})['error_code']=='NOT_FOUND'
    assert publish(admin,'promotions','promotion_id','TEST-OFFER',1).status_code==200
    one=invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','promotion_id':'TEST-OFFER'})['data']
    two=invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','quantity':2,'promotion_id':'TEST-OFFER'})['data']
    assert not one['eligible'] and one['payable_fen']==gross
    assert two['eligible'] and two['payable_fen']==gross*2*90//100
    other=invoke(buyer,'recommend.calculate_discount',{'product_id':'P002','quantity':20,'promotion_id':'TEST-OFFER'})['data']
    assert not other['eligible']
    assert admin.post('/admin/promotions/save',json={**payload,'rate_percent':80,'expected_revision':2}).status_code==200
    assert invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','quantity':2,'promotion_id':'TEST-OFFER'})['data']['payable_fen']==two['payable_fen']
    assert publish(admin,'promotions','promotion_id','TEST-OFFER',3,'disable').status_code==200
    assert invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','promotion_id':'TEST-OFFER'})['error_code']=='NOT_FOUND'


def test_fixed_discount_clamps_at_zero_and_category_must_match(clients):
    admin,buyer=clients['admin'],clients['alice']
    assert admin.post('/admin/promotions/save',json=promotion('TEST-FIXED',kind='fixed',discount_fen=10000000,category='')).status_code==200
    assert publish(admin,'promotions','promotion_id','TEST-FIXED',1).status_code==200
    assert invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','promotion_id':'TEST-FIXED'})['data']['payable_fen']==0
    assert admin.post('/admin/promotions/save',json=promotion('TEST-WRONG',category='不存在的分类')).status_code==200
    assert publish(admin,'promotions','promotion_id','TEST-WRONG',1).status_code==200
    assert not invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','promotion_id':'TEST-WRONG'})['data']['eligible']


def test_promotion_scheduled_missing_dates_and_conflicts(clients):
    admin,buyer=clients['admin'],clients['alice']
    assert admin.post('/admin/promotions/save',json=promotion('TEST-FUTURE', starts_at='2099-01-01T00:00:00Z')).status_code==200
    assert publish(admin,'promotions','promotion_id','TEST-FUTURE',1).json()['effective'] is False
    assert invoke(buyer,'recommend.calculate_discount',{'product_id':'P001','promotion_id':'TEST-FUTURE'})['error_code']=='PROMOTION_INACTIVE'
    active=invoke(buyer,'recommend.query_promotions',{})['data']['promotions']
    assert 'TEST-FUTURE' not in [p['id'] for p in active]
    assert admin.post('/admin/promotions/save',json=promotion('TEST-FUTURE')).status_code==409
    assert admin.post('/admin/promotions/save',json=promotion('TEST-NODATE',starts_at=None,ends_at=None)).status_code==200
    assert publish(admin,'promotions','promotion_id','TEST-NODATE',1).status_code==409
    assert admin.post('/admin/promotions/save',json=promotion('TEST-EXPIRED',ends_at='2001-01-01T00:00:00Z')).status_code==200
    assert publish(admin,'promotions','promotion_id','TEST-EXPIRED',1).status_code==409


@pytest.mark.parametrize('changes',[{'rate_percent':1.5},{'min_amount_fen':True},{'kind':'fixed','discount_fen':0},{'product_ids':['P001','P001']},{'ends_at':'1999-01-01T00:00:00Z'}])
def test_invalid_promotion_rejected(clients,changes):
    assert clients['admin'].post('/admin/promotions/save',json=promotion('TEST-INVALID',**changes)).status_code==422


def test_disabled_promotions_stay_disabled_on_worker_restart(clients):
    admin=clients['admin']
    before=admin.get('/admin/promotions').json()
    try:
        assert admin.post('/admin/plugins/recommend/disable',json={}).status_code==200
        assert admin.get('/admin/promotions').status_code==409
        assert invoke(clients['alice'],'order.get_order_status',{'order_id':'O1001'})['status']=='ok'
    finally:
        admin.post('/admin/plugins/recommend/enable',json={}).raise_for_status()
    assert admin.get('/admin/promotions').json()==before


def test_return_window_next_day_opened_and_quality(monkeypatch):
    from shopagent.plugins.aftersale import service
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):
            return cls(2026,10,9,15,59,tzinfo=timezone.utc)
    monkeypatch.setattr(service,'datetime',Frozen)
    order={'status':'已签收','delivered_at':'2026-10-02T00:00:00+08:00'}
    rule={'days':7,'start_next_day':True,'opened_requires_review':True,'quality_manual_review':True}
    params=service.Prepare(order_id='O1002',reason='不合适',opened=False,quality_issue=False)
    service.eligible(order,rule,params) # 第7天23:59，尚在窗口。
    with pytest.raises(BusinessError) as error:
        service.eligible(order,{**rule,'start_next_day':False},params)
    assert error.value.code=='INELIGIBLE'
    for values in [{'opened':True},{'quality_issue':True}]:
        with pytest.raises(BusinessError) as error:
            service.eligible(order,rule,params.model_copy(update=values))
        assert error.value.code=='MANUAL_REVIEW'
    with pytest.raises(BusinessError) as error:
        service.eligible({**order,'delivered_at':'2020-01-01T00:00:00Z'},rule,params.model_copy(update={'quality_issue':True}))
    assert error.value.code=='MANUAL_REVIEW'


def test_changed_policy_invalidates_pending_return(tmp_path,monkeypatch):
    from shopagent import storage
    from shopagent.plugins.aftersale import service
    monkeypatch.setattr(storage,'DATA',tmp_path)
    service.initialize()
    rule={'days':7,'start_next_day':True,'opened_requires_review':True,'quality_manual_review':True,'policy':{'version':'v1'}}
    order={'id':'O1002','status':'已签收','delivered_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
    async def call(req,tool,params):
        return order if tool.startswith('order.') else rule.copy()
    monkeypatch.setattr(service,'call',call)
    req=SimpleNamespace(identity=Identity(user_id='alice'))
    async def scenario():
        draft=await service.prepare(service.Prepare(order_id='O1002',reason='不合适',opened=False,quality_issue=False),req)
        rule['policy']={'version':'v2'}
        with pytest.raises(BusinessError) as error:
            await service.confirm(service.Confirm(draft_id=draft['draft_id'],confirmation_token=draft['confirmation_token']),req)
        assert error.value.code=='STALE_DRAFT'
        with storage.connect('aftersale') as db:
            assert db.execute("SELECT count(*) FROM returns WHERE draft_id=?",(draft['draft_id'],)).fetchone()[0]==0
    asyncio.run(scenario())


def test_published_return_rule_and_evidence_use_same_version(tmp_path,monkeypatch):
    from shopagent import storage
    from shopagent.plugins.knowledge import admin, service
    monkeypatch.setattr(storage,'DATA',tmp_path)
    admin.initialize_store([])
    req=SimpleNamespace(identity=Identity(user_id='admin',role='admin'))
    async def scenario():
        await admin.save(admin.PolicyWrite(policy_id='return',title='测试退货',tags='退货',text='需核实本人订单和商品情况。',kind='return',return_rule=admin.ReturnRule(days=14)),req)
        await admin.action(admin.PolicyAction(policy_id='return',expected_revision=1,action='publish'),req)
        executable=await service.rule(service.GetRule(),req)
        evidence=(await service.search(service.SearchPolicy(question='退货'),req))['evidence'][0]
        assert executable['days']==14 and executable['policy']==evidence
        assert '14天' in evidence['text'] and not executable['legacy']
        await admin.action(admin.PolicyAction(policy_id='return',expected_revision=2,action='disable'),req)
        with pytest.raises(BusinessError) as error:
            await service.rule(service.GetRule(),req)
        assert error.value.code=='POLICY_UNAVAILABLE'
    asyncio.run(scenario())
