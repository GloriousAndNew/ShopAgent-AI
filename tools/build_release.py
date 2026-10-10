"""生成不含本机配置、秘密及业务数据的源码部署包（仅使用 Python 标准库）。"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = {'shopagent', 'deploy', 'docs', 'tests', 'tools', 'learning_examples'}
ROOT_FILES = {'.env.example', '.gitignore', '.gitattributes', 'README.md',
              'pyproject.toml', 'requirements.txt', 'requirements.lock.txt'}
SECRET_PATTERN = re.compile(rb'(?:sk-[A-Za-z0-9_-]{25,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def allowed(path):
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or '\\' in path:
        return False
    if p.name == '.env.example' or p.name.endswith('.env.example'):
        return p.parts[0] in PREFIXES or path in ROOT_FILES
    if any(part.startswith('.env') or part in {'__pycache__', '.venv', 'data', 'data-production', 'backups', 'test-results', 'dist'} for part in p.parts):
        return False
    if p.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.log', '.pyc', '.pem', '.key', '.p12', '.pfx', '.env'} or p.name.endswith(('.db-wal', '.db-shm')):
        return False
    return p.parts[0] in PREFIXES or path in ROOT_FILES or (len(p.parts) == 1 and p.suffix == '.cmd')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def collect():
    # 已跟踪文件加新增未忽略文件；仍须通过允许目录及敏感内容检查。
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode('utf-8').split('\0')
    payload = {}
    secrets = []
    for env_name in ('.env', '.env.production'):
        env_path = ROOT / env_name
        if env_path.exists():
            for line in env_path.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.strip().partition('=')
                value = value.strip().strip('\"\'')
                if sep and any(word in key.upper() for word in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET')) and len(value) >= 12:
                    secrets.append(value.encode())
    for name in sorted(set(names)):
        if not name or not allowed(name):
            continue
        path = ROOT / name
        if path.is_symlink() or ROOT not in path.resolve().parents or not path.is_file():
            raise ValueError('部署文件必须是项目内的普通文件：' + name)
        data = path.read_bytes()
        if SECRET_PATTERN.search(data) or any(value in data for value in secrets):
            raise ValueError('疑似秘密内容，停止打包（不显示内容）：' + name)
        if path.suffix in {'.py', '.js', '.css', '.html', '.md', '.toml', '.template', '.service', '.example', '.txt'}:
            data = data.replace(b'\r\n', b'\n')
        payload[name] = data
    # 根目录开发配置可能启用了付费模型。部署包强制从无需密钥的规则模式开始。
    payload['agent.toml'] = b'[agent]\nmode = "mock"\n'
    required = {'shopagent/run.py', 'shopagent/admin_init.py', 'requirements.lock.txt', 'deploy/shopagent.service', 'deploy/README.md'}
    if not required <= payload.keys():
        raise ValueError('部署包缺少必要文件：' + ', '.join(sorted(required - payload.keys())))
    return payload


def build(output):
    payload = collect()
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    commit = git('rev-parse', 'HEAD').decode().strip()
    dirty = bool(git('status', '--porcelain').strip())
    name = 'ShopAgent-AI-' + project['version'] + '-' + commit[:7] + ('-working' if dirty else '')
    manifest = {'project': 'ShopAgent-AI', 'version': project['version'], 'source_commit': commit,
                'working_tree_changes': dirty, 'initial_model_mode': 'mock',
                'files': {k: {'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k, v in sorted(payload.items())}}
    payload['RELEASE-MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / (name + '.zip'), output / (name + '.tar.gz')]
    if any(p.exists() for p in targets):
        raise ValueError('同名部署包已存在；请使用新的输出目录，避免覆盖。')
    with zipfile.ZipFile(targets[0], 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(payload.items()):
            item = zipfile.ZipInfo(name + '/' + path, date_time=(2026, 10, 10, 0, 0, 0))
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED)
    with targets[1].open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for path, data in sorted(payload.items()):
                item = tarfile.TarInfo(name + '/' + path)
                item.size, item.mode, item.mtime = len(data), 0o644, 0
                archive.addfile(item, io.BytesIO(data))
    checksum = output / (name + '.SHA256SUMS.txt')
    checksum.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in targets), encoding='utf-8')
    print(json.dumps({'archives': [str(p.resolve()) for p in targets], 'checksums': str(checksum.resolve()), 'files': len(payload)}, ensure_ascii=False))
    return targets


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    try:
        build(parser.parse_args().output.resolve())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
