"""学习列表、字典、循环、条件、函数。金额以整数分保存。"""

PRODUCTS = [
    {"id": "P001", "name": "降噪耳机", "price_fen": 18900},
    {"id": "P002", "name": "无线鼠标", "price_fen": 9900},
    {"id": "P003", "name": "机械键盘", "price_fen": 29900},
]


def search_products(max_price_fen):
    """遍历商品，返回不超过预算的商品。"""
    results = []
    for product in PRODUCTS:
        if product["price_fen"] <= max_price_fen:
            results.append(product)
    return results


if __name__ == "__main__":
    budget_fen = 20000  # 200 元；可改成 10000，观察结果。
    matches = search_products(budget_fen)
    print(f"预算：{budget_fen / 100:.2f} 元")
    for product in matches:
        print(f"{product['name']}：{product['price_fen'] / 100:.2f} 元")
    print(f"共找到 {len(matches)} 件商品")
