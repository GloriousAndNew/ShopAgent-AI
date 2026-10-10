from tools.build_release import allowed


def test_release_excludes_credentials_data_and_path_escape():
    for path in ['.env', '.env.production', 'data/core.db', 'docs/a.pem', 'deploy/model.env',
                 'shopagent/__pycache__/a.pyc', '../README.md', '/README.md', 'data-production/a.json',
                 'docs/local.db-wal', 'backups/a.zip', 'docs/../.env', 'docs\\a.md', 'dist/a.zip']:
        assert not allowed(path), path


def test_release_keeps_source_and_only_empty_env_templates():
    for path in ['shopagent/run.py', 'deploy/model.env.example', '.env.example', 'docs/使用说明.md',
                 'requirements.lock.txt', 'tools/build_release.py', '启动正式ShopAgent.cmd']:
        assert allowed(path), path
