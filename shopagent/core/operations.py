"""店铺公开信息及持久化模型调用预算，不记录提示词和密钥。"""
import json
from datetime import datetime,timezone,timedelta
from pydantic import Field,field_validator
from shopagent.contracts.models import StrictModel,BusinessError
from shopagent.storage import connect,now
from shopagent.core.environment import production


class SettingsWrite(StrictModel):
    expected_revision:int=Field(ge=1,strict=True)
    shop_name:str=Field(min_length=1,max_length=60)
    greeting:str=Field(min_length=1,max_length=120)
    service_hours:str=Field(min_length=1,max_length=200)
    ai_enabled:bool=Field(strict=True)
    daily_model_calls:int=Field(ge=0,le=50000,strict=True)

    @field_validator('shop_name','greeting','service_hours')
    @classmethod
    def trim(cls,value):
        if not value.strip():raise ValueError('内容不能为空')
        return value.strip()


def day_key():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def default_settings():
    return {'shop_name':'ShopAgent AI','greeting':'你好，有什么可以帮你？','service_hours':'人工咨询不保证即时回复，请在网页查看处理状态。','ai_enabled':True,'daily_model_calls':200 if production() else 1000}


def initialize():
    defaults=default_settings()
    with connect('core') as db:
        db.executescript('CREATE TABLE IF NOT EXISTS operating_settings(id INTEGER PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS operating_audit(id INTEGER PRIMARY KEY,actor TEXT,body TEXT,created_at TEXT); CREATE TABLE IF NOT EXISTS model_attempts(id INTEGER PRIMARY KEY,day TEXT,started_at TEXT,status TEXT); CREATE INDEX IF NOT EXISTS attempt_day ON model_attempts(day);')
        db.execute('INSERT OR IGNORE INTO operating_settings VALUES(1,?,1)',(json.dumps(defaults,ensure_ascii=False),))


def read():
    day=day_key()
    with connect('core') as db:
        row=db.execute('SELECT * FROM operating_settings WHERE id=1').fetchone()
        value=json.loads(row['body'])
        used=db.execute('SELECT count(*) FROM model_attempts WHERE day=?',(day,)).fetchone()[0]
    return {**value,'revision':row['revision'],'day':day,'model_calls_used':used,'model_calls_remaining':max(0,value['daily_model_calls']-used)}


def public_info():
    value=read()
    return {key:value[key] for key in ('shop_name','greeting','service_hours')}


def save(value,actor):
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        revision=db.execute('SELECT revision FROM operating_settings WHERE id=1').fetchone()[0]
        if revision!=value.expected_revision:
            raise BusinessError('REVISION_CONFLICT','运营设置已变化，请刷新后再保存。')
        body=value.model_dump(exclude={'expected_revision'})
        encoded=json.dumps(body,ensure_ascii=False)
        db.execute('UPDATE operating_settings SET body=?,revision=revision+1 WHERE id=1',(encoded,))
        db.execute('INSERT INTO operating_audit(actor,body,created_at) VALUES(?,?,?)',(actor,encoded,now()))
    return read()


def reserve():
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        value=json.loads(db.execute('SELECT body FROM operating_settings WHERE id=1').fetchone()[0])
        if not value['ai_enabled']:
            raise BusinessError('AI_PAUSED','AI咨询暂时暂停，请点击“联系人工客服”登记问题。')
        day=day_key()
        count=db.execute('SELECT count(*) FROM model_attempts WHERE day=?',(day,)).fetchone()[0]
        if count>=value['daily_model_calls']:
            raise BusinessError('MODEL_DAILY_LIMIT','今天的AI咨询额度已用完，请联系人工客服。')
        return db.execute('INSERT INTO model_attempts(day,started_at,status) VALUES(?,?,?)',(day,now(),'attempted')).lastrowid


class GuardedProvider:
    def __init__(self,provider):self.provider=provider

    async def complete(self,messages,tools):
        attempt=reserve() # 在网络请求前原子占额，重启和切换模型模式不能清零。
        status='failed_or_cancelled'
        try:
            result=await self.provider.complete(messages,tools)
            status='success'
            return result
        finally:
            with connect('core') as db:db.execute('UPDATE model_attempts SET status=? WHERE id=?',(status,attempt))
