"""商品咨询访客边界。宿主与 Agent 目录共用，身份不能由模型指定。"""
PUBLIC_TOOLS = frozenset({
    'product.search_products', 'product.get_product_detail', 'product.check_inventory', 'product.compare_prices',
    'knowledge.search_policy', 'recommend.get_recommendations', 'recommend.query_promotions', 'recommend.calculate_discount',
})


def is_guest(identity):
    return identity.role == 'buyer' and identity.user_id.startswith('guest_')
