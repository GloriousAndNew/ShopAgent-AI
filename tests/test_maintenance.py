from datetime import datetime,timezone,timedelta
import time
import uuid
import pytest
from shopagent.core import sessions,operations,maintenance
from shopagent.contracts.models import BusinessError,Identity


@pytest.fixture
def store(tmp_path,monkeypatch):
    from shopagent import storage,settings
    monkeypatch.setattr(storage,'DATA',tmp_path);monkeypatch.setattr(settings,'RUNTIME_ENV','development')
    sessions.initialize();operations.initialize();maintenance.initialize()
    return storage


def old_session(store,status=None,owner=None):
    if owner is None:
        value=sessions.guest_login();owner=Identity(user_id=value['user']['user_id'])
    sid,_=sessions.session(owner)
    sessions.save(sid,owner,{},'测试内容不应出现在监控',{'reply':'测试回复'})
    old=(datetime.now(timezone.utc)-timedelta(days=100)).isoformat()
    with store.connect('core') as db:
        db.execute('UPDATE sessions SET created_at=? WHERE id=?',(old,sid))
        db.execute('UPDATE messages SET created_at=? WHERE session_id=?',(old,sid))
        db.execute('UPDATE tokens SET expires_at=? WHERE user_id=?',(time.time()-100,owner.user_id))
    tid=None
    if status:
        ticket=sessions.ticket(owner,sid,'测试工单');tid=ticket['id']
        with store.connect('core') as db:db.execute('UPDATE tickets SET status=?,created_at=? WHERE id=?',(status,old,tid))
    return sid,owner,tid


def enable():
    value=maintenance.settings()
    return maintenance.save(maintenance.RetentionWrite(expected_revision=value['revision'],enabled=True,days=90),'admin')


def test_disabled_by_default_preview_does_not_delete(store):
    sid,_,_=old_session(store)
    assert maintenance.preview()['eligible_sessions']==1
    with pytest.raises(BusinessError):maintenance.cleanup(1)
    with store.connect('core') as db:assert db.execute('SELECT id FROM sessions WHERE id=?',(sid,)).fetchone()


def test_cleanup_only_old_guest_and_closed_records(store):
    eligible,owner,tid=old_session(store,'已关闭')
    maintenance.acknowledge(owner.user_id,maintenance.NOTICE_VERSION)
    with store.connect('core') as db:
        db.execute('INSERT INTO ticket_replies VALUES(?,?,?,?,?)',('old-key',tid,'admin','旧回复',(datetime.now(timezone.utc)-timedelta(days=100)).isoformat()))
    protected=[]
    for status in ['待处理','已回复']:
        protected.append(old_session(store,status)[0])
    protected.append(old_session(store,owner=Identity(user_id='alice'))[0])
    protected.append(old_session(store,owner=Identity(user_id='admin',role='admin'))[0])
    revision=enable()['revision'];result=maintenance.cleanup(revision)
    assert result['sessions']==1
    with store.connect('core') as db:
        assert not db.execute('SELECT id FROM sessions WHERE id=?',(eligible,)).fetchone()
        assert not db.execute('SELECT id FROM tickets WHERE id=?',(tid,)).fetchone()
        assert not db.execute('SELECT hash FROM tokens WHERE user_id=?',(owner.user_id,)).fetchone()
        assert not db.execute('SELECT user_id FROM privacy_ack WHERE user_id=?',(owner.user_id,)).fetchone()
        for sid in protected:assert db.execute('SELECT id FROM sessions WHERE id=?',(sid,)).fetchone()
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert maintenance.settings()['last_run']['sessions']==1


@pytest.mark.parametrize('reason',['token','message','close_event','ticket','reply','busy'])
def test_recent_activity_or_busy_protected(store,reason):
    sid,owner,tid=old_session(store,'已关闭')
    with store.connect('core') as db:
        if reason=='token':db.execute('INSERT INTO tokens VALUES(?,?,?)',(uuid.uuid4().hex,owner.user_id,time.time()+3600))
        if reason=='message':db.execute('UPDATE messages SET created_at=? WHERE session_id=?',(datetime.now(timezone.utc).isoformat(),sid))
        if reason=='close_event':db.execute('INSERT INTO ticket_events(ticket_id,actor,old_status,new_status,created_at) VALUES(?,?,?,?,?)',(tid,'admin','待处理','已关闭',datetime.now(timezone.utc).isoformat()))
        if reason=='ticket':db.execute('UPDATE tickets SET created_at=? WHERE id=?',(datetime.now(timezone.utc).isoformat(),tid))
        if reason=='reply':db.execute('INSERT INTO ticket_replies VALUES(?,?,?,?,?)',('recent-key',tid,'admin','新回复',datetime.now(timezone.utc).isoformat()))
    revision=enable()['revision']
    assert maintenance.cleanup(revision,busy=[sid] if reason=='busy' else [])['sessions']==0


def test_settings_conflict_and_model_counter_untouched(store):
    operations.reserve();revision=enable()['revision']
    with pytest.raises(BusinessError):maintenance.cleanup(revision-1)
    assert operations.read()['model_calls_used']==1


def test_monitor_records_whitelist_and_no_content(store):
    maintenance.record('MODEL_TIMEOUT');maintenance.record('私密问题和密钥不能进入事件')
    value=maintenance.report()
    assert {r['code'] for r in value['counts']}=={'MODEL_TIMEOUT','OTHER_BUSINESS_FAILURE'}
    assert '私密' not in str(value)
    assert set(value['recent'][0])=={'created_at','code'}


def test_public_notice_and_admin_boundary(server,clients):
    notice=clients['alice'].get('/privacy').json()
    assert notice['version']==maintenance.NOTICE_VERSION+'-r1'
    assert '继续保留' in str(notice) and notice['retention']['enabled'] is False
    assert clients['alice'].get('/admin/maintenance').status_code==403
    assert clients['alice'].post('/admin/retention',json={'expected_revision':1,'enabled':False,'days':90}).status_code==403
    result=clients['admin'].get('/admin/maintenance')
    assert result.status_code==200,result.text
    assert result.json()['retention']['enabled'] is False
    assert clients['admin'].post('/admin/retention/cleanup',json={'expected_revision':1}).status_code==409


def test_retention_change_updates_notice_version(store):
    old=maintenance.notice()['version']
    enable()
    assert maintenance.notice()['version']!=old
    assert maintenance.notice()['retention']['enabled'] is True


def test_stale_notice_cannot_create_guest_after_settings_change(store):
    version=maintenance.current_version()
    enable()
    with pytest.raises(BusinessError):sessions.guest_login(version)
    with store.connect('core') as db:
        assert db.execute("SELECT count(*) FROM users WHERE substr(id,1,6)='guest_'").fetchone()[0]==0
    guest=sessions.guest_login(maintenance.current_version())
    with store.connect('core') as db:
        assert db.execute('SELECT version FROM privacy_ack WHERE user_id=?',(guest['user']['user_id'],)).fetchone()[0]==maintenance.current_version()
