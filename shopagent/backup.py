"""停机备份及恢复到新目录；不复制密钥、日志或宿主停止凭据。"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from shopagent.settings import DATA, RUNTIME_ENV, PLUGINS
from shopagent.storage import now

ALLOWED={'.environment.json','plugins.json','core.db',*(name+'.db' for name in PLUGINS)}


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def integrity(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise ValueError('数据库完整性检查失败：'+path.name)
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('数据库外键检查失败：'+path.name)


def environment(source):
    marker=source/'.environment.json'
    if marker.is_symlink() or not marker.is_file():raise ValueError('缺少有效的环境标记，请先正常初始化。')
    value=json.loads(marker.read_text(encoding='utf-8')).get('environment')
    if value!=RUNTIME_ENV:raise ValueError('环境不匹配，禁止将演示备份恢复为正式数据。')
    return value


def backup(source,target):
    source=Path(source).resolve();target=Path(target).resolve()
    if (source/'host.json').exists():raise ValueError('请先正常停止 ShopAgent 及所有模块，再备份。')
    if source==target or source in target.parents:raise ValueError('备份目录必须位于数据目录之外。')
    env=environment(source)
    files=[p for p in source.iterdir() if p.name in ALLOWED]
    if not (source/'core.db').is_file():raise ValueError('数据目录缺少 core.db。')
    if any(p.is_symlink() or not p.is_file() for p in files):raise ValueError('拒绝符号链接或非普通文件。')
    target.mkdir(parents=True,exist_ok=False)
    checksums={}
    for path in files:
        dest=target/path.name
        if path.suffix=='.db':
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(dest) as dst:src.backup(dst)
            integrity(dest)
        else:shutil.copyfile(path,dest)
        checksums[path.name]=digest(dest)
    if (source/'host.json').exists():raise ValueError('备份期间服务启动，备份未完成，请停机重试。')
    manifest={'format':1,'environment':env,'created_at':now(),'files':checksums}
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def verify(source):
    source=Path(source).resolve()
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format')!=1 or manifest.get('environment')!=RUNTIME_ENV:raise ValueError('备份格式或环境不匹配。')
    files=manifest.get('files')
    if not isinstance(files,dict) or not {'core.db','.environment.json'}<=files.keys() or not files.keys()<=ALLOWED:raise ValueError('备份文件清单不合法。')
    for name,checksum in files.items():
        path=source/name
        if path.is_symlink() or not path.is_file() or digest(path)!=checksum:raise ValueError('备份校验失败：'+name)
        if path.suffix=='.db':integrity(path)
    environment(source)
    return manifest


def restore(source,target):
    source=Path(source).resolve();target=Path(target).resolve()
    manifest=verify(source) # 验证后才创建目录；从不覆盖现有目录。
    if source==target or source in target.parents:raise ValueError('恢复目录必须位于备份目录之外。')
    target.mkdir(parents=True,exist_ok=False)
    for name,checksum in manifest['files'].items():
        shutil.copyfile(source/name,target/name)
        if digest(target/name)!=checksum:raise ValueError('恢复拷贝校验失败：'+name)
    # 旧网页登录凭据全部作废；管理员需重新登录，访客旧链接凭据不能继续使用。
    with sqlite3.connect(target/'core.db') as db:
        db.execute('DELETE FROM tokens')
        # 恢复点可能早于今天的调用记录。默认暂停 AI，避免旧额度立即被重用。
        from shopagent.core.operations import default_settings
        db.execute('CREATE TABLE IF NOT EXISTS operating_settings(id INTEGER PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL)')
        row=db.execute('SELECT body,revision FROM operating_settings WHERE id=1').fetchone()
        value=json.loads(row[0]) if row else default_settings()
        value['ai_enabled']=False
        db.execute('INSERT OR REPLACE INTO operating_settings VALUES(1,?,?)',(json.dumps(value,ensure_ascii=False),(row[1]+1) if row else 1))
    integrity(target/'core.db')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','verify','restore'])
    parser.add_argument('--source',type=Path,default=DATA)
    parser.add_argument('--target',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='verify':result=verify(args.source)
        else:
            if args.target is None:parser.error('backup/restore 必须指定新目录 --target')
            result=(backup if args.action=='backup' else restore)(args.source,args.target)
        print(f"完成：{args.action}，{result['environment']} 环境，{len(result['files'])} 个文件。")
    except (ValueError,OSError,sqlite3.Error) as exc:
        parser.exit(1,'操作未完成：'+str(exc)+'\n')


if __name__=='__main__':main()
