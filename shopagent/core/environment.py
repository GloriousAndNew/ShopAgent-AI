import json
from shopagent import settings
from shopagent.contracts.models import BusinessError


def production():
    return settings.RUNTIME_ENV=='production'


def ensure_directory(directory):
    """在读写数据库之前检查环境标记，禁止把开发库当生产库使用。"""
    if production() and directory.resolve()==(settings.ROOT/'data').resolve():
        raise BusinessError('ENVIRONMENT_MISMATCH','生产模式不能使用原演示data目录，请使用独立目录。')
    marker=directory/'.environment.json'
    if marker.exists():
        try:
            value=json.loads(marker.read_text(encoding='utf-8'))
            if value.get('environment')!=settings.RUNTIME_ENV:
                raise ValueError()
        except (OSError,ValueError,AttributeError):
            raise BusinessError('ENVIRONMENT_MISMATCH','数据目录的环境标记不匹配，拒绝启动；不要修改标记来迁移演示库。') from None
        return
    if production() and directory.exists() and any(directory.iterdir()):
        raise BusinessError('ENVIRONMENT_MISMATCH','生产数据目录须为空或已有production标记，不能直接使用旧数据库。')
    directory.mkdir(parents=True,exist_ok=True)
    try:
        with marker.open('x',encoding='utf-8') as stream:
            json.dump({'environment':settings.RUNTIME_ENV},stream)
    except FileExistsError:
        ensure_directory(directory)


PRODUCTION_MODULES=frozenset({'product','knowledge','recommend'})
