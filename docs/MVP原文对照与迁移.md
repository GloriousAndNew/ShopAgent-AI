# 原文对照与代码迁移

依据：用户提供的全站 HTML 中 ShopAgent AI 项目正文（p0021），已提取到 `ShopAgent-AI-原始项目正文.txt`。本文件解释当前 MVP，原文中的效果数字和简历表述不作为本系统实测结果。

## 原文与当前边界

| 原文设计 | 当前 MVP | 后续生产实现 |
|---|---|---|
| 主 Agent 识别意图并分派 | 模型生成最多4步任务，程序验证角色白名单，串行执行并汇总 | 更完整的意图评测、失败重规划 |
| ProductAgent | 独立提示、工具权限和执行上下文；商品、库存、店内比价 | 真实商品平台、跨平台价格来源 |
| OrderAgent | 本人订单、物流、售后政策、草稿；提交由顾客确认 | 店铺订单权限映射、真实售后接口 |
| RecommendAgent | 本人资料、预算筛选、活动查询、整数分优惠计算 | 授权后的真实会员偏好与运营活动 |
| Planning + ReAct | 主客服拆分任务；专业助手循环查工具；无独立第五个角色 | 更复杂售后任务的持久化计划 |
| A2A | python-a2a SDK 消息编解码、关联会话、主客服本地派发 | **尚无跨进程 A2A 网络服务/AgentCard 发现，不能宣称完成分布式 A2A** |
| MCP / FastMCP | 官方 MCP SDK Server + ClientSession，真实 initialize/list_tools/call_tool，本地内存传输 | 独立 HTTP/stdio MCP 服务与外部客户端认证 |
| MySQL | 迁移保留现有 SQLite，不丢失已生成会话和申请 | 按真实订单平台或数据库适配，生产迁移与备份 |
| Docker / 上线 | 本机运行 | 公网 HTTPS、正式账户、限流、日志、部署和备份 |

MVP 保留原文业务角色和协作顺序；没有把“模块数”说成“Agent 数”。真实模式中主客服加三个专业角色，共4个。演示规则模式仍保留旧确定性路由，用于无需密钥体验和回归测试，不声称运行多模型 Agent。

## 原文13个工具对应

| 原文名称 | 当前实现 | 说明 |
|---|---|---|
| search_products | product.search_products | 关键词、预算、降噪条件 |
| get_product_detail | product.get_product_detail | 详情和规格 |
| check_inventory | product.check_inventory | 指定颜色 SKU |
| compare_prices | product.compare_prices | 2至5个本店商品，不声称跨平台比价 |
| get_order_status | order.get_order_status | 当前登录顾客所有权验证 |
| track_logistics | logistics.track_logistics | 保留现有独立物流模块，订单 Agent 使用 |
| apply_return | aftersale.prepare_return + 页面确认接口 | 原文一步操作拆成草稿/确认，防止模型直接提交 |
| get_return_status | aftersale.get_return_status | 本人申请 |
| get_user_profile | recommend.get_user_profile | 本人资料，不猜测偏好 |
| get_recommendations | recommend.get_recommendations | 条件筛选最多3款，非训练推荐模型 |
| query_promotions | recommend.query_promotions | 明确标注的示例活动 |
| calculate_discount | recommend.calculate_discount | 查询商品标价后按整数分计算 |
| save_shopping_record | recommend.save_shopping_record | 与画像共用导购库，记录归属当前用户；未给模型自动写入权限 |

补充保留：本人订单列表、政策引用、人工咨询、人工回复。原文对会员意图和 Planning 命名有不一致之处，本版统一为导购职责和主客服规划职责。

## 直接迁移的代码

| 代码 | 如何复用 |
|---|---|
| core/agent_config.py、llm_provider.py | 原 DeepSeek 配置读取、密钥隔离、HTTP模型适配和错误处理保留 |
| core/llm_agent.py | 原单 Agent 工具循环改为可指定角色提示、工具白名单、是否保存聊天；三个专业角色共用执行器 |
| core/registry.py、plugins/worker.py | 现有独立进程、健康检查、在途排空、版本兼容和故障处理保留 |
| plugins/product/order/logistics/knowledge/aftersale | 商品、订单、权限、政策、确认防重业务保留；商品新增比价 |
| core/sessions.py、security.py | 登录、会话归属、人工回复、内部调用签名继续使用 |
| web/app.js | 复用登录、会话、卡片、售后确认；新增真实协作记录 |
| data、.env、agent.toml | 原地保留，未重置已有数据与模型密钥 |

新增代码在 `agents/` 和 `plugins/recommend/`。旧 Orchestrator 仍承接 API 和演示规则模式；真实模式交给新 MainAgent。

## 热插拔说明

工具模块是独立进程，可以在管理员后台启停。导购依赖商品；物流依赖订单；售后依赖订单和政策。禁止直接停用仍有依赖方的模块，或选择级联停用。Agent 工具目录按每轮健康快照生成，停用的工具不会提供给后续请求。

专业 Agent 当前在宿主进程内按请求运行，共用模型连接配置；不是三个独立部署的远程服务。配置角色或新增角色需要改代码并重启。跨机器 A2A 和 Agent 角色运行时装卸属于后续扩展，不混称为已经完成。

## 上线前剩余顺序

1. 对接真实商品与政策，用真实资料验证回答。
2. 对接真实用户身份、订单和物流，保持所有权检查。
3. 选定要开放的售后范围，真实提交依然要求确认、幂等和审计。
4. 增加正式账户、登录限流、咨询限流、密钥管理、备份和监控。
5. 部署 HTTPS 独立客服网页，配置域名并从外网验收。不要公开示例账户版本。
6. 有多进程/跨机器协作需求时，再补标准网络 A2A 和独立 MCP 服务。

需要平台账号或服务器才能完成公网部署。本轮交付是重构后的本地可运行 MVP，不是已上线的真实商铺客服。
