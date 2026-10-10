from shopagent.core.llm_agent import ALLOWED_TOOLS

ROLES = {
    "ProductAgent": {
        "label": "商品 Agent", "job": "查商品、比较标价、核实颜色库存",
        "tools": {"product.search_products", "product.get_product_detail", "product.check_inventory", "product.compare_prices", "knowledge.search_policy"},
        "prompt": "你是 ProductAgent。只负责商品事实、库存和商品相关政策；比较必须查询 compare_prices。不得代替订单或导购 Agent。",
    },
    "OrderAgent": {
        "label": "订单 Agent", "job": "查本人订单、物流、售后；退货只生成待确认草稿",
        "tools": {"order.list_orders", "order.get_order_status", "logistics.track_logistics", "aftersale.prepare_return", "aftersale.get_return_status", "knowledge.get_return_rule", "knowledge.search_policy"},
        "prompt": "你是 OrderAgent。负责当前顾客的订单、物流和售后政策。禁止猜测订单归属，退货只生成草稿，不能提交或执行退款。",
    },
    "RecommendAgent": {
        "label": "导购 Agent", "job": "了解偏好、筛选候选、查询活动、计算优惠",
        "tools": {"recommend.get_user_profile", "recommend.get_recommendations", "recommend.query_promotions", "recommend.calculate_discount", "knowledge.search_policy"},
        "prompt": "你是 RecommendAgent。先查画像或顾客条件，再筛选候选商品；不推测个人偏好。优惠先查活动再调用计算工具。库存只能交给 ProductAgent 核实，不用标价推测优惠价。",
    },
}


def overview(manager):
    available = manager.snapshot()["tools"]
    return [{"id": name, "label": role["label"], "job": role["job"], "tools": sorted(role["tools"]),
             "available_tools": sorted(role["tools"] & available.keys())} for name, role in ROLES.items()]
