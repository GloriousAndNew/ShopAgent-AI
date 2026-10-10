"""仅通过当前宿主的随机停止凭据请求退出，不按进程名批量杀进程。"""
import json
import time
import httpx
from shopagent.settings import DATA


def main():
    record = DATA / "host.json"
    if not record.exists():
        print("没有发现本项目启动记录；服务可能已停止。")
        return
    host = json.loads(record.read_text(encoding="utf-8"))
    with httpx.Client(trust_env=False, timeout=3) as client:
        try:
            response = client.post(host["url"] + "/internal/shutdown", headers={"X-Stop-Key": host["stop_key"]})
            response.raise_for_status()
        except httpx.HTTPError:
            print("无法连接记录中的宿主，未停止任何其他进程；请检查启动窗口。")
            raise SystemExit(1)
        for _ in range(100):
            if not record.exists():
                print("ShopAgent 及其插件已停止。")
                return
            time.sleep(.2)
    print("已请求停止，仍在等待当前任务结束；请查看启动窗口。")


if __name__ == "__main__":
    main()
