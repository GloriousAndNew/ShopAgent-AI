"""商品管理只在商品模块内读写。通用工具入口同样执行管理员检查。"""
import json
from typing import Literal
from pydantic import Field, field_validator, model_validator, ValidationError
from shopagent.contracts.models import StrictModel, BusinessError
from shopagent.storage import connect, now


class SKUWrite(StrictModel):
    color: str = Field(min_length=1, max_length=20, pattern=r"^[\w\u4e00-\u9fff .-]+$")
    stock: int = Field(ge=0, le=1000000, strict=True)

    @field_validator("color")
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError("颜色不能为空")
        return value.strip()


class ProductWrite(StrictModel):
    product_id: str = Field(pattern=r"^P\d{3}$")
    expected_revision: int = Field(default=0, ge=0, strict=True)
    name: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    price_fen: int = Field(gt=0, le=10000000, strict=True)
    noise_cancel: bool = Field(default=False, strict=True)
    description: str = Field(default="", max_length=3000)
    specification: str = Field(default="", max_length=1500)
    brand: str = Field(default="", max_length=80)
    unknown_fields: list[str] = Field(default_factory=list, max_length=20)
    warranty_months: int | None = Field(default=None, ge=0, le=120, strict=True)
    status: Literal["draft", "published", "archived"] = "draft"
    simulation: bool = Field(default=True, strict=True)
    skus: list[SKUWrite] = Field(min_length=1, max_length=20)

    @field_validator("name", "category", "brand", "specification", "description")
    @classmethod
    def trim(cls, value, info):
        value = value.strip()
        if info.field_name in ("name", "category") and not value:
            raise ValueError("名称和分类不能为空")
        return value

    @model_validator(mode="after")
    def unique_colors(self):
        if len({s.color for s in self.skus}) != len(self.skus):
            raise ValueError("同一商品不能重复颜色")
        if any(not x.strip() or len(x)>80 for x in self.unknown_fields):
            raise ValueError("未知字段应为简短名称")
        return self


class CatalogQuery(StrictModel):
    keyword: str = Field(default="", max_length=80)
    status: Literal["all", "draft", "published", "archived"] = "all"


class ProductID(StrictModel):
    product_id: str = Field(pattern=r"^P\d{3}$")


class StatusWrite(ProductID):
    status: Literal["draft", "published", "archived"]
    expected_revision: int = Field(ge=1, strict=True)


class ImportBatch(StrictModel):
    products: list[dict] = Field(min_length=1, max_length=100)


def require_admin(req):
    if req.identity.role != "admin":
        raise BusinessError("FORBIDDEN", "只有商家管理员可以管理商品。")


def read_product(db, pid):
    from shopagent.plugins.product.service import serialize
    row = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not row:
        raise BusinessError("NOT_FOUND", "商品不存在。")
    value = serialize(row)
    value["skus"] = [dict(r) for r in db.execute("SELECT * FROM skus WHERE product_id=? ORDER BY color", (pid,))]
    return value


def audit(db, req, pid, action, before, after):
    db.execute("INSERT INTO product_audit(product_id,actor,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)",
               (pid, req.identity.user_id, action, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), now()))


def write_product(db, value, req):
    from shopagent.core.environment import production
    if production() and value.status=='published' and value.simulation:
        raise BusinessError('SAMPLE_NOT_PUBLISHABLE','示例商品不能在生产模式上架，请核实真实资料并取消示例标记。')
    row = db.execute("SELECT revision FROM products WHERE id=?", (value.product_id,)).fetchone()
    revision = row["revision"] if row else 0
    if revision != value.expected_revision:
        raise BusinessError("REVISION_CONFLICT", "商品已被修改或编号已存在，请刷新后再编辑，避免覆盖其他修改。")
    before = read_product(db, value.product_id) if row else None
    fields = value.model_dump(exclude={"product_id", "skus", "expected_revision"})
    fields["unknown_fields"] = json.dumps(fields["unknown_fields"], ensure_ascii=False)
    fields.update(revision=revision+1, updated_at=now())
    if row:
        db.execute("UPDATE products SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?", [*fields.values(), value.product_id])
    else:
        fields = {"id": value.product_id, **fields}
        db.execute("INSERT INTO products("+",".join(fields)+") VALUES("+",".join("?" for _ in fields)+")", list(fields.values()))
    db.execute("DELETE FROM skus WHERE product_id=?", (value.product_id,))
    for sku in value.skus:
        db.execute("INSERT INTO skus(id,product_id,color,stock,updated_at) VALUES(?,?,?,?,?)", (f"{value.product_id}-{sku.color}", value.product_id, sku.color, sku.stock, now()))
    after = read_product(db, value.product_id)
    audit(db, req, value.product_id, "edit" if row else "create", before, after)
    return after


async def catalog(params, req):
    require_admin(req)
    with connect("product") as db:
        rows = db.execute("SELECT id FROM products WHERE (name LIKE ? OR id LIKE ?) AND (?='all' OR status=?) ORDER BY id LIMIT 1000",
                          (f"%{params.keyword}%", f"%{params.keyword}%", params.status, params.status)).fetchall()
        return {"products": [read_product(db, r["id"]) for r in rows]}


async def get_product(params, req):
    require_admin(req)
    with connect("product") as db:
        return read_product(db, params.product_id)


async def save_product(params, req):
    require_admin(req)
    with connect("product") as db:
        db.execute("BEGIN IMMEDIATE")
        return write_product(db, params, req)


async def set_status(params, req):
    require_admin(req)
    with connect("product") as db:
        db.execute("BEGIN IMMEDIATE")
        before = read_product(db, params.product_id)
        from shopagent.core.environment import production
        if production() and params.status=='published' and before['simulation']:
            raise BusinessError('SAMPLE_NOT_PUBLISHABLE','示例商品不能在生产模式上架。')
        if before["revision"] != params.expected_revision:
            raise BusinessError("REVISION_CONFLICT", "商品已被修改，请刷新后再操作。")
        db.execute("UPDATE products SET status=?,revision=revision+1,updated_at=? WHERE id=?", (params.status, now(), params.product_id))
        after = read_product(db, params.product_id)
        audit(db, req, params.product_id, "status", before, after)
        return after


def validate_batch(params, db):
    values, errors, seen = [], [], set()
    for index, raw in enumerate(params.products, 1):
        try:
            value = ProductWrite.model_validate(raw)
            if value.expected_revision != 0:
                raise ValueError("导入只新增商品，不能更新已有记录")
            if value.product_id in seen:
                raise ValueError("文件内编号重复")
            seen.add(value.product_id)
            if db.execute("SELECT 1 FROM products WHERE id=?", (value.product_id,)).fetchone():
                raise ValueError("编号已存在，请使用编辑功能，不会覆盖已有商品")
            # 导入后先审核，不能凭上传文件直接发布。
            value.status = "draft"
            values.append(value)
        except ValidationError as error:
            errors.append({"row": index, "message": "；".join(".".join(map(str, e["loc"]))+"："+e["msg"] for e in error.errors(include_input=False, include_url=False))})
        except ValueError as error:
            errors.append({"row": index, "message": str(error)})
    return values, errors


async def preview_import(params, req):
    require_admin(req)
    with connect("product") as db:
        values, errors = validate_batch(params, db)
    return {"valid": not errors, "count": len(values), "errors": errors,
            "products": [v.model_dump() for v in values], "notice": "确认导入后全部保存为草稿，不替换已有商品，也不自动上架。"}


async def commit_import(params, req):
    require_admin(req)
    with connect("product") as db:
        db.execute("BEGIN IMMEDIATE")
        values, errors = validate_batch(params, db)
        if errors:
            raise BusinessError("IMPORT_INVALID", "导入失败，未写入任何商品。请重新预览并处理错误。")
        for value in values:
            write_product(db, value, req)
        return {"imported": len(values), "status": "draft", "product_ids": [v.product_id for v in values]}


ADMIN_TOOLS = {
    "admin_catalog": (CatalogQuery, catalog, "管理员查看商品与SKU，包含草稿和下架商品"),
    "admin_get_product": (ProductID, get_product, "管理员查看商品编辑资料"),
    "admin_save_product": (ProductWrite, save_product, "管理员新增或编辑商品与SKU，使用版本防覆盖"),
    "admin_set_status": (StatusWrite, set_status, "管理员上架、下架或设为草稿"),
    "admin_preview_import": (ImportBatch, preview_import, "管理员校验导入，返回逐行错误，不写入"),
    "admin_commit_import": (ImportBatch, commit_import, "管理员原子导入新商品为草稿，不覆盖旧商品"),
}
