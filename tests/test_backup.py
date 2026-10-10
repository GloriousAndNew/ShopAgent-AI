import json
import sqlite3
import pytest
from shopagent import backup


@pytest.fixture
def data(tmp_path):
    source=tmp_path/'source';source.mkdir()
    (source/'.environment.json').write_text(json.dumps({'environment':backup.RUNTIME_ENV}),encoding='utf-8')
    with sqlite3.connect(source/'core.db') as db:
        db.execute('CREATE TABLE tokens(hash TEXT)');db.execute("INSERT INTO tokens VALUES('expired-test-token')")
        db.execute('CREATE TABLE messages(id INTEGER, content TEXT)');db.execute("INSERT INTO messages VALUES(1,'人工回复测试')")
    with sqlite3.connect(source/'product.db') as db:
        db.execute('CREATE TABLE items(name TEXT)');db.execute("INSERT INTO items VALUES('测试商品')")
    (source/'.env').write_text('not-for-backup',encoding='utf-8')
    (source/'host.log').write_text('not-for-backup',encoding='utf-8')
    return source


def test_backup_restore_integrity_and_logout(data,tmp_path):
    snapshot=tmp_path/'snapshot';restored=tmp_path/'restored'
    result=backup.backup(data,snapshot)
    assert set(result['files'])=={'.environment.json','core.db','product.db'}
    assert not (snapshot/'.env').exists() and not (snapshot/'host.log').exists()
    backup.verify(snapshot);backup.restore(snapshot,restored)
    with sqlite3.connect(restored/'core.db') as db:
        assert db.execute('SELECT count(*) FROM tokens').fetchone()[0]==0
        assert db.execute('SELECT content FROM messages').fetchone()[0]=='人工回复测试'
        assert json.loads(db.execute('SELECT body FROM operating_settings').fetchone()[0])['ai_enabled'] is False
    with sqlite3.connect(data/'core.db') as db:assert db.execute('SELECT count(*) FROM tokens').fetchone()[0]==1


def test_running_host_refused(data,tmp_path):
    (data/'host.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError):backup.backup(data,tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()


def test_never_overwrites_existing_directory(data,tmp_path):
    snapshot=tmp_path/'snapshot';backup.backup(data,snapshot)
    with pytest.raises(FileExistsError):backup.backup(data,snapshot)
    with pytest.raises(FileExistsError):backup.restore(snapshot,data)


@pytest.mark.parametrize('damage',['checksum','path','environment'])
def test_corrupted_backup_refused_before_restore(data,tmp_path,damage):
    snapshot=tmp_path/'snapshot';backup.backup(data,snapshot)
    manifest=json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    if damage=='checksum':(snapshot/'product.db').write_bytes(b'corrupt')
    elif damage=='path':manifest['files']['../private.db']='bad'
    else:manifest['environment']='production' if backup.RUNTIME_ENV=='development' else 'development'
    (snapshot/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError):backup.restore(snapshot,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_backup_live_wal_snapshot(data,tmp_path):
    db=sqlite3.connect(data/'product.db')
    try:
        db.execute('PRAGMA journal_mode=WAL');db.execute("INSERT INTO items VALUES('WAL商品')");db.commit()
        backup.backup(data,tmp_path/'snapshot')
        with sqlite3.connect(tmp_path/'snapshot/product.db') as copy:assert copy.execute('SELECT count(*) FROM items').fetchone()[0]==2
    finally:db.close()
