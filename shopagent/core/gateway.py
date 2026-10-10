"""ASGI 请求边界：实际体积限制、Host 校验和显式代理信任。"""
import asyncio
import ipaddress
import json
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    public_host: str = ''
    trusted_proxies: tuple = ()
    max_body: int = 512 * 1024
    body_timeout: float = 15

    @classmethod
    def from_env(cls):
        host=os.getenv('SHOPAGENT_PUBLIC_HOST','').strip().lower()
        if host and not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',host):
            raise ValueError('SHOPAGENT_PUBLIC_HOST 必须是域名，不含协议、端口或路径。')
        values=[]
        for value in os.getenv('SHOPAGENT_TRUSTED_PROXY_IPS','').split(','):
            if not value.strip():continue
            address=ipaddress.ip_address(value.strip())
            if not address.is_loopback:raise ValueError('当前模板仅支持同机回环地址代理。')
            values.append(str(address))
        if values and not host:raise ValueError('启用可信代理时须配置 SHOPAGENT_PUBLIC_HOST。')
        return cls(host,tuple(values))


class GatewayBoundary:
    def __init__(self,app,config=None):self.app=app;self.config=config or GatewayConfig.from_env()

    async def reject(self,send,status,code,message):
        body=json.dumps({'error_code':code,'message':message},ensure_ascii=False).encode()
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json; charset=utf-8'),(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff'),(b'content-length',str(len(body)).encode())]})
        await send({'type':'http.response.body','body':body})

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers=scope.get('headers',[])
        hosts=[v.decode('latin1').lower() for k,v in headers if k.lower()==b'host']
        allowed={'127.0.0.1','localhost','[::1]'}
        if self.config.public_host:allowed.add(self.config.public_host)
        host=hosts[0] if len(hosts)==1 else ''
        hostname=host.split(']:',1)[0]+']' if host.startswith('[') and ']:' in host else host.split(':',1)[0] if not host.startswith('[') else host
        if hostname not in allowed:
            return await self.reject(send,400,'INVALID_HOST','访问域名不在允许列表。')
        peer=scope.get('client',('',0))[0]
        real=[v.decode('latin1') for k,v in headers if k.lower()==b'x-real-ip']
        if peer in self.config.trusted_proxies and real:
            try:
                if len(real)!=1:raise ValueError()
                address=str(ipaddress.ip_address(real[0]))
            except ValueError:return await self.reject(send,400,'INVALID_PROXY_HEADER','代理来源地址格式不正确。')
            if scope['path']=='/internal' or scope['path'].startswith('/internal/'):
                return await self.reject(send,404,'NOT_FOUND','入口不存在。')
            scope={**scope,'client':(address,scope['client'][1])}
            # 不信任链式 X-Forwarded-For 或客户端自报协议。
        lengths=[v for k,v in headers if k.lower()==b'content-length']
        if lengths:
            if len(lengths)!=1 or not lengths[0].isdigit():
                return await self.reject(send,400,'INVALID_LENGTH','请求长度格式不正确。')
            if int(lengths[0])>self.config.max_body:
                return await self.reject(send,413,'REQUEST_TOO_LARGE','请求内容过大，请减少导入或文本大小。')
        messages=[];size=0
        try:
            async with asyncio.timeout(self.config.body_timeout):
                while True:
                    event=await receive()
                    if event['type']=='http.disconnect':return
                    size+=len(event.get('body',b''))
                    if size>self.config.max_body:
                        return await self.reject(send,413,'REQUEST_TOO_LARGE','请求内容过大，请减少导入或文本大小。')
                    messages.append(event)
                    if not event.get('more_body',False):break
        except TimeoutError:return await self.reject(send,408,'BODY_TIMEOUT','请求上传超时，请重试。')
        if lengths and int(lengths[0])!=size:
            return await self.reject(send,400,'INVALID_LENGTH','请求长度与实际内容不一致。')
        index=0
        async def replay():
            nonlocal index
            if index<len(messages):
                value=messages[index];index+=1;return value
            return await receive()
        return await self.app(scope,replay,send)
