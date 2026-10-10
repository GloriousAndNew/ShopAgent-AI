"""第一天的练习接口；没有登录、模型和插件热插拔。"""

from fastapi import FastAPI, Query

from learning_examples.product_filter import search_products

app = FastAPI(title="ShopAgent 入门练习")


@app.get("/health")
def health():
    return {"status": "ok", "mode": "learning"}


@app.get("/products")
def products(max_price: int = Query(default=200, ge=0, le=100000)):
    # 浏览器传入整数元，转为整数分后交给商品函数。
    matches = search_products(max_price * 100)
    return {"mode": "learning", "count": len(matches), "products": matches}
