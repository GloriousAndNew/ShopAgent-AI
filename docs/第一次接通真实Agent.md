# 第一次接通真实 Agent：新手操作指南

你现在需要完成的是“给客服接上模型”，无需先学会编程。接入代码已完成，但你还没有 API 账号，因此目前页面仍使用演示规则。

下面以 **DeepSeek** 作为一个可选起点。当前官方入门及工具调用示例使用 `deepseek-flash` 和 `https://api.deepseek.com`。项目已准备对应配置样例；尚未使用你的真实账号验证。[官方入门文档](https://api-docs.deepseek.com/en/)、[官方工具调用文档](https://api-docs.deepseek.com/guides/tool_calls/)

## 第一步：获取自己的模型 API 密钥

1. 在浏览器打开 [DeepSeek 官方开放平台](https://platform.deepseek.com/)。
2. 由你注册或登录自己的账号。
3. 在平台中找到 API 密钥管理，创建一个用于 ShopAgent 的密钥。具体菜单以当时页面为准。
4. 把密钥保存在你自己的电脑上，接下来填入 `.env`；不要发到聊天里。

API 调用按照平台规则计费，账号可用性、余额和充值由你在官方平台确认。这里只准备配置，没有注册账号、充值或调用付费模型。

## 第二步：填写密钥

项目文件夹是：`C:\Users\44332\Documents\ChatGPT\智能电商客服`。

1. 找到 `.env.example`，复制一份并改名为 `.env`。
2. 在资源管理器打开“查看 → 显示 → 文件扩展名”，确保它不是 `.env.txt`。
3. 用 VS Code 打开 `.env`，把 `your-api-key` 换成你的真实密钥，保存。

文件内容形如下面这样，等号后面填你自己的密钥：

```text
SHOPAGENT_MODEL_API_KEY=在自己电脑上填写真实密钥
```

不要修改 Python 源码，不要把密钥放到网页、截图或 `agent.toml` 中。

## 第三步：复制已准备的模型配置

打开 `docs\examples\agent.deepseek.toml`，复制全部内容，粘贴到项目根目录的 `agent.toml` 并保存。

关键配置如下，完整样例文件已经包含调用上限：

```toml
[agent]
mode = "mock"
protocol = "chat_completions"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
thinking_mode = "disabled"
max_steps = 6
max_tool_calls = 8
max_output_tokens = 1200
timeout_seconds = 60
```

保留 `mode = "mock"`，接下来在管理员页面手动启用。`thinking_mode` 是该服务的可选参数，样例关闭思考模式用于首次接通；换用其他服务时先核对其文档，通常删除该行即可使用默认设置。[官方思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode)

如果平台提示模型名称已改变，先使用其控制台和官方文档给出的名称；不要随意尝试不存在的模型。

## 第四步：启用并验证

1. 双击“启动ShopAgent.cmd”。如果程序已经在运行，不要重复启动。
2. 打开 [本机客服页面](http://127.0.0.1:8000/)。
3. 登录 `admin`，当前本机原型密码为 `demo123`。
4. 点“模块管理”，找到“客服 Agent”，点击“启用真实 Agent”。
5. 页面应显示“真实模型 Agent”。这一步仅证明本地配置完整，实际连接还需要下一步问答。
6. 返回“客服对话”，输入：`给我看看两百元内能降噪的耳机，顺便查下拆封退货政策`。
7. 应看到商品卡片、相关政策及自然语言答复；展开“查看处理记录”，核对成功的商品和知识工具调用。
8. 如果看到认证、额度、格式或超时提示，按《Agent接入与配置.md》的错误表处理，不要把错误当作接通成功。

你也可以在另外一个标签页登录 `alice`，从顾客视角咨询。管理员选择的模型模式对本次运行的所有用户生效。

## 你完成后告诉我什么

只需要告诉我：**“我选了 DeepSeek，本地配置已填好”**，或者描述你看到的错误提示。不要提供密钥。我会协助核对一次真实问答、工具调用和跨用户权限，再继续做商家资料维护与公网部署。

即使真实模型接通，当前商品和政策仍是示例。上线 MVP 还需要真实资料、顾客入口与线上权限配置、HTTPS 部署以及外部手机验收。
