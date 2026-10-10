"""从 ZIP 解压后的代码验证正式模式；只操作临时数据、不调用真实模型。"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile


def verify_archive(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        names = [entry.filename for entry in entries]
        assert len(names) == len(set(names)), '归档存在重复路径'
        for name in names:
            path = PurePosixPath(name)
            assert not path.is_absolute() and '..' not in path.parts and '\\' not in name, '归档路径不安全'
            assert target.resolve() in (target / name).resolve().parents, '归档路径越界'
        roots = {PurePosixPath(name).parts[0] for name in names}
        assert len(roots) == 1, '归档必须只有一个项目根目录'
        bundle.extractall(target)
    source = target / roots.pop()
    manifest = json.loads((source / 'RELEASE-MANIFEST.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()}
    assert actual == set(manifest['files']) | {'RELEASE-MANIFEST.json'}, '归档文件清单不匹配'
    for name, item in manifest['files'].items():
        data = (source / name).read_bytes()
        assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'], '归档内容校验失败：' + name
    return source, manifest


def smoke(archive):
    import httpx
    checks = []
    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    with tempfile.TemporaryDirectory(prefix='shopagent-release-') as temporary:
        base = Path(temporary)
        source, manifest = verify_archive(archive, base / 'unpacked')
        checks.append('ZIP 文件集合、大小及 SHA256 完整一致')
        data = base / 'formal-data'
        config = base / 'agent.production.toml'
        config.write_text('[agent]\nmode = "mock"\n', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if not k.startswith('SHOPAGENT_') and k != 'PYTHONPATH'}
        env.update(SHOPAGENT_ENV='production', SHOPAGENT_DATA_DIR=str(data),
                   SHOPAGENT_AGENT_CONFIG=str(config), SHOPAGENT_MODEL_API_KEY='', PYTHONUTF8='1')
        password = secrets.token_urlsafe(32) + 'Aa1'
        init_env = {**env, 'SHOPAGENT_TEST_ADMIN_PASSWORD': password}
        command = "import os; from shopagent.core.sessions import initialize_admin; initialize_admin('release_owner', os.environ['SHOPAGENT_TEST_ADMIN_PASSWORD'])"
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', command], cwd=source, env=init_env, capture_output=True)
        check(result.returncode == 0, '临时正式管理员初始化')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        url = 'http://127.0.0.1:' + str(port)
        with (base / 'host.log').open('wb') as log:
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'shopagent.run', '--environment', 'production', '--port', str(port)], cwd=source, env=env, stdout=log, stderr=log, start_new_session=os.name != 'nt')
            try:
                with httpx.Client(base_url=url, trust_env=False, timeout=25) as public:
                    for _ in range(200):
                        if process.poll() is not None:
                            raise RuntimeError('部署包服务未启动；临时正式环境验收失败')
                        try:
                            if public.get('/health').status_code == 200: break
                        except httpx.HTTPError: pass
                        time.sleep(.1)
                    else: raise RuntimeError('部署包启动超时')
                    check(public.get('/runtime').json()['environment'] == 'production', '解压代码正式启动')
                    check(all(public.get(p).status_code == 200 for p in ('/customer', '/merchant')), '顾客入口与商家入口可访问')
                    check(all(public.get(p).status_code == 404 for p in ('/docs', '/redoc', '/openapi.json')), '正式模式隐藏 API 文档')
                    check(public.post('/auth/login', json={'username': 'admin', 'password': 'demo123'}).status_code == 401, '演示管理员不能登录')
                    check(public.post('/auth/guest', json={}).status_code == 409, '未阅读说明不能创建正式访客')
                    token = public.post('/auth/login', json={'username': 'release_owner', 'password': password}).json()['token']
                    with httpx.Client(base_url=url, trust_env=False, timeout=25, headers={'Authorization': 'Bearer ' + token}) as admin:
                        check(all(admin.get('/admin/' + p).json()[p] == [] for p in ('products', 'policies', 'promotions')), '正式数据库无示例商品政策活动')
                        payload = {'product_id': 'P901', 'name': '部署包临时验收耳机', 'category': '耳机', 'price_fen': 9900, 'skus': [{'color': '黑色', 'stock': 3}], 'simulation': False, 'status': 'published'}
                        check(admin.post('/admin/products/save', json=payload).status_code == 200, '临时商品发布')
                        notice = public.get('/privacy').json()['version']
                        tokens = [public.post('/auth/guest', json={'acknowledged': True, 'notice_version': notice}).json()['token'] for _ in range(2)]
                        with httpx.Client(base_url=url, trust_env=False, timeout=25, headers={'Authorization': 'Bearer ' + tokens[0]}) as guest, httpx.Client(base_url=url, trust_env=False, timeout=25, headers={'Authorization': 'Bearer ' + tokens[1]}) as other:
                            detail = guest.post('/tools/invoke', json={'tool': 'product.get_product_detail', 'params': {'product_id': 'P901'}}).json()
                            check(detail['status'] == 'ok' and detail['data']['price_fen'] == 9900, '顾客只查询临时正式商品')
                            check(guest.get('/admin/products').status_code == 403, '顾客不能进入后台接口')
                            order = guest.post('/tools/invoke', json={'tool': 'order.list_orders', 'params': {}}).json()
                            check(order['error_code'] == 'PRODUCTION_SCOPE', '订单模块正式模式保持关闭')
                            chat = guest.post('/chat', json={'message': '查询商品 P901 的详情'}).json()
                            check(bool(chat.get('session_id')) and bool(chat.get('reply')), '规则模式聊天无需付费模型')
                            ticket = guest.post('/customer/handoff', json={'message': '部署包人工闭环验收', 'session_id': chat['session_id']}).json()
                            tid, sid = ticket['ticket']['id'], ticket['session_id']
                            reply = {'message': '这是临时验收人工回复。', 'request_key': secrets.token_hex(16)}
                            check(admin.post('/admin/tickets/' + tid + '/reply', json=reply).status_code == 200, '管理员回复人工咨询')
                            check(admin.post('/admin/tickets/' + tid + '/reply', json=reply).json()['duplicate'], '人工回复重复提交不重复写入')
                            check(any(t['id'] == tid and t['unread'] == 1 for t in guest.get('/customer/tickets').json()), '顾客看到未读人工回复')
                            check(other.get('/sessions/' + sid + '/messages').status_code == 404, '不同顾客会话相互隔离')
                            messages = guest.get('/sessions/' + sid + '/messages').json()
                            check(any(m['content'] == reply['message'] for m in messages), '原会话显示人工回复')
                            check(guest.post('/sessions/' + sid + '/read', json={'message_id': messages[-1]['id']}).status_code == 200, '顾客可标记已读')
                            check(any(t['id'] == tid and t['unread'] == 0 for t in guest.get('/customer/tickets').json()), '未读提示清零')
                            check(admin.post('/admin/tickets/' + tid + '/status', json={'expected_status': '已回复', 'status': '已关闭'}).status_code == 200, '人工咨询可关闭')
                    record = json.loads((data / 'host.json').read_text())
                    public.post('/internal/shutdown', headers={'X-Stop-Key': record['stop_key']}).raise_for_status()
                    process.wait(timeout=40)
                    check(process.returncode == 0 and not (data / 'host.json').exists(), '宿主与模块正常停止')
            finally:
                if process.poll() is None:
                    # 故障时清理本次测试的进程组，不操作用户正在运行的应用。
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=15)
        return {'status': 'passed', 'source_commit': manifest['source_commit'], 'working_tree_changes': manifest['working_tree_changes'], 'checks': checks, 'model': 'mock', 'temporary_data_removed': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = smoke(args.archive.resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding='utf-8')
    print(text)
