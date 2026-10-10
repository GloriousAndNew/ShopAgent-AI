"""兼容新版样例：保留工具 Schema，增加可核查的构建信息。"""
from shopagent.plugins.product import service

initialize = service.initialize


async def search(params, request):
    result = await service.search(params, request)
    result["catalog_build"] = "1.1.0"
    return result


TOOLS = dict(service.TOOLS)
TOOLS["search_products"] = (service.Search, search, service.TOOLS["search_products"][2])
