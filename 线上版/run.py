# -*- coding: utf-8 -*-
"""
线上版启动入口。

重要：会话状态保存在进程内存里，**必须单 worker**（uvicorn 默认即单 worker）。
后台线程负责定期回收过期会话与残留临时目录。
"""

import os
import sys
import threading
import time

# 让容器内也能 `import models/utils/processors/config`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import scratch  # noqa: E402
from app.api import app  # noqa: E402
from app.session import store  # noqa: E402

#: 后台清理线程的间隔（秒）
REAP_INTERVAL = 60
#: 兜底清扫阈值：超过这个时间没被动过的会话目录一律删（比 TTL 宽松，防进程异常重启后的残留）
STALE_SECONDS = 6 * 60 * 60


def _reaper():
    """后台清理线程：定期回收过期会话，并兜底清扫临时区残留目录。"""
    while True:
        time.sleep(REAP_INTERVAL)
        try:
            store.reap()
        except Exception:
            pass
        try:
            scratch.sweep_older_than(STALE_SECONDS)
        except Exception:
            pass


def main():
    import uvicorn
    # 进程刚起，没有任何活会话，把上次跑剩的临时目录全清掉
    try:
        freed = scratch.sweep_stale()
        print(f"[scratch] root={scratch.root()} 启动清扫释放 {scratch.mb(freed)}", flush=True)
    except Exception as exc:                       # noqa: BLE001 - 清扫失败不该挡住启动
        print(f"[scratch] 启动清扫失败：{exc}", flush=True)
    threading.Thread(target=_reaper, daemon=True).start()
    uvicorn.run(
        "app.api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8788")),
        workers=1,
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
