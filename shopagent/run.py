"""本机启动入口，插件由宿主自动管理。"""
import argparse
import os
import socket


def main():
    parser = argparse.ArgumentParser(description="ShopAgent AI 演示 MVP")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument('--environment',choices=['development','production'],default=os.getenv('SHOPAGENT_ENV','development'))
    args = parser.parse_args()
    os.environ['SHOPAGENT_ENV']=args.environment
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", args.port))
        except OSError:
            print(f"端口 {args.port} 已被占用。先停止已有服务，或使用 --port 8001。")
            raise SystemExit(1)
    os.environ["SHOPAGENT_HOST_URL"] = f"http://127.0.0.1:{args.port}"
    print(f"ShopAgent AI：http://127.0.0.1:{args.port}", flush=True)
    print('正式商品咨询模式：需要先初始化管理员；按 Ctrl+C 停止。' if args.environment=='production' else "演示账号 alice / bob / admin，密码均为 demo123。按 Ctrl+C 停止全部插件。", flush=True)
    import uvicorn
    from shopagent.app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info", timeout_graceful_shutdown=30, proxy_headers=False))
    app.state.stop_key = __import__("secrets").token_urlsafe(32)
    app.state.stop_server = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
