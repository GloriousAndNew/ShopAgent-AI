import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ENV = os.getenv('SHOPAGENT_ENV','development')
if RUNTIME_ENV not in ('development','production'):
    raise ValueError('SHOPAGENT_ENV 只能为 development 或 production。')
DATA = Path(os.getenv("SHOPAGENT_DATA_DIR", str(ROOT / ('data-production' if RUNTIME_ENV=='production' else 'data')))).resolve()
HOST_URL = os.getenv("SHOPAGENT_HOST_URL", "http://127.0.0.1:8000")
DEFAULT_VERSION = "1.0.0"
CONTRACT_VERSION = "1"
PLUGINS = {
    "product": {"module": "shopagent.plugins.product.service", "label": "商品中心", "dependencies": []},
    "order": {"module": "shopagent.plugins.order.service", "label": "订单中心", "dependencies": []},
    "knowledge": {"module": "shopagent.plugins.knowledge.service", "label": "政策知识", "dependencies": []},
    "logistics": {"module": "shopagent.plugins.logistics.service", "label": "物流跟踪", "dependencies": ["order"]},
    "aftersale": {"module": "shopagent.plugins.aftersale.service", "label": "售后申请", "dependencies": ["order", "knowledge"]},
    "recommend": {"module": "shopagent.plugins.recommend.service", "label": "导购与优惠", "dependencies": ["product"]},
    "demo": {"module": "shopagent.plugins.demo.service", "label": "扩展演示", "dependencies": []},
}
