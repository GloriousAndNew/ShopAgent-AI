"""演示模式用确定性路由模拟模型；不将它描述为真正 LLM。"""
import re
from decimal import Decimal, InvalidOperation
from shopagent.contracts.models import StrictModel


class Decision(StrictModel):
    intent: str
    params: dict = {}


class MockModelProvider:
    mode = "mock"

    def classify(self, message, context):
        text = message.strip()
        oid = re.search(r"O\d{4}", text, re.I)
        order_id = oid[0].upper() if oid else context.get("order_id")
        rid = re.search(r"RT-[A-Z0-9]+", text, re.I)
        if "取消" in text and context.get("pending_return"):
            return Decision(intent="return_cancel_collect", params={})
        if "转人工" in text or "人工客服" in text:
            return Decision(intent="handoff", params={})
        if rid or ("售后进度" in text and context.get("return_id")):
            return Decision(intent="return_status", params={"return_id": rid[0].upper() if rid else context["return_id"]})
        if any(word in text for word in ("政策", "规则", "能退", "可以退", "拆封以后", "拆封后", "保修", "运费", "发票", "优惠", "退款多久到账")) and not any(word in text for word in ("申请退", "帮我退")):
            return Decision(intent="policy_query", params={"question": text})
        if "我的订单" == text or "订单列表" in text or "最近订单" in text:
            return Decision(intent="order_list", params={})
        pending = context.get("pending_return", {})
        if any(word in text for word in ("退货", "申请售后", "申请退", "帮我退", "换货")) or pending:
            if "换货" in text:
                return Decision(intent="clarify", params={"reply": "本版只演示退货申请，换货可以点击转人工创建模拟工单。"})
            values = dict(pending)
            values["order_id"] = order_id or pending.get("order_id")
            if "未拆封" in text or "没拆封" in text:
                values["opened"] = False
            elif "已拆封" in text or "拆过" in text:
                values["opened"] = True
            if any(word in text for word in ("质量", "没声音", "无声音", "坏了", "故障")) and not any(word in text for word in ("非质量", "无质量", "没有质量")):
                values["quality_issue"] = True
                values["reason"] = text
            elif any(word in text for word in ("不喜欢", "不想要", "非质量", "无理由")):
                values["quality_issue"] = False
                values["reason"] = text
            missing = []
            if not values.get("order_id"):
                missing.append("订单号（例如 O1002）")
            if "opened" not in values:
                missing.append("是否已拆封（回复已拆封或未拆封）")
            if "quality_issue" not in values:
                missing.append("原因（例如质量问题或不喜欢）")
            if missing:
                return Decision(intent="return_collect", params={"values": values, "reply": "请补充：" + "、".join(missing) + "。确认信息后我只生成草稿，不会直接提交。"})
            return Decision(intent="return_apply", params=values)
        if any(word in text for word in ("物流", "快递", "到哪", "什么时候到")):
            return Decision(intent="logistics_track", params={"order_id": order_id})
        if any(word in text for word in ("订单", "发货", "签收")) or oid:
            return Decision(intent="order_query", params={"order_id": order_id})
        product_ids = context.get("product_ids", [])
        pid_match = re.search(r"P\d{3}", text, re.I)
        product_id = pid_match[0].upper() if pid_match else context.get("product_id")
        ordinal = re.search(r"第([一二三四五六七八九十1-9])(?:款|个|件)", text)
        if ordinal:
            chars = "一二三四五六七八九十"
            index = chars.index(ordinal[1])+1 if ordinal[1] in chars else int(ordinal[1])
            product_id = product_ids[index-1] if index <= len(product_ids) else None
        if any(word in text for word in ("库存", "有货", "还有货")):
            return Decision(intent="inventory_check", params={"product_id": product_id, "color": "白色" if "白色" in text else "黑色"})
        if "详情" in text or "规格" in text or pid_match:
            return Decision(intent="product_detail", params={"product_id": product_id})
        keywords = ("耳机", "鼠标", "键盘", "收纳包", "水杯", "保温杯", "围巾")
        keyword = next((word for word in keywords if word in text), "")
        budget = re.search(r"(?:预算\s*)?(\d+(?:\.\d{1,2})?)\s*(?:元|块)?\s*(?:以内|以下|内|预算)", text)
        if not budget:
            budget = re.search(r"预算\s*(\d+(?:\.\d{1,2})?)", text)
        if re.search(r"-\d+\s*(?:元|以内)", text):
            return Decision(intent="clarify", params={"reply": "预算不能为负数，请输入有效预算。"})
        if keyword or budget or "商品" in text or "推荐" in text:
            price = int(Decimal(budget[1]) * 100) if budget else 10000000
            return Decision(intent="product_query", params={"keyword": keyword, "max_price_fen": price, "noise_cancel": "降噪" in text})
        if "取消" in text and pending:
            return Decision(intent="return_cancel_collect", params={})
        return Decision(intent="clarify", params={"reply": "当前是无需密钥的演示模式。可以问商品、订单、物流或售后政策；试试“200 元以内的降噪耳机”或“查订单 O1001”。未识别的问法可点击转人工。"})
