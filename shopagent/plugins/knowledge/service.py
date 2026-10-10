from pathlib import Path
from pydantic import Field
from shopagent.settings import DATA
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.storage import now
from shopagent.storage import connect
import json
from shopagent.plugins.knowledge.admin import initialize_store, effective, ADMIN_TOOLS
from shopagent.core.environment import production

POLICIES = [
    ("return", "退货申请条件", "退货 售后 拆封 质量 声音 保修", "本模拟店铺：已签收 7 天内可以申请售后。非质量原因需未拆封；质量原因可提交已拆封商品的申请，最终由模拟客服审核。保修期不等于退货期。"),
    ("refund", "退款说明", "退款 金额 到账", "本系统只创建模拟申请，不执行退款，也不承诺真实到账时间。模拟申请受理后状态为待审核。"),
    ("shipping", "发货和配送", "发货 配送 运费 物流", "演示订单提供模拟轨迹；没有预计到达时间时显示未知，不承诺次日送达。"),
    ("cancel", "取消规则", "取消 草稿", "提交前可取消申请草稿；已提交的申请不在本 MVP 中自动撤销，请创建模拟客服工单。"),
    ("exchange", "换货范围", "换货 更换", "本 MVP 仅演示退货申请，不安排真实换货。换货诉求可创建模拟客服工单。"),
    ("warranty", "保修说明", "保修 维修", "保修问题需要查看商品具体说明；本知识库没有厂家保修承诺，不将保修期视作退货资格。"),
    ("invoice", "发票说明", "发票 开票", "本 MVP 不提供开票服务，可登记模拟客服工单。"),
    ("stock", "库存说明", "库存 补货 有货", "库存以指定 SKU 查询结果为准；缺货时没有补货时间，不编造补货承诺。"),
    ("privacy", "订单可见范围", "隐私 账号 订单", "仅登录用户可查看自己的订单、售后和会话；管理员也不能通过买家工具绕过订单归属。"),
    ("promotion", "优惠说明", "优惠 促销 优惠券", "优惠以当前已发布且在有效期内的活动查询结果为准；需核实商品范围和金额门槛，不能把活动试算当作订单成交价，不自动使用不存在的优惠券。"),
]


class SearchPolicy(StrictModel):
    question: str = Field(min_length=1, max_length=500)


class GetRule(StrictModel):
    pass


def initialize():
    if production():
        initialize_store([])
        return
    directory = DATA / "policies"
    directory.mkdir(parents=True, exist_ok=True)
    for pid, title, tags, text in POLICIES:
        path = directory / f"{pid}.md"
        if not path.exists():
            path.write_text(f"id: {pid}\nversion: 1.0\nactive: true\ntitle: {title}\ntags: {tags}\n\n{text}\n", encoding="utf-8")
    initialize_store(legacy_documents())


def legacy_documents():
    docs = []
    for path in sorted((DATA / "policies").glob("*.md")):
        raw = path.read_text(encoding="utf-8-sig")
        header, content = raw.split("\n\n", 1)
        metadata = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
        docs.append({"id": metadata["id"], "version": metadata["version"], "title": metadata["title"], "tags": metadata["tags"], "text": content.strip(), "source": f"policies/{path.name}", "active": metadata.get("active") == "true"})
    return docs


def documents():
    with connect("knowledge") as db:
        docs = [json.loads(r[0]) for r in db.execute("SELECT live_json FROM policies WHERE live_json IS NOT NULL")]
    return [d for d in docs if effective(d) and (not production() or d.get('simulation') is False)]


async def search(params, req):
    ranked = []
    for doc in documents():
        score = sum(len(tag) for tag in doc["tags"].split() if tag in params.question)
        if score:
            ranked.append((score, doc))
    ranked.sort(key=lambda value: value[0], reverse=True)
    evidence=[doc for _,doc in ranked[:3]]
    return {"evidence":evidence, "simulation": any(d.get('simulation',True) for d in evidence), "method": "keyword"}


async def rule(params, req):
    docs = [d for d in documents() if d["id"] == "return"]
    if len(docs) != 1 or not docs[0].get("rule"):
        raise BusinessError("POLICY_UNAVAILABLE", "当前售后政策缺失或版本尚未支持，请转人工核验。")
    if docs[0]["rule"].get("legacy") and docs[0]["text"] != POLICIES[0][3]:
        raise BusinessError("POLICY_UNAVAILABLE", "旧文件政策与可执行规则不一致，请在后台核实并发布新规则。")
    return {**docs[0]["rule"], "policy": docs[0]}


TOOLS = {"search_policy": (SearchPolicy, search, "按关键词检索有效演示政策并返回引用"), "get_return_rule": (GetRule, rule, "获取受支持版本的确定性售后规则")}
TOOLS.update(ADMIN_TOOLS)
