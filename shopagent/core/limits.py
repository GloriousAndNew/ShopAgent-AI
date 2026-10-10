"""单宿主进程的滑动窗口限流；不信任客户端提供的代理IP。"""
import time
from collections import deque, OrderedDict
from shopagent.contracts.models import BusinessError


class RateLimiter:
    def __init__(self, max_keys=10000):
        self.buckets = OrderedDict()
        self.max_keys = max_keys

    def check(self, key, count, seconds):
        instant = time.monotonic()
        bucket = self.buckets.get(key)
        if bucket is None:
            if len(self.buckets) >= self.max_keys:
                # 只回收已过期的窗口，不能通过制造新身份清空他人限额。
                for old_key, (old_seconds, values) in list(self.buckets.items()):
                    if not values or values[-1] <= instant-old_seconds:
                        self.buckets.pop(old_key)
                if len(self.buckets) >= self.max_keys:
                    raise BusinessError('RATE_LIMITED', '当前请求较多，请稍后重试。')
            bucket = (seconds, deque())
            self.buckets[key] = bucket
        values = bucket[1]
        while values and values[0] <= instant-seconds:
            values.popleft()
        if len(values) >= count:
            raise BusinessError('RATE_LIMITED', '操作太频繁，请稍后重试。')
        values.append(instant)
