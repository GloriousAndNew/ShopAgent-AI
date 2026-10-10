import asyncio
import json
import httpx
import pytest
from shopagent.core.gateway import GatewayBoundary,GatewayConfig


async def echo(scope,receive,send):
    event=await receive()
    body=json.dumps({'client':scope['client'][0],'body':event.get('body',b'').decode()}).encode()
    await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
    await send({'type':'http.response.body','body':body})


@pytest.mark.asyncio
@pytest.mark.parametrize('peer,trusted,headers,expected',[
    ('203.0.113.5',(),{'X-Real-IP':'192.0.2.1','X-Forwarded-For':'192.0.2.9'},'203.0.113.5'),
    ('127.0.0.1',(),{'X-Real-IP':'192.0.2.1'},'127.0.0.1'),
    ('127.0.0.1',('127.0.0.1',),{'X-Real-IP':'192.0.2.1','X-Forwarded-For':'192.0.2.9'},'192.0.2.1'),
    ('127.0.0.1',('127.0.0.1',),{},'127.0.0.1'),
])
async def test_proxy_trust(peer,trusted,headers,expected):
    transport=httpx.ASGITransport(app=GatewayBoundary(echo,GatewayConfig('shop.example.test',trusted)),client=(peer,123))
    async with httpx.AsyncClient(transport=transport,base_url='http://shop.example.test') as c:
        r=await c.post('/',content=b'hello',headers=headers)
        assert r.status_code==200 and r.json()=={'client':expected,'body':'hello'}


@pytest.mark.asyncio
@pytest.mark.parametrize('header',['not-an-ip','192.0.2.1,192.0.2.2'])
async def test_invalid_proxy_header(header):
    transport=httpx.ASGITransport(app=GatewayBoundary(echo,GatewayConfig('shop.example.test',('127.0.0.1',))))
    async with httpx.AsyncClient(transport=transport,base_url='http://shop.example.test') as c:
        assert (await c.get('/',headers={'X-Real-IP':header})).status_code==400


@pytest.mark.asyncio
async def test_host_and_internal_boundary():
    transport=httpx.ASGITransport(app=GatewayBoundary(echo,GatewayConfig('shop.example.test',('127.0.0.1',))))
    async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as c:
        assert (await c.get('/',headers={'Host':'evil.example'})).status_code==400
        assert (await c.get('/internal/invoke',headers={'X-Real-IP':'192.0.2.2'})).status_code==404
        assert (await c.get('/internal/invoke')).status_code==200 # 本机插件仍可走鉴权通道


@pytest.mark.asyncio
async def test_size_limit_streaming_before_business():
    calls=[]
    async def business(*args):calls.append(True);await echo(*args)
    transport=httpx.ASGITransport(app=GatewayBoundary(business,GatewayConfig(max_body=4)))
    async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as c:
        assert (await c.post('/',content=b'12345')).status_code==413
        async def chunks():yield b'123';yield b'45'
        assert (await c.post('/',content=chunks())).status_code==413
        assert calls==[]
        assert (await c.post('/',content=b'1234')).status_code==200


@pytest.mark.asyncio
async def test_upload_timeout():
    sent=[]
    async def receive():await asyncio.sleep(1)
    async def send(event):sent.append(event)
    await GatewayBoundary(echo,GatewayConfig(body_timeout=.01))({'type':'http','client':('127.0.0.1',1),'headers':[(b'host',b'localhost')],'path':'/'},receive,send)
    assert sent[0]['status']==408


@pytest.mark.parametrize('value',['*','0.0.0.0','10.0.0.1','127.0.0.0/8'])
def test_no_wildcard_proxy_trust(monkeypatch,value):
    monkeypatch.setenv('SHOPAGENT_PUBLIC_HOST','shop.example.test')
    monkeypatch.setenv('SHOPAGENT_TRUSTED_PROXY_IPS',value)
    with pytest.raises(ValueError):GatewayConfig.from_env()


def test_real_server_rejects_forged_host_and_oversize(server):
    assert httpx.get(server.url+'/health',headers={'Host':'evil.example'},trust_env=False).status_code==400
    assert httpx.post(server.url+'/auth/login',content=b'x'*(512*1024+1),trust_env=False).status_code==413
    assert httpx.get(server.url+'/health',headers={'X-Forwarded-For':'192.0.2.4','X-Real-IP':'192.0.2.4'},trust_env=False).status_code==200


def test_real_proxy_clients_have_separate_limits(tmp_path,monkeypatch):
    from conftest import Server
    monkeypatch.setenv('SHOPAGENT_PUBLIC_HOST','shop.example.test')
    monkeypatch.setenv('SHOPAGENT_TRUSTED_PROXY_IPS','127.0.0.1')
    service=Server(tmp_path)
    try:
        service.start()
        with httpx.Client(base_url=service.url,trust_env=False,timeout=20,headers={'Host':'shop.example.test'}) as c:
            for _ in range(10):
                assert c.post('/auth/guest',json={},headers={'X-Real-IP':'192.0.2.10','X-Forwarded-For':'203.0.113.99'}).status_code==200
            assert c.post('/auth/guest',json={},headers={'X-Real-IP':'192.0.2.10'}).status_code==429
            assert c.post('/auth/guest',json={},headers={'X-Real-IP':'192.0.2.11'}).status_code==200
            assert c.post('/internal/shutdown',headers={'X-Real-IP':'192.0.2.11'}).status_code==404
    finally:
        service.stop()
        for log in service.logs:log.close()
