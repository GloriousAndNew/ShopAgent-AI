import os
import subprocess
import sys
import httpx
import pytest
from conftest import Server, invoke
from shopagent.contracts.models import BusinessError


def test_directory_rejects_legacy_and_wrong_environment(tmp_path,monkeypatch):
    from shopagent import settings
    from shopagent.core.environment import ensure_directory
    monkeypatch.setattr(settings,'RUNTIME_ENV','production')
    (tmp_path/'core.db').write_bytes(b'legacy')
    with pytest.raises(BusinessError):ensure_directory(tmp_path)
    assert (tmp_path/'core.db').read_bytes()==b'legacy'
    fresh=tmp_path/'fresh';ensure_directory(fresh)
    monkeypatch.setattr(settings,'RUNTIME_ENV','development')
    with pytest.raises(BusinessError):ensure_directory(fresh)


def test_admin_initialization_strong_password_and_no_overwrite(tmp_path,monkeypatch):
    from shopagent import settings,storage
    from shopagent.core import sessions
    monkeypatch.setattr(settings,'RUNTIME_ENV','production');monkeypatch.setattr(storage,'DATA',tmp_path)
    sessions.initialize()
    with storage.connect('core') as db:assert db.execute('SELECT count(*) FROM users').fetchone()[0]==0
    with pytest.raises(BusinessError):sessions.validate_production_admin()
    for name,pwd in [('admin','Testing-only-12345'),('owner','demo123'),('guest_owner','Testing-only-12345')]:
        with pytest.raises(BusinessError):sessions.initialize_admin(name,pwd)
    sessions.initialize_admin('owner','Testing-only-12345');sessions.validate_production_admin()
    with pytest.raises(BusinessError):sessions.initialize_admin('owner2','Testing-only-12345')
    sessions.initialize()
    assert sessions.login('owner','Testing-only-12345')['user']['role']=='admin'
    with pytest.raises(BusinessError):sessions.login('admin','demo123')


@pytest.fixture(scope='module')
def formal(tmp_path_factory):
    directory=tmp_path_factory.mktemp('production')
    env={**os.environ,'SHOPAGENT_ENV':'production','SHOPAGENT_DATA_DIR':str(directory)}
    subprocess.run([sys.executable,'-X','utf8','-c',"from shopagent.core.sessions import initialize_admin; initialize_admin('owner','Testing-only-12345')"],env=env,check=True,capture_output=True)
    server=Server(directory)
    old=os.environ.get('SHOPAGENT_ENV');os.environ['SHOPAGENT_ENV']='production'
    try:server.start()
    finally:
        if old is None:os.environ.pop('SHOPAGENT_ENV',None)
        else:os.environ['SHOPAGENT_ENV']=old
    admin=httpx.Client(base_url=server.url,trust_env=False,timeout=20)
    admin.headers['Authorization']='Bearer '+admin.post('/auth/login',json={'username':'owner','password':'Testing-only-12345'}).json()['token']
    guest=httpx.Client(base_url=server.url,trust_env=False,timeout=20)
    guest.headers['Authorization']='Bearer '+guest.post('/auth/guest',json={'acknowledged':True,'notice_version':admin.get('/privacy').json()['version']}).json()['token']
    yield server,admin,guest
    admin.close();guest.close();server.stop()
    for log in server.logs:log.close()


def test_production_has_no_seeds_and_reserved_accounts(formal):
    server,admin,guest=formal
    assert admin.get('/runtime').json()['environment']=='production'
    assert admin.get('/',follow_redirects=False).status_code==307
    assert admin.post('/auth/login',json={'username':'admin','password':'demo123'}).status_code==401
    assert admin.get('/admin/products').json()['products']==[]
    assert admin.get('/admin/policies').json()['policies']==[]
    assert admin.get('/admin/promotions').json()['promotions']==[]
    assert not (server.directory/'policies').exists()
    assert not (server.directory/'order.db').exists()


@pytest.mark.parametrize('module',['order','logistics','aftersale','demo'])
def test_production_cannot_enable_sample_modules(formal,module):
    _,admin,guest=formal
    assert admin.post('/admin/plugins/'+module+'/enable',json={}).json()['error_code']=='PRODUCTION_SCOPE'
    assert invoke(guest,module+'.missing',{})['error_code']=='PRODUCTION_SCOPE'


def test_sample_product_cannot_publish_and_real_product_is_visible(formal):
    _,admin,guest=formal
    sample={'product_id':'P801','name':'验收资料','category':'配件','price_fen':12345,'skus':[{'color':'黑色','stock':3}]}
    assert admin.post('/admin/products/save',json={**sample,'status':'published'}).status_code==409
    assert admin.post('/admin/products/save',json=sample).status_code==200
    assert admin.post('/admin/products/status',json={'product_id':'P801','expected_revision':1,'status':'published'}).status_code==409
    assert admin.post('/admin/products/save',json={**sample,'simulation':False,'expected_revision':1,'status':'published'}).status_code==200
    assert invoke(guest,'product.get_product_detail',{'product_id':'P801'})['data']['simulation'] is False
    assert invoke(guest,'product.check_inventory',{'product_id':'P801'})['data']['stock']==3
    assert admin.get('/admin/products/starter').status_code==409
    assert admin.get('/admin/policy-starter').status_code==409


@pytest.mark.parametrize('resource,key,payload',[
    ('policies','policy_id',{'policy_id':'shipping','title':'配送','tags':'发货','text':'商家核实后填写。'}),
    ('promotions','promotion_id',{'promotion_id':'REAL-OFFER','title':'验收活动','starts_at':'2000-01-01T00:00:00Z','ends_at':'2100-01-01T00:00:00Z'}),
])
def test_sample_policy_and_promotion_rejected_until_reviewed(formal,resource,key,payload):
    _,admin,guest=formal
    assert admin.post('/admin/'+resource+'/save',json=payload).status_code==200
    action={key:payload[key],'expected_revision':1,'action':'publish'}
    assert admin.post('/admin/'+resource+'/action',json=action).json()['error_code']=='SAMPLE_NOT_PUBLISHABLE'
    assert admin.post('/admin/'+resource+'/save',json={**payload,'simulation':False,'expected_revision':1}).status_code==200
    assert admin.post('/admin/'+resource+'/action',json={**action,'expected_revision':2}).status_code==200


def test_runtime_scope_preserved_after_hotplug(formal):
    _,admin,guest=formal
    assert admin.post('/admin/plugins/recommend/disable',json={}).status_code==200
    assert admin.post('/admin/plugins/product/switch-version',json={'version':'1.1.0'}).status_code==200
    assert admin.post('/admin/plugins/recommend/enable',json={}).status_code==200
    assert invoke(guest,'product.get_product_detail',{'product_id':'P801'})['data']['simulation'] is False
    states={r['id']:r for r in admin.get('/admin/plugins').json()}
    assert states['order']['state']=='stopped'
    assert not admin.get('/admin/launch-readiness').json()['ready']


def test_production_hides_api_documentation(formal):
    _,admin,_=formal
    for path in ['/docs','/redoc','/openapi.json']:
        assert admin.get(path).status_code==404


def test_production_guest_requires_current_notice(formal):
    _,admin,_=formal
    assert admin.post('/auth/guest',json={}).status_code==409
    assert admin.post('/auth/guest',json={'acknowledged':True,'notice_version':'old'}).status_code==409
    assert admin.post('/auth/guest',json={'acknowledged':True,'notice_version':admin.get('/privacy').json()['version']}).status_code==200
