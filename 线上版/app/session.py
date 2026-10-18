# -*- coding: utf-8 -*-
"""
会话内存仓。

设计约束（用户已拍板）：**关页后数据不留存**。
- 全部状态只存在进程内存里，服务端不落库、不写用户数据到磁盘；
- 会话带 TTL，过期即从内存删除；
- 只有导出用的临时文件会短暂落到 /tmp 下的会话目录，下载完成后立即清理。

因此 uvicorn 必须单 worker（多 worker 时内存不共享）。
"""

import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.contact import ContactManager

from app import scratch

# 默认会话空闲存活时间（秒）。关页只是结束使用，服务端到点自然回收。
# 30 分钟够用：真要用会一直轮询/操作，updated_at 一直在刷新。
DEFAULT_TTL_SECONDS = 30 * 60

# 单个会话最多允许存在的会话数，防止内存被刷爆
MAX_SESSIONS = 64

# 临时区可用空间低于这个值时，主动回收最旧会话（与 scratch.MIN_FREE_BYTES 同量级）。
# 这是一道自保：不管是谁把盘写满了，都不至于让新任务直接 ENOSPC。
MIN_FREE_FLOOR = 512 * 1024 * 1024


@dataclass
class Session:
    """一次浏览器会话的完整内存状态。"""

    sid: str
    created_at: float
    updated_at: float
    manager: ContactManager = field(default_factory=ContactManager)
    # 导入日志（文件级），用于前端展示每一步做了什么
    log: List[Dict] = field(default_factory=list)
    # 导出用的临时目录；会话过期时随之删除
    workdir: str = field(default_factory=scratch.new_session_dir)
    # 证书下载作业的内存状态（CertJob 实例等）；不 import certdown，保持解耦
    cert: Dict[str, Any] = field(default_factory=dict)
    # 简单访问令牌：前端首帧领取，后续请求带上，避免会话被随意串号
    token: str = field(default_factory=lambda: uuid.uuid4().hex)

    def touch(self) -> None:
        self.updated_at = time.time()

    def add_log(self, kind: str, message: str, **extra) -> None:
        entry = {"kind": kind, "message": message, "ts": time.time()}
        entry.update(extra)
        self.log.append(entry)


class SessionStore:
    """线程安全的进程内会话仓。"""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, max_sessions: int = MAX_SESSIONS):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def create(self) -> Session:
        """新建一个空会话。超过上限时先回收最旧会话。"""
        with self._lock:
            self._reap_locked()
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
                self._drop_locked(oldest.sid)
            self._enforce_space_locked()
            now = time.time()
            sid = uuid.uuid4().hex
            session = Session(sid=sid, created_at=now, updated_at=now)
            self._sessions[sid] = session
            return session

    def get(self, sid: str, token: Optional[str] = None) -> Optional[Session]:
        """按 id 取会话；带 token 时校验令牌。"""
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if time.time() - session.updated_at > self.ttl_seconds:
                self._drop_locked(sid)
                return None
            if token is not None and token != session.token:
                return None
            session.touch()
            return session

    def drop(self, sid: str) -> None:
        """主动结束会话并清理磁盘暂存。"""
        with self._lock:
            self._drop_locked(sid)

    def _drop_locked(self, sid: str) -> None:
        session = self._sessions.pop(sid, None)
        if session is not None:
            shutil.rmtree(session.workdir, ignore_errors=True)

    def _reap_locked(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items()
                   if now - s.updated_at > self.ttl_seconds]
        for sid in expired:
            self._drop_locked(sid)

    def _enforce_space_locked(self) -> None:
        """临时区可用空间不足时，按最久未使用顺序回收会话，直到腾出空间。

        防的是「上一个用户的大文件把盘占满，下一个人一点开始就 ENOSPC」。
        """
        while self._sessions and scratch.free_bytes() < MIN_FREE_FLOOR:
            oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
            self._drop_locked(oldest.sid)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def reap(self) -> int:
        """回收所有过期会话，返回回收数量（背景线程调用）。"""
        with self._lock:
            before = len(self._sessions)
            self._reap_locked()
            return before - len(self._sessions)


# 进程级单例
store = SessionStore()
