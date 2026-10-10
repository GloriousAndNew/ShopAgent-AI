# 部署包使用说明（先看这里）

这个包是服务器部署用的源码，不是已经上线的网站。你还需要一台 Linux 服务器、域名和 HTTPS 证书。目前先完成本机验收和打包。

## 包里有什么

- `shopagent/`：客服程序和独立业务模块。
- `deploy/`：Linux 服务、Nginx 和环境配置模板。
- `docs/`：学习、操作、部署和验收记录。
- `tests/`、`tools/`：回归测试与部署包验证工具。
- `requirements.lock.txt`：锁定的 Python 依赖版本。
- `RELEASE-MANIFEST.json`：每个文件的大小和 SHA256；外部 SHA256SUMS 文件用于核对下载包。

ZIP 方便在 Windows 查看；tar.gz 用于 Linux。两种格式包含同一组源码。包内 `agent.toml` 从 `mock`（规则演示）开始，不继承本机的付费模型配置。没有密钥、管理员密码、数据库或虚拟环境。

## 没有服务器时

1. 解压 ZIP，打开项目目录。安装 Python 3.12 或 3.13。
2. 在 PowerShell 执行下面的命令，创建这台电脑自己的运行环境：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

3. 双击“初始化正式管理员.cmd”，输入自己的账号和密码；再双击“启动正式ShopAgent.cmd”。打开 `http://127.0.0.1:8001/merchant`。
4. 正式库初始没有商品。按《正式模式初始化与使用指南》录入并审核自己的商品、政策、店铺信息，再测试 `/customer` 和人工回复。

规则模式只用于安装和流程验收；公开试营业前，在服务器本地填写 DeepSeek 密钥和正式模型配置，再核对回复。不要向聊天或 Git 仓库提交密钥。

## 有服务器时

本包采用“单台 Linux + 同机 Nginx + systemd + SQLite”。不要复制 Windows 的 `.venv`，不要开启多个宿主共享数据目录。

1. 在服务器核对下载包：`sha256sum -c ShopAgent-AI-版本-提交.SHA256SUMS.txt`（用实际文件名）。
2. 解压 tar.gz；将项目目录内的源码安装到 `/opt/shopagent`。新安装使用新目录；已有服务先停机备份，不直接覆盖现有数据。
3. 用服务器的 Python 3.12/3.13 创建 `.venv`，安装 `requirements.lock.txt`。`python3 --version` 须落在支持范围内。
4. 按 [公网网关与服务器部署步骤](../docs/公网网关与服务器部署步骤.md) 依次完成专用用户、目录权限、环境文件、交互式管理员初始化、systemd、证书和 Nginx。模板中的 `YOUR_DOMAIN` 必须替换。
5. 检查 `systemd-analyze verify`、`nginx -t`、`/health`；公网仅开放网页 80/443，不开放 8001 和模块端口。实际命令要结合服务器发行版及已有站点，不能直接覆盖现有 Nginx 配置。
6. 开启前按照 [整体验收与部署打包报告](../docs/整体验收与部署打包报告.md) 的外网验收清单操作。

Nginx 模板是完整 `nginx.conf`，PID 位于 `/run/nginx.pid`。Linux 服务和可信公网证书尚需在实际服务器验收。本机通过不表示域名已能访问。

## 开发者复验

在源码目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 tools/build_release.py --output dist/new-build
.\.venv\Scripts\python.exe -X utf8 tools/verify_release.py dist/new-build/实际包名.zip --report test-results/package-smoke.json
```

`verify_release.py` 校验 ZIP 文件并在临时目录创建随机测试管理员、临时商品和两个顾客，验证人工闭环，最后关闭宿主、清理临时数据；不使用真实模型或当前店铺数据库。需要先安装锁定依赖。Linux tar.gz 的文件清单应与 ZIP 一致。

## 更新和回退

更新之前停服务、保存旧源码、按《店铺运营与备份恢复操作指南》备份并校验正式库。新版本先在独立测试目录验收，再安装。出现问题时停新服务；恢复旧源码，并把备份恢复到全新数据目录后修改运行配置。恢复会让旧网页登录凭据失效、默认暂停 AI；重新登录并验收后才恢复服务。不要直接把新格式数据库交给旧版本运行。
