# -*- coding: utf-8 -*-
"""临时区（scratch）管理。

背景
----
原先临时区挂在容器 tmpfs 上（`TMPDIR=/tmp/txl`，tmpfs 768M）。实测一份真实文档
302 个 PDF 附件约 352M，打包 zip 时「原件 + 压缩包」同时存在需要约 704M，
再加上历史会话残留 → tmpfs 打满，打包直接 `OSError: [Errno 28] No space left on device`。

现在临时区改为**磁盘支持的目录**（compose 绑定挂载 `/var/scratch`），
并由本模块统一负责：分配会话目录、启动清扫、空间预检、用量统计。

约定：
- 所有会话临时目录都叫 ``txl-*``，一律落在 :func:`root` 下；
- 进程启动时（还没有活会话）可以安全地全量清扫 :func:`sweep_stale`；
- 任何「要写大文件」的动作之前都应先 :func:`ensure_free`，失败给中文原因，
  不要等到底层抛 errno 28。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List

#: 会话临时目录前缀（改动它要同步改 :func:`sweep_stale` 的匹配规则）
SESSION_PREFIX = "txl-"

#: 临时区根目录：优先 SCRATCH_DIR，其次 TMPDIR，最后 /tmp/txl
DEFAULT_ROOT = "/tmp/txl"

#: 低于这个可用空间就不再接受新任务（字节）。留出余量给正在打包的作业。
MIN_FREE_BYTES = 512 * 1024 * 1024

#: 打包/合并阶段的额外开销系数与固定余量（压缩包 ≈ 原件之和，再加点结构开销）
BUNDLE_OVERHEAD_RATIO = 1.15
BUNDLE_OVERHEAD_FIXED = 64 * 1024 * 1024


class ScratchFull(RuntimeError):
    """临时区空间不足。消息是给用户看的中文短句。"""


def root() -> Path:
    """返回临时区根目录（不存在则创建）。"""
    raw = (os.environ.get("SCRATCH_DIR")
           or os.environ.get("TMPDIR")
           or DEFAULT_ROOT)
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_session_dir() -> str:
    """分配一个新的会话临时目录。"""
    return tempfile.mkdtemp(prefix=SESSION_PREFIX, dir=str(root()))


def _session_dirs() -> List[Path]:
    base = root()
    try:
        return [p for p in base.iterdir()
                if p.is_dir() and p.name.startswith(SESSION_PREFIX)]
    except OSError:
        return []


def dir_size(path: os.PathLike) -> int:
    """目录占用的字节数（递归；文件在统计过程中消失不报错）。"""
    total = 0
    stack = [Path(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def sweep_stale() -> int:
    """清空临时区里所有会话目录，返回释放的字节数。

    **只在进程启动时调用**：那一刻不存在任何活会话，全删是安全的。
    """
    freed = 0
    for p in _session_dirs():
        freed += dir_size(p)
        shutil.rmtree(p, ignore_errors=True)
    return freed


def sweep_older_than(seconds: float) -> int:
    """兜底清扫：删掉超过 ``seconds`` 没被改动过的会话目录。

    TTL 回收依赖会话对象还活着；如果进程曾经异常重启、或会话被遗忘在内存外，
    这里按 mtime 兜底，避免残留长期占着空间。
    """
    cutoff = time.time() - seconds
    freed = 0
    for p in _session_dirs():
        try:
            if p.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        freed += dir_size(p)
        shutil.rmtree(p, ignore_errors=True)
    return freed


def free_bytes() -> int:
    """临时区所在文件系统的可用字节数（取不到时返回一个很大的值，避免误判）。"""
    try:
        return shutil.disk_usage(str(root())).free
    except OSError:
        return 1 << 62


def usage() -> Dict[str, object]:
    """给 /api/health 用的临时区快照。"""
    base = root()
    try:
        du = shutil.disk_usage(str(base))
        free, total = du.free, du.total
    except OSError:
        free, total = 0, 0
    sessions = _session_dirs()
    return {
        "root": str(base),
        "free_mb": round(free / 1048576, 1),
        "total_mb": round(total / 1048576, 1),
        "sessions": len(sessions),
        "session_mb": round(sum(dir_size(p) for p in sessions) / 1048576, 1),
    }


def mb(n: float) -> str:
    """字节 → 人类可读（给日志和报错用）。"""
    return f"{n / 1048576:.1f} MB"


def ensure_free(need_bytes: int, what: str) -> None:
    """确认临时区至少有 ``need_bytes`` 可用，否则抛 :class:`ScratchFull`。

    Args:
        need_bytes: 本次操作预计需要的字节数。
        what: 中文动作名，用于拼错误信息（如「打包」「下载」）。
    """
    free = free_bytes()
    if free < max(need_bytes, MIN_FREE_BYTES):
        raise ScratchFull(
            f"临时空间不足，无法{what}：需要约 {mb(need_bytes)}，"
            f"当前可用 {mb(free)}。请减少「数量上限」后重试，或联系管家扩容。"
        )
