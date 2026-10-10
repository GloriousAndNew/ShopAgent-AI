"""隐私告知、仅访客咨询清理和不含聊天内容的运行事件。"""
import json
import time
from datetime import datetime,timezone,timedelta
from pydantic import Field
from shopagent.contracts.models import StrictModel,BusinessError
from shopagent.storage import connect,now

NOTICE_VERSION='2026-10-10.1'

class GuestNotice(StrictModel):
    acknowledged:bool=Field(default=False,strict=True)
    notice_version:str=Field(default='',max_length=40)

class RetentionWrite(StrictModel):
    expected_revision:int=Field(ge=1,strict=True)
    enabled:bool=Field(strict=True)
    days:int=Field(ge=7,le=365,strict=True)

class CleanupRequest(StrictModel):
    expected_revision:int=Field(ge=1,strict=True)


def initialize():
    with connect('core') as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS privacy_ack(user_id TEXT PRIMARY KEY REFERENCES users(id),version TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS retention_settings(id INTEGER PRIMARY KEY,enabled INTEGER,days INTEGER,revision INTEGER);
        INSERT OR IGNORE INTO retention_settings VALUES(1,0,90,1);
        CREATE TABLE IF NOT EXISTS retention_runs(id INTEGER PRIMARY KEY,created_at TEXT,actor TEXT,sessions INTEGER,users INTEGER,tokens INTEGER);
        CREATE TABLE IF NOT EXISTS operational_events(id INTEGER PRIMARY KEY,created_at TEXT,code TEXT);
        CREATE INDEX IF NOT EXISTS event_time ON operational_events(created_at);""")


def current_version():
    return NOTICE_VERSION+'-r'+str(settings()['revision'])


def notice():
    value=settings()
    return {'version':current_version(),'title':'咨询数据与 AI 使用说明','paragraphs':[
        '为了提供咨询和人工回复，店铺会保存你的问题、客服回答、咨询状态，以及随机访客编号。管理员可查看咨询上下文并回复。',
        '启用真实 AI 时，你的问题、相关聊天上下文和必要商品或政策资料会发送到店铺配置的模型服务；实际服务商由商家后台的模型配置决定。规则测试模式不发送给模型。',
        '请勿发送密码、支付信息、完整身份证信息或其他无关敏感资料。AI 回答可能出错，请核对引用；网页不执行支付或退款。',
        '访客凭据在当前标签页保存，8小时有效。关闭标签页或凭据过期后可能无法找回咨询；商家回复不会发送短信或邮件。',
        (f"已启用清理：无有效登录凭据、至少 {value['days']} 天没有活动且全部工单已关闭的访客咨询，会定时分批删除；待处理或已回复未关闭工单保留。" if value['enabled'] else '自动咨询清理尚未启用，当前咨询会继续保留。商家需在上线前确定保留安排。'),
        '清理只作用于本程序数据库，不能删除模型服务已有记录或历史备份。涉及个人资料的查询或删除，请通过联系人工客服提出，商家需核验后处理。'],
        'retention':{'enabled':value['enabled'],'days':value['days']}}


def acknowledge(user_id,version):
    with connect('core') as db:db.execute('INSERT OR REPLACE INTO privacy_ack VALUES(?,?,?)',(user_id,version,now()))


def settings():
    with connect('core') as db:
        row=db.execute('SELECT * FROM retention_settings WHERE id=1').fetchone()
        last=db.execute('SELECT * FROM retention_runs ORDER BY id DESC LIMIT 1').fetchone()
    return {'enabled':bool(row['enabled']),'days':row['days'],'revision':row['revision'],'last_run':dict(last) if last else None}


def save(value,actor):
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT revision FROM retention_settings WHERE id=1').fetchone()
        if row[0]!=value.expected_revision:raise BusinessError('REVISION_CONFLICT','清理设置已变化，请刷新。')
        db.execute('UPDATE retention_settings SET enabled=?,days=?,revision=revision+1 WHERE id=1',(value.enabled,value.days))
        db.execute('INSERT INTO operating_audit(actor,body,created_at) VALUES(?,?,?)',(actor,json.dumps({'retention':value.model_dump()}),now()))
    return settings()


def eligible(db,days):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    return [r[0] for r in db.execute("""SELECT s.id FROM sessions s JOIN users u ON s.user_id=u.id
    WHERE u.role='buyer' AND substr(u.id,1,6)='guest_' AND julianday(s.created_at)<julianday(?)
    AND NOT EXISTS(SELECT 1 FROM tokens t WHERE t.user_id=s.user_id AND t.expires_at>?)
    AND NOT EXISTS(SELECT 1 FROM tickets t WHERE t.session_id=s.id AND (t.status!='已关闭' OR julianday(t.created_at)>=julianday(?)))
    AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.session_id=s.id AND julianday(m.created_at)>=julianday(?))
    AND NOT EXISTS(SELECT 1 FROM ticket_events e JOIN tickets t ON e.ticket_id=t.id WHERE t.session_id=s.id AND julianday(e.created_at)>=julianday(?))
    AND NOT EXISTS(SELECT 1 FROM ticket_replies r JOIN tickets t ON r.ticket_id=t.id WHERE t.session_id=s.id AND julianday(r.created_at)>=julianday(?))
    ORDER BY s.created_at LIMIT 200""",(cutoff,time.time(),cutoff,cutoff,cutoff,cutoff))]


def preview(busy=()):
    value=settings()
    with connect('core') as db:
        ids=[sid for sid in eligible(db,value['days']) if sid not in busy]
        expired=db.execute('SELECT count(*) FROM tokens WHERE expires_at<=?',(time.time(),)).fetchone()[0]
    return {**value,'eligible_sessions':len(ids),'expired_tokens':expired,'batch_limit':200}


def cleanup(revision,actor='scheduler',busy=()):
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        value=db.execute('SELECT * FROM retention_settings WHERE id=1').fetchone()
        if value['revision']!=revision:raise BusinessError('REVISION_CONFLICT','清理设置已变化，请刷新。')
        if not value['enabled']:raise BusinessError('CLEANUP_DISABLED','先确认保留期限并启用清理。')
        ids=[sid for sid in eligible(db,value['days']) if sid not in busy]
        for sid in ids:
            db.execute('DELETE FROM ticket_replies WHERE ticket_id IN (SELECT id FROM tickets WHERE session_id=?)',(sid,))
            db.execute('DELETE FROM ticket_events WHERE ticket_id IN (SELECT id FROM tickets WHERE session_id=?)',(sid,))
            db.execute('DELETE FROM tickets WHERE session_id=?',(sid,))
            db.execute('DELETE FROM messages WHERE session_id=?',(sid,))
            db.execute('DELETE FROM sessions WHERE id=?',(sid,))
        tokens=db.execute('DELETE FROM tokens WHERE expires_at<=?',(time.time(),)).rowcount
        orphan="SELECT id FROM users u WHERE role='buyer' AND substr(id,1,6)='guest_' AND NOT EXISTS(SELECT 1 FROM tokens t WHERE t.user_id=u.id) AND NOT EXISTS(SELECT 1 FROM sessions s WHERE s.user_id=u.id) AND NOT EXISTS(SELECT 1 FROM tickets t WHERE t.user_id=u.id)"
        db.execute('DELETE FROM privacy_ack WHERE user_id IN ('+orphan+')')
        users=db.execute('DELETE FROM users WHERE id IN ('+orphan+')').rowcount
        db.execute('INSERT INTO retention_runs(created_at,actor,sessions,users,tokens) VALUES(?,?,?,?,?)',(now(),actor,len(ids),users,tokens))
    return {'sessions':len(ids),'users':users,'tokens':tokens}


CODES={'MODEL_TIMEOUT','MODEL_UNAVAILABLE','MODEL_AUTH_FAILED','MODEL_RATE_LIMIT','MODEL_INVALID_RESPONSE','MODEL_OUTPUT_LIMIT','MODEL_DAILY_LIMIT','AI_PAUSED','AGENT_TIMEOUT','AGENT_STEP_LIMIT','HTTP_UNEXPECTED','MAINTENANCE_FAILED'}

def record(code):
    safe=code if code in CODES else 'OTHER_BUSINESS_FAILURE'
    with connect('core') as db:
        db.execute('INSERT INTO operational_events(created_at,code) VALUES(?,?)',(now(),safe))
        # 只保存最近1000条无内容的运行事件，避免无界增长。
        db.execute('DELETE FROM operational_events WHERE id NOT IN (SELECT id FROM operational_events ORDER BY id DESC LIMIT 1000)')


def report():
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
    with connect('core') as db:
        groups=[dict(r) for r in db.execute('SELECT code,count(*) AS count FROM operational_events WHERE created_at>=? GROUP BY code',(cutoff,))]
        recent=[dict(r) for r in db.execute('SELECT created_at,code FROM operational_events ORDER BY id DESC LIMIT 20')]
    return {'window_hours':24,'counts':groups,'recent':recent,'notice':'只记录错误类别和时间，不保存问题、回复、密钥或身份；没有外部告警推送。'}
